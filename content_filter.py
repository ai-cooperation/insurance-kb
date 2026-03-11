#!/usr/bin/env python3
"""
保險知識庫 — 內容相關性過濾器
每日爬取後執行，用 AI 判斷並移除非保險相關文章

用法:
  python3 content_filter.py                # dry-run（預設，僅顯示結果）
  python3 content_filter.py --apply        # 實際移除不相關文章
  python3 content_filter.py --batch-size 5 # 調整每批次數量
  python3 content_filter.py --today-only   # 只檢查今天的文章
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
AUDIT_DIR = ROOT / "data" / "filter-audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

INDEX_PATH = ROOT / "index" / "master-index.json"
TZ_UTC8 = timezone(timedelta(hours=8))

# 載入環境變數
env_path = Path("/home/ac-macmini2/world-monitor/.env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "content_filter.log"),
    ],
)
logger = logging.getLogger("content_filter")

# ── AI 判斷 Prompt ──

FILTER_PROMPT = """你是保險產業內容審查專家。判斷以下文章是否與「保險產業」相關。

相關的定義（符合任一即為相關）：
- 保險公司、再保公司、保經保代的業務、財報、人事
- 保險產品、保單、理賠、核保、精算
- 保險監管、法規、金管會、保監局
- 保險科技 InsurTech
- 風險管理（與保險業直接相關的）
- 保險業的 ESG、永續發展
- 保險業的併購、投資、市場趨勢
- 退休金、年金、長照保險
- 保險公司冠名的活動（如體育賽事冠名）→ 不相關，除非內容討論保險業務本身

不相關的範例：
- 一般政治、軍事、外交新聞
- 純科技新聞（AI、晶片、社群媒體）與保險無關
- 體育、娛樂、時尚、美食
- 石油、能源、礦業（除非討論相關保險影響）
- 一般經濟新聞（股市、GDP）與保險無直接關聯
- 保險公司冠名的體育賽事報導（如明治安田聯賽的比賽結果）

請對每篇文章回傳 JSON 陣列，格式：
[
  {"id": "文章編號", "relevant": true/false, "reason": "簡短理由（10字內）"}
]

只回傳 JSON，不要加 markdown code block 或其他文字。"""


def _call_gemini(prompt):
    """透過 Gemini CLI 呼叫"""
    try:
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Gemini CLI error: {result.stderr[:200]}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError("Gemini CLI timeout (120s)")


def _call_groq(prompt, model="llama-3.3-70b-versatile"):
    """Groq API fallback"""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set")
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": FILTER_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=2000,
    )
    return resp.choices[0].message.content.strip()


def _parse_json(raw):
    """Parse JSON，處理 markdown fences"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def judge_batch(articles):
    """
    批次判斷文章相關性。
    回傳 dict: {uid: {"relevant": bool, "reason": str}}
    """
    # 組裝 prompt
    items = []
    for i, a in enumerate(articles):
        title = a.get("title", "")
        summary = (a.get("summary", "") or "")[:200]
        source = a.get("source", "")
        items.append(f"[{i+1}] 來源: {source}\n    標題: {title}\n    摘要: {summary}")

    user_prompt = "請判斷以下文章是否與保險產業相關：\n\n" + "\n\n".join(items)
    user_prompt += "\n\n回傳格式：[{\"id\": 1, \"relevant\": true/false, \"reason\": \"理由\"}]"

    full_prompt = FILTER_PROMPT + "\n\n" + user_prompt

    # 嘗試 Gemini
    raw = None
    try:
        raw = _call_gemini(full_prompt)
        results = _parse_json(raw)
    except Exception as e:
        logger.warning(f"Gemini failed: {e}")
        # Fallback Groq
        try:
            raw = _call_groq(user_prompt)
            results = _parse_json(raw)
        except Exception as e2:
            logger.error(f"Groq also failed: {e2}")
            if raw:
                logger.debug(f"Raw response: {raw[:500]}")
            # 安全起見：判斷失敗時保留所有文章
            return {a["uid"]: {"relevant": True, "reason": "AI判斷失敗，保留"} for a in articles}

    # 對應回文章
    judgments = {}
    for r in results:
        idx = r.get("id", 0)
        if isinstance(idx, int) and 1 <= idx <= len(articles):
            uid = articles[idx - 1]["uid"]
            judgments[uid] = {
                "relevant": r.get("relevant", True),
                "reason": r.get("reason", ""),
            }

    # 沒被 AI 回應到的文章，保留
    for a in articles:
        if a["uid"] not in judgments:
            judgments[a["uid"]] = {"relevant": True, "reason": "AI未回應，保留"}

    return judgments


