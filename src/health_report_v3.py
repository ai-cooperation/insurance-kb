"""
來源健康度報告 + Telegram 告警
v3: 移除 Groq gap scan（改由 verifier.py 的 gemini_gap_scan 處理）
    增加驗證統計到 Telegram 報告
"""

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger("health_report")

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
TZ_UTC8 = timezone(timedelta(hours=8))
DATA_DIR = Path(__file__).parent.parent / "data"

PAGES_URL = "https://cooperation.tw/insurance-kb/"


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

    for articles in crawl_results.values():
        for a in articles:
            source = a.get("source", {})
            report["by_region"][source.get("region", "未知")] += 1
            report["by_type"][source.get("type", "未知")] += 1
            report["new_articles"] += 1

    # 警告：核心地區文章為 0
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


def send_telegram_report(report, gap_scan=None):
    """透過 Telegram 推送每日總結報告"""
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

    # 地區摘要
    if report.get("by_region"):
        region_parts = []
        for region in ["新加坡", "香港", "中國", "日本", "韓國", "台灣", "全球", "亞太"]:
            count = report["by_region"].get(region, 0)
            if count > 0:
                region_parts.append(f"{region}:{count}")
        if region_parts:
            lines.append("🌏 " + " · ".join(region_parts))
            lines.append("")

    # 驗證統計
    verification = report.get("verification")
    if verification:
        v_total = verification["valid"] + verification["fixed"] + verification["invalid"]
        lines.append(f"🔍 驗證: ✅{verification['valid']} 🔧{verification['fixed']} ❌{verification['invalid']} / {v_total}")

    # 審計覆蓋率
    audit_score = report.get("audit_coverage")
    if audit_score is not None:
        emoji = "🟢" if audit_score >= 80 else "🟡" if audit_score >= 60 else "🔴"
        lines.append(f"{emoji} 審計覆蓋率: *{audit_score}%*")

    # AI 補漏
    if gap_scan and gap_scan.get("coverage_score"):
        score = gap_scan['coverage_score']
        emoji = "🟢" if score >= 80 else "🟡" if score >= 60 else "🔴"
        lines.append(f"{emoji} AI覆蓋率: *{score}%*")
        assessment = gap_scan.get('assessment', '')
        if assessment:
            lines.append(f"   {assessment}")

    lines.append("")

    # 審計缺口
    audit_gaps = report.get("audit_gaps", [])
    if audit_gaps:
        lines.append("📋 缺口:")
        for g in audit_gaps[:3]:
            lines.append(f"  · {g}")
        lines.append("")

    # 警告
    if report.get("warnings"):
        for w in report["warnings"][:3]:
            lines.append(w)
        lines.append("")

    # 失敗來源
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
