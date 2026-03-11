#!/usr/bin/env python3
"""
保險知識庫 — 缺口分析報告工具

用法:
  # 1. 匯出本週文章摘要（給 Gemini 比對用）
  python3 gap_report.py --export

  # 2. 比對 Gemini 報告，找出遺漏
  python3 gap_report.py --compare reports/gemini_report_20260311.md

  # 3. 從 Gemini 報告中提取缺口文章，生成待補清單
  python3 gap_report.py --gaps reports/gemini_report_20260311.md
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
INDEX_PATH = ROOT / "index" / "master-index.json"
REPORTS_DIR = ROOT / "reports"
TZ_UTC8 = timezone(timedelta(hours=8))


def load_articles():
    """載入所有未過濾的文章"""
    articles = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return [a for a in articles if not a.get("filter")]


def get_date_range(days=7):
    """取得最近 N 天的日期範圍"""
    now = datetime.now(TZ_UTC8)
    end = now.strftime("%Y-%m-%d")
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    return start, end


def export_summary(days=7):
    """匯出最近 N 天的文章摘要，用於 Gemini 比對"""
    articles = load_articles()
    start, end = get_date_range(days)

    recent = [a for a in articles if start <= a.get("date", "") <= end]
    recent.sort(key=lambda a: a.get("date", ""), reverse=True)

    # 按日期分組
    by_date = {}
    for a in recent:
        d = a.get("date", "unknown")
        by_date.setdefault(d, []).append(a)

    lines = [
        f"# 保險知識庫文章摘要",
        f"# 期間: {start} ~ {end}",
        f"# 文章數: {len(recent)}",
        f"# 匯出時間: {datetime.now(TZ_UTC8).strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    for date in sorted(by_date.keys(), reverse=True):
        lines.append(f"## {date} ({len(by_date[date])} 篇)")
        lines.append("")
        for a in by_date[date]:
            title = a.get("title", "無標題")
            source = a.get("source", "")
            category = a.get("category", "")
            url = a.get("source_url", "")
            summary = (a.get("summary", "") or "")[:120]
            companies = ", ".join(a.get("companies", []))

            lines.append(f"- **{title}**")
            lines.append(f"  來源: {source} | 分類: {category}")
            if companies:
                lines.append(f"  公司: {companies}")
            if summary:
                lines.append(f"  摘要: {summary}")
            if url:
                lines.append(f"  URL: {url}")
            lines.append("")

    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"kb_summary_{end.replace('-', '')}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已匯出 {len(recent)} 篇文章摘要 → {out_path}")
    return out_path


def compare_report(report_path, days=7):
    """比對 Gemini 報告與現有文章，找出可能遺漏"""
    report_text = Path(report_path).read_text(encoding="utf-8")
    articles = load_articles()
    start, end = get_date_range(days)
    recent = [a for a in articles if start <= a.get("date", "") <= end]

    # 建立現有文章的標題和關鍵字集合（用於模糊比對）
    existing_titles = set()
    existing_keywords = set()
    existing_companies = set()
    for a in recent:
        title = a.get("title", "").lower()
        existing_titles.add(title)
        for kw in a.get("keywords", []):
            existing_keywords.add(kw.lower())
        for co in a.get("companies", []):
            existing_companies.add(co.lower())

    # 從報告中提取表格行
    report_items = parse_report_table(report_text)

    found = []
    missing = []

    for item in report_items:
        item_title = item.get("title", "").lower()
        item_text = (item.get("title", "") + " " + item.get("summary", "")).lower()

        # 模糊匹配：標題相似度 或 關鍵字匹配
        matched = False
        for existing_title in existing_titles:
            # 計算共同詞數
            item_words = set(re.findall(r'[\w\u4e00-\u9fff]+', item_title))
            exist_words = set(re.findall(r'[\w\u4e00-\u9fff]+', existing_title))
            if len(item_words & exist_words) >= min(3, len(item_words) // 2 + 1):
                matched = True
                break

        if not matched:
            # 檢查公司名稱 + 關鍵主題是否匹配
            item_words = set(re.findall(r'[\w\u4e00-\u9fff]+', item_text))
            company_match = item_words & existing_companies
            keyword_match = item_words & existing_keywords
            if len(company_match) >= 1 and len(keyword_match) >= 2:
                matched = True

        if matched:
            found.append(item)
        else:
            missing.append(item)

    print(f"\n{'='*60}")
    print(f"缺口分析報告")
    print(f"期間: {start} ~ {end}")
    print(f"{'='*60}")
    print(f"\n知識庫現有: {len(recent)} 篇")
    print(f"Gemini 報告: {len(report_items)} 則")
    print(f"已覆蓋: {len(found)} 則")
    print(f"可能遺漏: {len(missing)} 則")

    if missing:
        print(f"\n{'─'*60}")
        print("可能遺漏的新聞:")
        print(f"{'─'*60}")
        for i, item in enumerate(missing, 1):
            print(f"\n{i}. {item.get('title', 'N/A')}")
            print(f"   來源: {item.get('date_source', 'N/A')}")
            print(f"   摘要: {item.get('summary', 'N/A')[:150]}")
            if item.get("url"):
                print(f"   URL: {item['url']}")

    # 儲存缺口報告
    gap_path = REPORTS_DIR / f"gaps_{end.replace('-', '')}.json"
    gap_data = {
        "date_range": f"{start} ~ {end}",
        "kb_articles": len(recent),
        "report_items": len(report_items),
        "covered": len(found),
        "missing": len(missing),
        "missing_items": missing,
        "generated_at": datetime.now(TZ_UTC8).strftime("%Y-%m-%d %H:%M"),
    }
    gap_path.write_text(json.dumps(gap_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n缺口報告已存: {gap_path}")
    return gap_data


def parse_report_table(text):
    """從 Gemini 報告 Markdown 中提取表格行"""
    items = []
    lines = text.split("\n")
    in_table = False

    for line in lines:
        line = line.strip()
        if line.startswith("|") and "---" not in line and "日期" not in line and "來源" not in line:
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) >= 3:
                item = {
                    "date_source": cells[0] if len(cells) > 0 else "",
                    "title": cells[1] if len(cells) > 1 else "",
                    "summary": cells[2] if len(cells) > 2 else "",
                    "url": cells[3] if len(cells) > 3 else "",
                }
                # 只保留有實質內容的行
                if item["title"] and len(item["title"]) > 5:
                    items.append(item)

    return items


def main():
    parser = argparse.ArgumentParser(description="保險知識庫缺口分析")
    parser.add_argument("--export", action="store_true", help="匯出本週文章摘要")
    parser.add_argument("--compare", type=str, help="比對 Gemini 報告檔案路徑")
    parser.add_argument("--gaps", type=str, help="提取缺口清單")
    parser.add_argument("--days", type=int, default=7, help="分析天數（預設 7）")
    args = parser.parse_args()

    if args.export:
        export_summary(args.days)
    elif args.compare or args.gaps:
        path = args.compare or args.gaps
        compare_report(path, args.days)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