def load_index():
    """載入 master-index.json"""
    if not INDEX_PATH.exists():
        logger.error("master-index.json not found")
        sys.exit(1)
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def save_index(articles):
    """儲存 master-index.json"""
    INDEX_PATH.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_audit(removed, timestamp):
    """儲存審計日誌"""
    audit_path = AUDIT_DIR / f"removed_{timestamp}.json"
    audit_path.write_text(
        json.dumps(removed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return audit_path


def rebuild_site():
    """重建靜態網站"""
    try:
        subprocess.run(
            ["python3", "build_site.py"],
            cwd=str(ROOT),
            capture_output=True, text=True, timeout=60,
        )
        logger.info("Site rebuilt")
    except Exception as e:
        logger.warning(f"Site rebuild failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="保險知識庫內容過濾器")
    parser.add_argument("--apply", action="store_true", help="實際執行移除（預設 dry-run）")
    parser.add_argument("--batch-size", type=int, default=10, help="每批次判斷數量")
    parser.add_argument("--today-only", action="store_true", help="只檢查今天的文章")
    parser.add_argument("--date", type=str, help="只檢查指定日期 (YYYY-MM-DD)")
    parser.add_argument("--delay", type=float, default=3.0, help="批次間隔秒數")
    args = parser.parse_args()

    now = datetime.now(TZ_UTC8)
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    logger.info("=" * 50)
    logger.info(f"內容過濾開始 {'[DRY-RUN]' if not args.apply else '[APPLY]'}")

    articles = load_index()
    logger.info(f"索引總數: {len(articles)}")

    # 篩選要檢查的文章
    if args.today_only:
        target_date = now.strftime("%Y-%m-%d")
        candidates = [a for a in articles if a.get("date") == target_date]
        logger.info(f"今日文章: {len(candidates)}")
    elif args.date:
        candidates = [a for a in articles if a.get("date") == args.date]
        logger.info(f"{args.date} 文章: {len(candidates)}")
    else:
        candidates = list(articles)

    if not candidates:
        logger.info("無文章需要檢查")
        return

    # 批次 AI 判斷
    all_judgments = {}
    total_batches = (len(candidates) + args.batch_size - 1) // args.batch_size

    for batch_idx in range(0, len(candidates), args.batch_size):
        batch = candidates[batch_idx:batch_idx + args.batch_size]
        batch_num = batch_idx // args.batch_size + 1
        logger.info(f"批次 {batch_num}/{total_batches} ({len(batch)} 篇)...")

        judgments = judge_batch(batch)
        all_judgments.update(judgments)

        irrelevant_in_batch = sum(1 for j in judgments.values() if not j["relevant"])
        if irrelevant_in_batch:
            for uid, j in judgments.items():
                if not j["relevant"]:
                    title = next((a["title"] for a in batch if a["uid"] == uid), "?")
                    logger.info(f"  ✗ {title[:60]} → {j['reason']}")

        if batch_num < total_batches:
            time.sleep(args.delay)

    # 統計結果
    irrelevant_uids = {uid for uid, j in all_judgments.items() if not j["relevant"]}
    relevant_count = len(candidates) - len(irrelevant_uids)

    logger.info(f"\n{'='*50}")
    logger.info(f"判斷結果: {relevant_count} 相關, {len(irrelevant_uids)} 不相關")

    if not irrelevant_uids:
        logger.info("全部文章皆為保險相關，無需清理")
        return

    # 收集要移除的文章資料（審計用）
    removed_articles = [a for a in articles if a["uid"] in irrelevant_uids]
    for a in removed_articles:
        j = all_judgments.get(a["uid"], {})
        a["_filter_reason"] = j.get("reason", "")
        a["_filter_time"] = timestamp

    if not args.apply:
        logger.info("\n[DRY-RUN] 以下文章將被移除：")
        for a in removed_articles:
            logger.info(f"  [{a.get('source', '')}] {a['title'][:70]}")
            logger.info(f"    理由: {a['_filter_reason']}")
        logger.info(f"\n執行 --apply 以實際移除這 {len(removed_articles)} 篇文章")
        # dry-run 也存審計檔供檢視
        audit_path = save_audit(removed_articles, f"dryrun_{timestamp}")
        logger.info(f"審計檔: {audit_path}")
        return

    # 實際移除
    logger.info("移除不相關文章...")
    new_articles = [a for a in articles if a["uid"] not in irrelevant_uids]
    save_index(new_articles)
    logger.info(f"索引更新: {len(articles)} → {len(new_articles)} ({len(irrelevant_uids)} 移除)")

    # 移除對應的 .md 檔案
    removed_notes = 0
    for a in removed_articles:
        note_path = a.get("note_path", "")
        if note_path:
            full_path = ROOT / note_path
            if full_path.exists():
                full_path.unlink()
                removed_notes += 1
    logger.info(f"移除 Markdown 筆記: {removed_notes} 檔")

    # 儲存審計日誌
    audit_path = save_audit(removed_articles, timestamp)
    logger.info(f"審計日誌: {audit_path}")

    # 重建網站
    rebuild_site()

    logger.info(f"內容過濾完成: 移除 {len(irrelevant_uids)} 篇不相關文章")


if __name__ == "__main__":
    main()
