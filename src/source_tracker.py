"""
來源可信度追蹤模組（系統級）

所有元件（crawler, content_filter, ai_processor, gap_report）
統一透過此模組記錄事件，長期累積來源品質數據。

追蹤維度:
  crawl         — 爬取成功/失敗
  relevance     — 內容相關性（信噪比）
  ai_classify   — AI 分類品質（含模型、修正）
  ai_filter     — AI 過濾判斷品質
  gemini_verify — Gemini 報告 URL 真實性

儲存: data/source_tracking.jsonl (append-only, 每行一筆事件)
"""

import fcntl
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
TRACKING_FILE = DATA_DIR / "source_tracking.jsonl"
TZ_UTC8 = timezone(timedelta(hours=8))

VALID_EVENT_TYPES = {
    "crawl",          # 爬取品質
    "relevance",      # 內容相關性
    "ai_classify",    # AI 分類品質
    "ai_filter",      # AI 過濾判斷
    "gemini_verify",  # Gemini 報告驗證
}

VALID_RESULTS = {
    "ok", "warning", "error",
    # crawl 專用
    "timeout", "404", "encoding_error", "empty",
    # relevance 專用
    "relevant", "filtered",
    # ai_classify 專用
    "fixed", "failed", "fallback",
    # gemini_verify 專用
    "real", "hallucinated", "mismatch",
}


