#!/usr/bin/env python3
"""
保險知識庫自動爬取主程式
用法:
  python3 run.py              # 全量爬取
  python3 run.py --http-only  # 只跑 HTTP 來源（快速測試）
  python3 run.py --rss-only   # 只跑 RSS
  python3 run.py --source air_news  # 單一來源測試
  python3 run.py --dry-run    # 只爬取不處理不推送
  python3 run.py --batch      # 批次 AI 處理（省 token）
"""

import argparse
import json
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

# Telegram 設定 (insurance-kb 專用)
os.environ.setdefault("TG_BOT_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
os.environ.setdefault("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", ""))

from src.sources import SOURCES, get_sources_by_method
from src.crawler import crawl_source, Deduplicator, CrawlResult
from src.ai_processor import process_article, process_batch
from src.md_generator import generate_md, update_index, git_commit_push
from src.health_report import generate_health_report, ai_gap_scan, send_telegram_report

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


def main():
    parser = argparse.ArgumentParser(description="保險知識庫自動爬取")
    parser.add_argument("--http-only", action="store_true", help="只跑 HTTP 來源")
    parser.add_argument("--rss-only", action="store_true", help="只跑 RSS 來源")
    parser.add_argument("--playwright-only", action="store_true", help="只跑 Playwright 來源")
    parser.add_argument("--source", type=str, help="單一來源 ID")
    parser.add_argument("--dry-run", action="store_true", help="只爬取，不做 AI 處理和推送")
    parser.add_argument("--no-push", action="store_true", help="不推送到 GitHub")
    parser.add_argument("--no-ai", action="store_true", help="跳過 AI 處理")
    parser.add_argument("--batch", action="store_true", help="批次 AI 處理（省 token）")
    parser.add_argument("--batch-size", type=int, default=5, help="批次大小（預設 5）")
    parser.add_argument("--limit", type=int, default=0, help="每個來源最多處理 N 篇")
    args = parser.parse_args()

    start_time = time.time()
    logger.info("=" * 60)
    logger.info(f"開始爬取 {datetime.now(TZ_UTC8).strftime('%Y-%m-%d %H:%M')}")

    # 決定要爬哪些來源
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

    # 去重器
    dedup = Deduplicator(ROOT / "data" / "seen.json")

    # Phase 1: 爬取
    all_health = []
    all_new_articles = defaultdict(list)
    total_new = 0
    total_dup = 0

    for i, source in enumerate(sources, 1):
        sid = source["id"]
        logger.info(f"[{i}/{len(sources)}] 爬取 {sid} ({source['method']})...")

        results, health = crawl_source(source)
        all_health.append(health)

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

    if args.dry_run:
        _print_summary(all_new_articles, all_health)
        return

    # Phase 2: AI 處理
    processed_articles = []
    if not args.no_ai and total_new > 0:
        # 收集所有文章到一個 flat list
        flat_articles = []
        for sid, articles in all_new_articles.items():
            flat_articles.extend(articles)

        if args.batch:
            processed_articles = _ai_batch_mode(flat_articles, args.batch_size)
        else:
            processed_articles = _ai_single_mode(flat_articles)

        logger.info(f"AI 處理完成: {len(processed_articles)} 篇")
    else:
        for articles in all_new_articles.values():
            for a in articles:
                a["ai"] = a.get("ai", {
                    "title_zh": a["crawl"]["title"],
                    "region": a["source"].get("region", ""),
                })
                processed_articles.append(a)

    # Phase 3: 生成 MD + 更新索引
    if processed_articles:
        logger.info("生成 Markdown...")
        for article in processed_articles:
            note_path = generate_md(article)
            article["note_path"] = note_path

        new_count = update_index(processed_articles)
        logger.info(f"索引更新: {new_count} 新增")

    # Phase 4: Git push
    if not args.no_push and processed_articles:
        git_commit_push()

    # Phase 5: 健康度報告 + AI 補漏
    report = generate_health_report(all_health, all_new_articles)
    gap_result = None
    if not args.no_ai and total_new > 0:
        gap_result = ai_gap_scan(all_new_articles, all_health)

    # Phase 6: Telegram 報告
    send_telegram_report(report, gap_result)

    elapsed = time.time() - start_time
    logger.info(f"全部完成! 耗時 {elapsed:.0f} 秒")


def _ai_single_mode(articles):
    """逐篇 AI 處理，自動 fallback 到其他模型"""
    logger.info(f"AI 單篇模式: {len(articles)} 篇待處理")
    processed = []
    for i, article in enumerate(articles, 1):
        crawl = article["crawl"]
        source = article["source"]
        ai_result = process_article(
            title=crawl["title"],
            snippet=crawl.get("snippet", ""),
            source_name=source["name"],
            source_region=source.get("region", ""),
        )
        article["ai"] = ai_result
        processed.append(article)

        if i % 10 == 0:
            logger.info(f"  AI 進度: {i}/{len(articles)}")
        time.sleep(2)  # Rate limit buffer

    return processed


def _ai_batch_mode(articles, batch_size=5):
    """批次 AI 處理，每次送 batch_size 篇，大幅省 token"""
    logger.info(f"AI 批次模式: {len(articles)} 篇, batch_size={batch_size}")

    batch_input = []
    for article in articles:
        crawl = article["crawl"]
        source = article["source"]
        batch_input.append({
            "title": crawl["title"],
            "snippet": crawl.get("snippet", ""),
            "source_name": source["name"],
            "source_region": source.get("region", ""),
        })

    results = process_batch(batch_input, batch_size=batch_size)

    processed = []
    for article, ai_result in zip(articles, results):
        article["ai"] = ai_result
        processed.append(article)

    return processed


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
