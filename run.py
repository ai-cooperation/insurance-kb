#!/usr/bin/env python3
"""
保險知識庫自動爬取主程式 v4
新增：三層驗證機制 + 截圖爬蟲 fallback + Gemini 補漏
"""

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 設定環境
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# 載入 .env
env_path = Path("/home/ac-macmini2/world-monitor/.env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# Telegram 設定
os.environ.setdefault("TG_BOT_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
os.environ.setdefault("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", ""))

from src.sources import SOURCES, get_sources_by_method
from src.crawler import crawl_source, Deduplicator
from src.ai_processor import process_article, get_interval
from src.md_generator import generate_md, update_index, git_commit_push
from src.health_report import generate_health_report, send_telegram_report
from src.verifier import verify_batch, audit_completeness, gemini_gap_scan
from src.screenshot_crawler import crawl_with_screenshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "logs" / "run.log"),
    ],
)
logger = logging.getLogger("main")
TZ_UTC8 = timezone(timedelta(hours=8))

# 截圖 fallback 候選：當 Playwright/HTTP 爬取失敗時用截圖+Vision
SCREENSHOT_FALLBACK_SOURCES = {"swissre_media"}


def main():
    parser = argparse.ArgumentParser(description="保險知識庫自動爬取")
    parser.add_argument("--http-only", action="store_true")
    parser.add_argument("--rss-only", action="store_true")
    parser.add_argument("--playwright-only", action="store_true")
    parser.add_argument("--source", type=str)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--no-verify", action="store_true", help="跳過驗證層")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    start_time = time.time()
    logger.info("=" * 60)
    logger.info(f"開始爬取 {datetime.now(TZ_UTC8).strftime('%Y-%m-%d %H:%M')}")

    # 決定來源
    if args.source:
        sources = [s for s in SOURCES if s["id"] == args.source]
        if not sources:
            logger.error(f"Source not found: {args.source}")
            sys.exit(1)
    elif args.http_only:
        sources = get_sources_by_method("http")
    elif args.rss_only:
        sources = get_sources_by_method("rss")
    elif args.playwright_only:
        sources = get_sources_by_method("playwright")
    else:
        sources = SOURCES

    logger.info(f"來源數: {len(sources)} (http={len([s for s in sources if s['method']=='http'])},"
                f" playwright={len([s for s in sources if s['method']=='playwright'])},"
                f" rss={len([s for s in sources if s['method']=='rss'])})")

    dedup = Deduplicator(ROOT / "data" / "seen.json")

    # ===== Phase 1: 爬取（Layer 1）=====
    all_health = []
    all_new_articles = defaultdict(list)
    total_new = 0
    total_dup = 0
    screenshot_fallback_used = []

    for i, source in enumerate(sources, 1):
        sid = source["id"]
        logger.info(f"[{i}/{len(sources)}] 爬取 {sid} ({source['method']})...")

        results, health = crawl_source(source)
        all_health.append(health)

        # 截圖 fallback：爬取失敗或結果為 0 時嘗試截圖+Vision
        if (health.get("status") != "ok" or health.get("count", 0) == 0) and sid in SCREENSHOT_FALLBACK_SOURCES:
            logger.info(f"  嘗試截圖 fallback: {sid}")
            ss_results, ss_health = crawl_with_screenshot(source)
            if ss_results:
                results = ss_results
                health = ss_health
                all_health[-1] = health
                screenshot_fallback_used.append(sid)
                logger.info(f"  截圖 fallback 成功: {len(ss_results)} items")

        new_items = []
        for r in results:
            if dedup.is_new(r):
                new_items.append(r)
                dedup.mark_seen(r)
            else:
                total_dup += 1

        if args.limit and len(new_items) > args.limit:
            new_items = new_items[:args.limit]

        for r in new_items:
            article = {
                "crawl": r.to_dict(),
                "source": {
                    "id": source["id"],
                    "name": source["name"],
                    "region": source.get("region", ""),
                    "type": source.get("type", ""),
                },
            }
            all_new_articles[sid].append(article)
            total_new += 1

        if health.get("status") == "ok":
            logger.info(f"  ✅ {health.get('count', 0)} found, {len(new_items)} new")
        else:
            logger.warning(f"  ❌ {health.get('error', 'unknown')}")

    dedup.save()
    logger.info(f"爬取完成: {total_new} 新文章, {total_dup} 重複")
    if screenshot_fallback_used:
        logger.info(f"截圖 fallback 使用: {screenshot_fallback_used}")

    if args.dry_run:
        _print_summary(all_new_articles, all_health)
        return

    # ===== Phase 2: AI 處理 =====
    processed_articles = []
    if not args.no_ai and total_new > 0:
        flat_articles = []
        for articles in all_new_articles.values():
            flat_articles.extend(articles)

        logger.info(f"AI 處理: {len(flat_articles)} 篇（單篇模式，動態間隔）")
        for i, article in enumerate(flat_articles, 1):
            crawl = article["crawl"]
            source = article["source"]
            ai_result = process_article(
                title=crawl["title"],
                snippet=crawl.get("snippet", ""),
                source_name=source["name"],
                source_region=source.get("region", ""),
            )
            article["ai"] = ai_result
            processed_articles.append(article)

            if i % 20 == 0:
                logger.info(f"  AI 進度: {i}/{len(flat_articles)}")

            interval = get_interval()
            time.sleep(interval)

        logger.info(f"AI 處理完成: {len(processed_articles)} 篇")
    else:
        for articles in all_new_articles.values():
            for a in articles:
                a["ai"] = a.get("ai", {
                    "title_zh": a["crawl"]["title"],
                    "region": a["source"].get("region", ""),
                })
                processed_articles.append(a)

    # ===== Phase 3: 三層驗證（Layer 2 + 3）=====
    verification_stats = None
    audit_result = None
    gap_scan = None

    if not args.no_verify and processed_articles:
        # Layer 2: AI 輸出驗證
        logger.info("Layer 2: AI 輸出驗證...")
        verification_stats = verify_batch(processed_articles)

        # Layer 3: 完整性審計
        logger.info("Layer 3: 完整性審計...")
        audit_result = audit_completeness(processed_articles, all_health)

        # Gemini 智慧補漏（取代 Groq API）
        if not args.no_ai:
            logger.info("Gemini 補漏掃描...")
            gap_scan = gemini_gap_scan(processed_articles)
            if gap_scan:
                logger.info(f"  覆蓋率: {gap_scan.get('coverage_score', '?')}%")
                for gap in gap_scan.get("gaps", [])[:3]:
                    logger.info(f"  缺口: {gap}")

    # ===== Phase 4: 生成 MD + 更新索引 =====
    if processed_articles:
        logger.info("生成 Markdown...")
        for article in processed_articles:
            note_path = generate_md(article)
            article["note_path"] = note_path

        new_count = update_index(processed_articles)
        logger.info(f"索引更新: {new_count} 新增")

    # ===== Phase 5: Build site + Git push =====
    if processed_articles:
        from build_site import build_site
        build_site()

    if not args.no_push and processed_articles:
        git_commit_push()
        # 等待 GitHub Pages 部署完成再發通知
        _wait_for_pages_deploy()

    # ===== Phase 6: 健康度報告 + Telegram =====
    report = generate_health_report(all_health, all_new_articles)

    # 合併驗證結果到報告
    if verification_stats:
        report["verification"] = {
            "valid": verification_stats["valid"],
            "fixed": verification_stats["fixed"],
            "invalid": verification_stats["invalid"],
        }
    if audit_result:
        report["audit_coverage"] = audit_result.get("coverage_score", 0)
        report["audit_gaps"] = audit_result.get("gaps", [])

    send_telegram_report(report, gap_scan)

    elapsed = time.time() - start_time
    logger.info(f"全部完成! 耗時 {elapsed:.0f} 秒")