def record(event_type, source, result, detail="", url="", model="", uid=""):
    """
    記錄一筆追蹤事件（append to JSONL）。

    Args:
        event_type: crawl | relevance | ai_classify | ai_filter | gemini_verify
        source:     來源名稱（e.g. "GNews: 亞洲保險產業", "content_filter"）
        result:     結果（ok/error/filtered/fallback/...）
        detail:     細節描述
        url:        相關 URL
        model:      使用的 AI 模型（ai_classify/ai_filter 用）
        uid:        文章 UID（可選）
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    event = {
        "ts": datetime.now(TZ_UTC8).strftime("%Y-%m-%d %H:%M:%S"),
        "type": event_type,
        "source": source,
        "result": result,
        "detail": detail,
    }
    # 只寫入有值的欄位，節省空間
    if url:
        event["url"] = url
    if model:
        event["model"] = model
    if uid:
        event["uid"] = uid

    line = json.dumps(event, ensure_ascii=False) + "\n"

    # 用 file lock 確保並發安全
    with open(TRACKING_FILE, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
        fcntl.flock(f, fcntl.LOCK_UN)


def load_events(days=30, event_type=None, source=None):
    """載入最近 N 天的事件"""
    if not TRACKING_FILE.exists():
        return []

    cutoff = (datetime.now(TZ_UTC8) - timedelta(days=days)).strftime("%Y-%m-%d")
    events = []

    with open(TRACKING_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            # 日期過濾
            if ev.get("ts", "") < cutoff:
                continue
            # 類型過濾
            if event_type and ev.get("type") != event_type:
                continue
            # 來源過濾
            if source and source not in ev.get("source", ""):
                continue

            events.append(ev)

    return events


def get_stats(days=30, event_type=None, source=None):
    """
    聚合統計，回傳結構化報告。

    回傳格式:
    {
        "period": "最近 30 天",
        "total_events": 1234,
        "by_type": {
            "crawl": {"total": 500, "ok": 490, "error": 10, "rate": 98.0},
            ...
        },
        "by_source": {
            "GNews: 亞洲保險產業": {"total": 100, "ok": 95, ...},
            ...
        },
        "ai_models": {
            "llama-3.3-70b-versatile": {"total": 200, "ok": 180, "fallback": 0, ...},
            ...
        }
    }
    """
    events = load_events(days=days, event_type=event_type, source=source)

    stats = {
        "period": f"最近 {days} 天",
        "total_events": len(events),
        "by_type": {},
        "by_source": {},
        "ai_models": {},
    }

    # 按 type 聚合
    type_groups = defaultdict(list)
    for ev in events:
        type_groups[ev.get("type", "unknown")].append(ev)

    for t, evs in type_groups.items():
        result_counter = Counter(ev.get("result", "unknown") for ev in evs)
        total = len(evs)
        ok_count = result_counter.get("ok", 0) + result_counter.get("relevant", 0) + result_counter.get("real", 0)
        stats["by_type"][t] = {
            "total": total,
            "results": dict(result_counter),
            "success_rate": round(ok_count / total * 100, 1) if total else 0,
        }

    # 按 source 聚合
    source_groups = defaultdict(list)
    for ev in events:
        source_groups[ev.get("source", "unknown")].append(ev)

    for s, evs in sorted(source_groups.items(), key=lambda x: -len(x[1])):
        result_counter = Counter(ev.get("result", "unknown") for ev in evs)
        total = len(evs)
        ok_count = result_counter.get("ok", 0) + result_counter.get("relevant", 0) + result_counter.get("real", 0)
        stats["by_source"][s] = {
            "total": total,
            "results": dict(result_counter),
            "success_rate": round(ok_count / total * 100, 1) if total else 0,
        }

    # AI 模型聚合
    model_groups = defaultdict(list)
    for ev in events:
        m = ev.get("model", "")
        if m:
            model_groups[m].append(ev)

    for m, evs in model_groups.items():
        result_counter = Counter(ev.get("result", "unknown") for ev in evs)
        total = len(evs)
        ok_count = result_counter.get("ok", 0)
        stats["ai_models"][m] = {
            "total": total,
            "results": dict(result_counter),
            "success_rate": round(ok_count / total * 100, 1) if total else 0,
        }

    return stats


def rotate_log(keep_days=90):
    """日誌輪替：只保留最近 N 天的事件"""
    if not TRACKING_FILE.exists():
        return 0

    cutoff = (datetime.now(TZ_UTC8) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    kept = []
    removed = 0

    with open(TRACKING_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if ev.get("ts", "") >= cutoff:
                    kept.append(line)
                else:
                    removed += 1
            except json.JSONDecodeError:
                removed += 1

    if removed > 0:
        with open(TRACKING_FILE, "w", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write("\n".join(kept) + "\n" if kept else "")
            fcntl.flock(f, fcntl.LOCK_UN)

    return removed


# ── CLI ──────────────────────────────────────────────────

def _print_stats(stats):
    """格式化輸出統計"""
    print(f"\n{'='*60}")
    print(f"來源品質追蹤報告（{stats['period']}）")
    print(f"總事件數: {stats['total_events']}")
    print(f"{'='*60}")

    if stats["by_type"]:
        print(f"\n{'─'*60}")
        print("按追蹤維度:")
        print(f"{'─'*60}")
        for t, d in stats["by_type"].items():
            print(f"\n  [{t}] 共 {d['total']} 筆, 成功率 {d['success_rate']}%")
            for r, c in sorted(d["results"].items(), key=lambda x: -x[1]):
                bar = "█" * min(c * 30 // d["total"], 30) if d["total"] else ""
                print(f"    {r:20s} {c:5d}  {bar}")

    if stats["ai_models"]:
        print(f"\n{'─'*60}")
        print("AI 模型品質:")
        print(f"{'─'*60}")
        for m, d in stats["ai_models"].items():
            print(f"\n  [{m}] 共 {d['total']} 筆, 成功率 {d['success_rate']}%")
            for r, c in sorted(d["results"].items(), key=lambda x: -x[1]):
                print(f"    {r:20s} {c:5d}")

    # Top 10 有問題的來源
    problem_sources = {
        s: d for s, d in stats["by_source"].items()
        if d["success_rate"] < 100 and d["total"] >= 3
    }
    if problem_sources:
        print(f"\n{'─'*60}")
        print("需要關注的來源（成功率 < 100%，至少 3 筆）:")
        print(f"{'─'*60}")
        for s, d in sorted(problem_sources.items(), key=lambda x: x[1]["success_rate"])[:10]:
            print(f"  {s}: {d['success_rate']}% ({d['total']} 筆)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="來源品質追蹤")
    parser.add_argument("--stats", action="store_true", help="顯示統計報告")
    parser.add_argument("--days", type=int, default=30, help="統計天數")
    parser.add_argument("--type", type=str, help="篩選事件類型")
    parser.add_argument("--source", type=str, help="篩選來源")
    parser.add_argument("--cleanup", action="store_true", help="清理損壞的日誌行（永久保留所有有效紀錄）")
    parser.add_argument("--json", action="store_true", help="JSON 格式輸出")
    args = parser.parse_args()

    if args.cleanup:
        removed = cleanup_log()
        print(f"已清理 {removed} 筆損壞事件")
    elif args.stats or not any([args.cleanup]):
        s = get_stats(days=args.days, event_type=args.type, source=args.source)
        if args.json:
            print(json.dumps(s, ensure_ascii=False, indent=2))
        else:
            _print_stats(s)
