"""
截圖爬蟲 fallback 模組
策略:
1. Playwright 渲染頁面 → 提取 body text → 正則解析新聞標題
2. 如失敗，嘗試 Gemini CLI Vision（需足夠記憶體）
適用於 JavaScript 動態渲染、反爬蟲嚴格的網站（如 Swiss Re）
"""

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin

logger = logging.getLogger("screenshot_crawler")


def crawl_with_screenshot(source):
    """多層 fallback 截圖爬蟲"""
    source_id = source["id"]
    url = source["url"]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(f"[{source_id}] Playwright not installed")
        return [], {"status": "error", "error": "playwright not installed"}

    # Step 1: Playwright 渲染 → 提取 body text + links
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": 1024, "height": 1600},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(8000)

            body_text = page.inner_text("body")

            # 嘗試用 JS 直接提取連結
            links_data = page.evaluate("""() => {
                const links = [];
                document.querySelectorAll('a[href]').forEach(el => {
                    const text = el.innerText.trim();
                    const href = el.href;
                    if (text.length > 10 && text.length < 300) {
                        links.push({text, href});
                    }
                });
                return links;
            }""")

            browser.close()
    except Exception as e:
        logger.error(f"[{source_id}] Playwright failed: {e}")
        return [], {"status": "error", "error": f"playwright: {e}"}

    try:
        from src.crawler import CrawlResult
    except ImportError:
        from crawler import CrawlResult

    results = []
    seen_titles = set()

    # 方法 A: 從 body text 用正則提取 "Read More about: {title}" 模式（最可靠）
    if body_text:
        pattern = re.compile(r"Read More about:\s*(.+?)(?:\n|$)")
        matches = pattern.findall(body_text)
        for title in matches:
            title = title.strip()
            if title and len(title) > 10 and title not in seen_titles:
                seen_titles.add(title)
                r = CrawlResult(source_id=source_id, title=title, url=url)
                results.append(r)

    # 方法 B: 從 JS links 提取（過濾導航，只保留新聞路徑）
    if not results and links_data:
        news_path_keywords = ["/news", "/press", "/media", "/release", "/article", "/insight"]
        for link in links_data:
            title = link.get("text", "").strip()
            href = link.get("href", "")

            if not title or not href or len(title) < 20:
                continue
            if title in seen_titles:
                continue
            if title.startswith("Read More about:"):
                continue
            # 只保留看起來像新聞的路徑
            href_lower = href.lower()
            if not any(kw in href_lower for kw in news_path_keywords):
                continue

            seen_titles.add(title)
            full_url = urljoin(url, href)
            r = CrawlResult(source_id=source_id, title=title, url=full_url)
            results.append(r)

    # 方法 C: 從 body text 提取連續的標題+日期模式
    if not results and body_text:
        # Swiss Re 格式: "City\nTitle\nDD Mon YYYY"
        date_pattern = re.compile(r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}")
        lines = [l.strip() for l in body_text.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if date_pattern.search(line) and i >= 2:
                # 往回找標題（跳過城市名）
                candidate = lines[i - 1]
                if len(candidate) > 20 and candidate not in seen_titles:
                    seen_titles.add(candidate)
                    r = CrawlResult(source_id=source_id, title=candidate, url=url)
                    results.append(r)

    results = results[:30]
    if results:
        logger.info(f"[{source_id}] Extracted {len(results)} articles from rendered page")
        return results, {"status": "ok", "count": len(results), "method": "playwright_text"}
    else:
        logger.warning(f"[{source_id}] No articles extracted from rendered page")
        return [], {"status": "error", "error": "no_articles_extracted"}