def _wait_for_pages_deploy(max_wait=120, interval=15):
    """等待 GitHub Pages 部署完成，確認頁面可存取"""
    import requests as req
    pages_url = "https://cooperation.tw/insurance-kb/"
    logger.info(f"等待 GitHub Pages 部署... (最多 {max_wait}s)")
    waited = 0
    while waited < max_wait:
        time.sleep(interval)
        waited += interval
        try:
            resp = req.get(pages_url, timeout=10)
            if resp.status_code == 200:
                logger.info(f"Pages 部署確認 ({waited}s)")
                return True
        except Exception:
            pass
        logger.info(f"  Pages 尚未就緒... ({waited}s)")
    logger.warning(f"Pages 部署等待超時 ({max_wait}s)，仍發送通知")
    return False


def _print_summary(all_articles, all_health):
    """dry-run 模式下的摘要輸出"""
    print("\n" + "=" * 60)
    print("DRY RUN SUMMARY")
    print("=" * 60)

    total = sum(len(articles) for articles in all_articles.values())
    ok = sum(1 for h in all_health if h.get("status") == "ok")
    fail = sum(1 for h in all_health if h.get("status") != "ok")

    print(f"來源: {ok} 成功, {fail} 失敗")
    print(f"新文章: {total}")
    print()

    for sid, articles in all_articles.items():
        if articles:
            print(f"[{sid}] ({len(articles)} articles)")
            for a in articles[:3]:
                title = a["crawl"]["title"][:60]
                print(f"  - {title}")
            if len(articles) > 3:
                print(f"  ... and {len(articles)-3} more")

    print()
    if fail > 0:
        print("FAILED SOURCES:")
        for h in all_health:
            if h.get("status") != "ok":
                print(f"  ❌ {h.get('source_id')}: {h.get('error', '')[:60]}")


if __name__ == "__main__":
    main()
