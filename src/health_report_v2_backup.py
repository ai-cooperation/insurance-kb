"""
來源健康度報告 + Telegram 告警 + 第二層 AI 補漏
"""

import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger("health_report")

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
TZ_UTC8 = timezone(timedelta(hours=8))
DATA_DIR = Path(__file__).parent.parent / "data"

PAGES_URL = "https://insurance-kb.cooperation.tw/cards"


def generate_health_report(health_results, crawl_results):
    """產出來源健康度報告"""
    now = datetime.now(TZ_UTC8)
    report = {
        "timestamp": now.isoformat(),
        "total_sources": len(health_results),
        "success": 0,
        "failed": 0,
        "total_articles": 0,
        "new_articles": 0,
        "by_method": Counter(),
        "by_region": Counter(),
        "by_type": Counter(),
        "failed_sources": [],
        "warnings": [],
    }

    for h in health_results:
        if h.get("status") == "ok":
            report["success"] += 1
            report["total_articles"] += h.get("count", 0)
        else:
            report["failed"] += 1
            report["failed_sources"].append({
                "id": h.get("source_id", ""),
                "error": h.get("error", "unknown"),
                "method": h.get("method", ""),
            })
        report["by_method"][h.get("method", "")] += 1

    # 按地區統計新文章數
    for articles in crawl_results.values():
        for a in articles:
            source = a.get("source", {})
            report["by_region"][source.get("region", "未知")] += 1
            report["by_type"][source.get("type", "未知")] += 1
            report["new_articles"] += 1

    # 警告：某地區文章為 0
    for region in ["新加坡", "香港", "中國", "日本", "韓國"]:
        if report["by_region"].get(region, 0) == 0:
            report["warnings"].append(f"⚠️ {region} 本次無任何文章")

    # 連續失敗偵測
    fail_history = _load_fail_history()
    for src in report["failed_sources"]:
        sid = src["id"]
        fail_history[sid] = fail_history.get(sid, 0) + 1
        if fail_history[sid] >= 3:
            report["warnings"].append(f"🔴 {sid} 已連續失敗 {fail_history[sid]} 次")
    success_ids = {h["source_id"] for h in health_results if h.get("status") == "ok"}
    for sid in success_ids:
        fail_history.pop(sid, None)
    _save_fail_history(fail_history)

    # 存儲報告
    report_path = DATA_DIR / "health" / f"health_{now.strftime('%Y%m%d_%H%M')}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {**report}
    serializable["by_method"] = dict(report["by_method"])
    serializable["by_region"] = dict(report["by_region"])
    serializable["by_type"] = dict(report["by_type"])
    report_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2))

    return report


def ai_gap_scan(collected_articles, health_results):
    """第二層 AI 補漏掃描"""
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set, skipping AI gap scan")
        return []

    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)

    titles_by_region = defaultdict(list)
    for articles in collected_articles.values():
        for a in articles:
            region = a.get("source", {}).get("region", "全球")
            title = a.get("crawl", {}).get("title", "")
            if title:
                titles_by_region[region].append(title[:60])

    summary_parts = []
    for region, titles in titles_by_region.items():
        summary_parts.append(f"## {region} ({len(titles)} 篇)")
        for t in titles[:10]:
            summary_parts.append(f"- {t}")

    prompt = f"""你是保險產業分析師。以下是今天自動爬蟲收集到的保險新聞：

{chr(10).join(summary_parts)}

請分析是否有重要遺漏。考慮以下面向：
1. 地區覆蓋：新加坡、香港、中國、日本、韓國 是否都有涵蓋？
2. 類型覆蓋：監管動態、產品創新、市場趨勢、科技應用、再保市場 是否有涵蓋？
3. 重大事件：近期（最近一週）是否有重要的保險業新聞被遺漏？

請回傳 JSON 格式（不要 markdown code block）：
{{
  "gaps": ["遺漏描述1", "遺漏描述2"],
  "search_queries": ["建議搜尋關鍵字1", "建議搜尋關鍵字2"],
  "coverage_score": 85,
  "assessment": "一句話評估"
}}"""

    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=500,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(raw)
        logger.info(f"AI gap scan: coverage={result.get('coverage_score')}%, gaps={len(result.get('gaps', []))}")
        return result
    except Exception as e:
        logger.error(f"AI gap scan failed: {e}")
        return {"gaps": [], "search_queries": [], "coverage_score": 0, "assessment": f"分析失敗: {e}"}


def send_telegram_report(report, gap_scan=None):
    """透過 Telegram 推送每日總結報告 + 頁面連結"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logger.warning("Telegram not configured, skipping")
        return

    now_str = report['timestamp'][:16]
    new_count = report.get('new_articles', 0)

    lines = [
        f"📊 *保險知識庫 \\| {now_str}*",
        "",
        f"✅ 來源: {report['success']}/{report['total_sources']}",
        f"📰 新增: *{new_count}* 篇",
        "",
    ]

    # 地區摘要（一行）
    if report.get("by_region"):
        region_parts = []
        for region in ["新加坡", "香港", "中國", "日本", "韓國", "台灣", "全球", "亞太"]:
            count = report["by_region"].get(region, 0)
            if count > 0:
                region_parts.append(f"{region}:{count}")
        if region_parts:
            lines.append("🌏 " + " · ".join(region_parts))
            lines.append("")

    # AI 覆蓋率
    if gap_scan and gap_scan.get("coverage_score"):
        score = gap_scan['coverage_score']
        emoji = "🟢" if score >= 80 else "🟡" if score >= 60 else "🔴"
        lines.append(f"{emoji} AI覆蓋率: *{score}%*")
        assessment = gap_scan.get('assessment', '')
        if assessment:
            lines.append(f"   {assessment}")
        lines.append("")

    # 警告（如有）
    if report.get("warnings"):
        for w in report["warnings"][:3]:
            lines.append(w)
        lines.append("")

    # 失敗來源（如有）
    if report.get("failed_sources"):
        lines.append(f"❌ 失敗: {report['failed']}")
        for f in report["failed_sources"][:3]:
            lines.append(f"  {f['id']}")
        lines.append("")

    # 頁面連結
    lines.append(f"🔗 [查看完整報告]({PAGES_URL})")

    text = "\n".join(lines)
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        logger.info("Telegram report sent")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


def _load_fail_history():
    path = DATA_DIR / "fail_history.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_fail_history(history):
    path = DATA_DIR / "fail_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2))
