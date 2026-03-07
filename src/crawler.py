"""
保險知識庫爬蟲引擎 — 三層爬取機制
Layer A: HTTP 直接抓取 (requests + BeautifulSoup)
Layer B: Playwright 瀏覽器渲染
Layer C: RSS Feed 解析
"""

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent.parent / "logs" / "crawler.log"),
    ],
)
logger = logging.getLogger("crawler")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 15
TZ_UTC8 = timezone(timedelta(hours=8))


class CrawlResult:
    """單篇抓取結果"""

    def __init__(self, source_id, title, url, snippet="", published=None):
        self.source_id = source_id
        self.title = title.strip() if title else ""
        self.url = url.strip() if url else ""
        self.snippet = snippet.strip() if snippet else ""
        self.published = published
        self.crawled_at = datetime.now(TZ_UTC8).isoformat()
        self.uid = hashlib.md5(self.url.encode()).hexdigest()[:12]

    def to_dict(self):
        return {
            "uid": self.uid,
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "published": self.published,
            "crawled_at": self.crawled_at,
        }

    def is_valid(self):
        return bool(self.title) and bool(self.url) and len(self.title) > 5


class Deduplicator:
    """基於 URL hash 的去重器，使用本地 JSON 檔案"""

    def __init__(self, path):
        self.path = Path(path)
        self.seen = set()
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.seen = set(data.get("seen_uids", []))
            except (json.JSONDecodeError, KeyError):
                self.seen = set()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"seen_uids": list(self.seen)}, indent=2))

    def is_new(self, result):
        return result.uid not in self.seen

    def mark_seen(self, result):
        self.seen.add(result.uid)


# ===== Layer A: HTTP =====

def crawl_http(source):
    """HTTP 直接抓取"""
    source_id = source["id"]
    url = source["url"]
    selectors = source.get("selectors", {})
    keywords_filter = source.get("keywords", [])

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"[{source_id}] HTTP failed: {e}")
        return [], {"status": "error", "error": str(e)}

    soup = BeautifulSoup(resp.text, "lxml")
    results = _extract_from_soup(soup, source_id, url, selectors, keywords_filter)
    logger.info(f"[{source_id}] HTTP: found {len(results)} items")
    return results, {"status": "ok", "count": len(results)}


# ===== Layer B: Playwright =====

def crawl_playwright(source):
    """Playwright 瀏覽器渲染抓取"""
    source_id = source["id"]
    url = source["url"]
    selectors = source.get("selectors", {})
    keywords_filter = source.get("keywords", [])

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(f"[{source_id}] Playwright not installed")
        return [], {"status": "error", "error": "playwright not installed"}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=USER_AGENT)
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()
    except Exception as e:
        logger.error(f"[{source_id}] Playwright failed: {e}")
        return [], {"status": "error", "error": str(e)}

    soup = BeautifulSoup(html, "lxml")
    results = _extract_from_soup(soup, source_id, url, selectors, keywords_filter)
    logger.info(f"[{source_id}] Playwright: found {len(results)} items")
    return results, {"status": "ok", "count": len(results)}


# ===== Layer C: RSS =====

def crawl_rss(source):
    """RSS Feed 解析"""
    source_id = source["id"]
    url = source["url"]

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error(f"[{source_id}] RSS failed: {e}")
        return [], {"status": "error", "error": str(e)}

    results = []
    for entry in feed.entries[:50]:
        title = entry.get("title", "")
        link = entry.get("link", "")
        snippet = entry.get("summary", entry.get("description", ""))
        snippet = BeautifulSoup(snippet, "lxml").get_text()[:300] if snippet else ""
        published = entry.get("published", entry.get("updated", ""))

        r = CrawlResult(source_id, title, link, snippet, published)
        if r.is_valid():
            results.append(r)

    logger.info(f"[{source_id}] RSS: found {len(results)} items")
    return results, {"status": "ok", "count": len(results)}


# ===== 共用解析 =====

def _extract_from_soup(soup, source_id, base_url, selectors, keywords_filter=None):
    """從 BeautifulSoup 解析結果中提取文章列表"""
    results = []
    list_sel = selectors.get("list", "article, .news-item, .card")
    title_sel = selectors.get("title", "h2, h3, a")
    link_sel = selectors.get("link", "a@href")

    # 嘗試多種選擇器策略
    items = soup.select(list_sel)

    if not items:
        # 退回到抓所有連結
        items = soup.find_all("a", href=True)
        for a in items[:100]:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or len(title) < 8:
                continue
            full_url = urljoin(base_url, href)
            if _is_article_url(full_url, base_url):
                r = CrawlResult(source_id, title, full_url)
                if r.is_valid() and _matches_keywords(r, keywords_filter):
                    results.append(r)
        return results[:30]

    for item in items[:50]:
        title_el = item.select_one(title_sel) if isinstance(item, type(soup)) else item
        title = title_el.get_text(strip=True) if title_el else ""

        # 提取連結
        link_tag, link_attr = link_sel.split("@") if "@" in link_sel else ("a", "href")
        link_el = item.select_one(link_tag) if isinstance(item, type(soup)) else item
        href = ""
        if link_el:
            href = link_el.get(link_attr, "")
        if not href and item.name == "a":
            href = item.get("href", "")

        if not href:
            continue

        full_url = urljoin(base_url, href)
        snippet = item.get_text(strip=True)[:200] if item else ""

        r = CrawlResult(source_id, title or snippet[:80], full_url, snippet)
        if r.is_valid() and _matches_keywords(r, keywords_filter):
            results.append(r)

    return results[:30]


def _is_article_url(url, base_url):
    """判斷是否為文章連結（排除導航、分類頁等）"""
    parsed = urlparse(url)
    path = parsed.path.lower()
    skip_patterns = [
        "/tag/", "/category/", "/search", "/login", "/register",
        "/sitemap", "/privacy", "/terms", "/contact", "/about",
        ".css", ".js", ".png", ".jpg", ".pdf",
    ]
    return (
        parsed.netloc in urlparse(base_url).netloc or parsed.netloc == ""
    ) and not any(p in path for p in skip_patterns)


def _matches_keywords(result, keywords):
    """關鍵字過濾（如有設定）"""
    if not keywords:
        return True
    text = (result.title + " " + result.snippet).lower()
    return any(kw.lower() in text for kw in keywords)


# ===== 主入口 =====

DISPATCH = {
    "http": crawl_http,
    "playwright": crawl_playwright,
    "rss": crawl_rss,
}


def crawl_source(source):
    """爬取單一來源"""
    method = source.get("method", "http")
    fn = DISPATCH.get(method)
    if not fn:
        return [], {"status": "error", "error": f"unknown method: {method}"}

    start = time.time()
    results, health = fn(source)
    health["duration_ms"] = int((time.time() - start) * 1000)
    health["source_id"] = source["id"]
    health["method"] = method
    health["timestamp"] = datetime.now(TZ_UTC8).isoformat()
    return results, health
