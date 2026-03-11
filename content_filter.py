#!/usr/bin/env python3
"""
保險知識庫 — 內容相關性過濾器（標記模式）
每日爬取後執行，用 AI 判斷新文章是否保險相關，
不相關的文章加上 filter 標記，前端自動隱藏。

用法:
  python3 content_filter.py                # 只處理未標記的文章（預設）
  python3 content_filter.py --today-only   # 只處理今天的文章
  python3 content_filter.py --recheck-all  # 重新檢查所有文章
  python3 content_filter.py --dry-run      # 只顯示結果不寫入
  python3 content_filter.py --batch-size 5 # 調整每批次數量
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

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

# 優先使用專用 key，fallback 到通用 key
GROQ_API_KEY = os.environ.get("GROQ_FILTER_API_KEY", os.environ.get("GROQ_API_KEY", ""))

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
- 空白或無意義的標題

請對每篇文章回傳 JSON 陣列，格式：
[
  {"id": 1, "relevant": true/false, "tag": "標記類型或null", "reason": "簡短理由（10字內）"}
]

tag 值選項（僅不相關時填寫）：
- "一般新聞" — 與保險無關的一般新聞
- "體育賽事" — 保險公司冠名的體育報導
- "空白內容" — 空白或導航頁面
- null — 相關文章

只回傳 JSON，不要加 markdown code block 或其他文字。"""


def _call_groq(prompt, model="llama-3.3-70b-versatile"):
    """Groq API"""
    if not GROQ_API_KEY:
        raise RuntimeError("No Groq API key (set GROQ_FILTER_API_KEY or GROQ_API_KEY)")
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
    回傳 dict: {uid: {"relevant": bool, "tag": str|None, "reason": str}}
    """
    items = []
    for i, a in enumerate(articles):
        title = a.get("title", "")
        summary = (a.get("summary", "") or "")[:200]
        source = a.get("source", "")
        items.append(f"[{i+1}] 來源: {source}\n    標題: {title}\n    摘要: {summary}")

    user_prompt = "請判斷以下文章是否與保險產業相關：\n\n" + "\n\n".join(items)

    # 嘗試主模型，失敗降級小模型
    models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    raw = None

    for model in models:
        try:
            raw = _call_groq(user_prompt, model=model)
            results = _parse_json(raw)
            break
        except json.JSONDecodeError:
            logger.warning(f"{model} returned invalid JSON")
            continue
        except Exception as e:
            err_str = str(e).lower()
            if "rate" in err_str or "429" in err_str:
                logger.warning(f"{model} rate limited, trying next")
                continue
            logger.error(f"{model} failed: {e}")
            continue
    else:
        logger.error("All models failed")
        if raw:
            logger.debug(f"Last raw: {raw[:300]}")
        return {a["uid"]: {"relevant": True, "tag": None, "reason": "AI判斷失敗"} for a in articles}

    # 對應回文章
    judgments = {}
    for r in results:
        idx = r.get("id", 0)
        if isinstance(idx, int) and 1 <= idx <= len(articles):
            uid = articles[idx - 1]["uid"]
            judgments[uid] = {
                "relevant": r.get("relevant", True),
                "tag": r.get("tag"),
                "reason": r.get("reason", ""),
            }

    # 未回應的保留
    for a in articles:
        if a["uid"] not in judgments:
            judgments[a["uid"]] = {"relevant": True, "tag": None, "reason": "AI未回應"}

    return judgments


def load_index():
    if not INDEX_PATH.exists():
        logger.error("master-index.json not found")
        sys.exit(1)
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def save_index(articles):
    INDEX_PATH.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="保險知識庫內容過濾器（標記模式）")
    parser.add_argument("--dry-run", action="store_true", help="只顯示結果不寫入")
    parser.add_argument("--batch-size", type=int, default=10, help="每批次判斷數量")
    parser.add_argument("--today-only", action="store_true", help="只處理今天的文章")
    parser.add_argument("--date", type=str, help="只處理指定日期 (YYYY-MM-DD)")
    parser.add_argument("--recheck-all", action="store_true", help="重新檢查所有文章")
    parser.add_argument("--delay", type=float, default=3.0, help="批次間隔秒數")
    args = parser.parse_args()

    now = datetime.now(TZ_UTC8)
    logger.info("=" * 50)
    logger.info(f"內容過濾開始 {'[DRY-RUN]' if args.dry_run else '[APPLY]'}")

    articles = load_index()
    logger.info(f"索引總數: {len(articles)}")

    # 選擇要檢查的文章
    if args.recheck_all:
        candidates_idx = list(range(len(articles)))
    elif args.today_only:
        target_date = now.strftime("%Y-%m-%d")
        candidates_idx = [i for i, a in enumerate(articles) if a.get("date") == target_date]
        logger.info(f"今日文章: {len(candidates_idx)}")
    elif args.date:
        candidates_idx = [i for i, a in enumerate(articles) if a.get("date") == args.date]
        logger.info(f"{args.date} 文章: {len(candidates_idx)}")
    else:
        # 預設：只處理尚未標記的文章（filter 欄位不存在）
        candidates_idx = [i for i, a in enumerate(articles) if "filter" not in a]
        logger.info(f"未標記文章: {len(candidates_idx)}")

    if not candidates_idx:
        logger.info("無文章需要檢查")
        return

    candidates = [articles[i] for i in candidates_idx]

    # 批次 AI 判斷
    tagged_count = 0
    total_batches = (len(candidates) + args.batch_size - 1) // args.batch_size

    for batch_start in range(0, len(candidates), args.batch_size):
        batch = candidates[batch_start:batch_start + args.batch_size]
        batch_num = batch_start // args.batch_size + 1
        logger.info(f"批次 {batch_num}/{total_batches} ({len(batch)} 篇)...")

        judgments = judge_batch(batch)

        for a in batch:
            j = judgments.get(a["uid"], {})
            if not j.get("relevant", True):
                tag = j.get("tag", "非保險相關") or "非保險相關"
                # 找到原始 index 中的位置並更新
                orig_idx = next(i for i, x in enumerate(articles) if x["uid"] == a["uid"])
                articles[orig_idx]["filter"] = tag
                tagged_count += 1
                logger.info(f"  ✗ [{tag}] {a.get('title', '')[:60]}")
            else:
                orig_idx = next(i for i, x in enumerate(articles) if x["uid"] == a["uid"])
                articles[orig_idx]["filter"] = None

        if batch_num < total_batches:
            time.sleep(args.delay)

    logger.info(f"\n{'='*50}")
    logger.info(f"結果: {tagged_count} 篇被標記為不相關, {len(candidates) - tagged_count} 篇保留")

    if args.dry_run:
        logger.info("[DRY-RUN] 不寫入變更")
        return

    save_index(articles)
    logger.info("索引已更新（標記模式）")

    # 重建網站
    try:
        import subprocess
        subprocess.run(["python3", "build_site.py"], cwd=str(ROOT), capture_output=True, timeout=60)
        logger.info("網站已重建")
    except Exception as e:
        logger.warning(f"網站重建失敗: {e}")

    logger.info(f"內容過濾完成")


if __name__ == "__main__":
    main()
