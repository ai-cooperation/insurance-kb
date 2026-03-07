"""
Markdown 筆記生成 + Git 自動提交
"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("md_generator")

REPO_DIR = Path(__file__).parent.parent
NOTES_DIR = REPO_DIR / "notes"
INDEX_DIR = REPO_DIR / "index"
TZ_UTC8 = timezone(timedelta(hours=8))


def generate_md(article):
    """從已處理的文章資料生成 Markdown 檔案"""
    now = datetime.now(TZ_UTC8)
    date_str = now.strftime("%Y-%m-%d")
    year_month = now.strftime("%Y/%m")

    out_dir = NOTES_DIR / year_month
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = article.get("ai", {})
    source = article.get("source", {})
    crawl = article.get("crawl", {})

    safe_title = re.sub(r'[^\w\u4e00-\u9fff-]', '_', meta.get("title_zh", crawl.get("title", "untitled")))[:60]
    filename = f"{date_str}_{safe_title}.md"
    filepath = out_dir / filename

    md_content = f"""---
id: "{crawl.get('uid', '')}"
title: "{_escape_yaml(meta.get('title_zh', crawl.get('title', '')))}"
date: "{date_str}"
source: "{_escape_yaml(source.get('name', ''))}"
source_url: "{crawl.get('url', '')}"
category: "{meta.get('category', '')}"
subcategory: "{meta.get('subcategory', '')}"
region: "{meta.get('region', source.get('region', ''))}"
companies: {json.dumps(meta.get('companies', []), ensure_ascii=False)}
keywords: {json.dumps(meta.get('keywords', []), ensure_ascii=False)}
importance: "{meta.get('importance', '低')}"
source_type: "{source.get('type', '')}"
collected_at: "{crawl.get('crawled_at', now.isoformat())}"
collector: "auto-crawler"
---

# {meta.get('title_zh', crawl.get('title', ''))}

## 摘要

{meta.get('summary_zh', crawl.get('snippet', '（無摘要）'))}

## 基本資訊

| 欄位 | 內容 |
|------|------|
| 來源 | {source.get('name', '')} |
| 地區 | {meta.get('region', '')} |
| 分類 | {meta.get('category', '')} / {meta.get('subcategory', '')} |
| 重要程度 | {meta.get('importance', '')} |
| 相關公司 | {', '.join(meta.get('companies', [])) or '—'} |

## 關鍵字

{' '.join(['`' + kw + '`' for kw in meta.get('keywords', [])])}

## 原文連結

[查看原文]({crawl.get('url', '')})

---
*自動收集於 {now.strftime('%Y-%m-%d %H:%M')} | 來源：{source.get('name', '')} | 分類：{meta.get('category', '')}/{meta.get('subcategory', '')}*
"""

    filepath.write_text(md_content, encoding="utf-8")
    logger.info(f"MD generated: {filepath.relative_to(REPO_DIR)}")
    return str(filepath.relative_to(REPO_DIR))


def update_index(all_articles):
    """更新 JSON 索引檔"""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    master_path = INDEX_DIR / "master-index.json"
    existing = []
    if master_path.exists():
        try:
            existing = json.loads(master_path.read_text())
        except json.JSONDecodeError:
            existing = []

    existing_uids = {a["uid"] for a in existing}
    new_entries = []

    for article in all_articles:
        crawl = article.get("crawl", {})
        meta = article.get("ai", {})
        source = article.get("source", {})
        uid = crawl.get("uid", "")

        if uid in existing_uids:
            continue

        entry = {
            "uid": uid,
            "title": meta.get("title_zh", crawl.get("title", "")),
            "date": datetime.now(TZ_UTC8).strftime("%Y-%m-%d"),
            "source": source.get("name", ""),
            "source_url": crawl.get("url", ""),
            "category": meta.get("category", ""),
            "subcategory": meta.get("subcategory", ""),
            "region": meta.get("region", ""),
            "companies": meta.get("companies", []),
            "keywords": meta.get("keywords", []),
            "importance": meta.get("importance", ""),
            "summary": meta.get("summary_zh", ""),
            "note_path": article.get("note_path", ""),
        }
        new_entries.append(entry)

    combined = new_entries + existing
    master_path.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"Index updated: {len(new_entries)} new, {len(combined)} total")
    return len(new_entries)


def git_commit_push():
    """自動 commit 並 push 到 GitHub"""
    try:
        os.chdir(REPO_DIR)
        subprocess.run(["git", "add", "notes/", "index/", "docs/"], check=True, capture_output=True)

        result = subprocess.run(
            ["git", "diff", "--staged", "--quiet"], capture_output=True
        )
        if result.returncode == 0:
            logger.info("No changes to commit")
            return False

        now = datetime.now(TZ_UTC8).strftime("%Y-%m-%d %H:%M")
        subprocess.run(
            ["git", "commit", "-m", f"chore: auto-sync {now}"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            check=True, capture_output=True, timeout=30,
        )
        logger.info("Git push successful")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e.stderr.decode() if e.stderr else e}")
        return False


def _escape_yaml(s):
    return s.replace('"', '\\"').replace("\n", " ") if s else ""
