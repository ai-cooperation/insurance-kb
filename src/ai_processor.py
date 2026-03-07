"""
AI 處理管線 — 多模型 fallback + batch 模式
主力: Groq llama-3.3-70b → fallback: Groq 8b → fallback: OpenRouter
"""

import json
import logging
import os
import time

import requests
from groq import Groq

logger = logging.getLogger("ai_processor")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

MODELS = [
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "label": "Groq-70B"},
    {"provider": "groq", "model": "llama-3.1-8b-instant", "label": "Groq-8B"},
    {"provider": "openrouter", "model": "meta-llama/llama-3.1-70b-instruct", "label": "OR-Llama70B"},
]

SYSTEM_PROMPT = """你是保險產業資訊分析專家。根據提供的新聞標題和摘要，產出結構化分析。

回傳嚴格的 JSON 格式（不要加 markdown code block）：
{
  "title_zh": "中文標題（如原文非中文則翻譯）",
  "summary_zh": "中文摘要，150字以內",
  "category": "主類別",
  "subcategory": "子類別",
  "region": "地區",
  "companies": ["相關公司"],
  "keywords": ["關鍵字1", "關鍵字2", "關鍵字3", "關鍵字4", "關鍵字5"],
  "importance": "高/中/低"
}

主類別選項：監管動態, 產品創新, 市場趨勢, 科技應用, 再保市場, ESG永續, 消費者保護, 人才與組織
子類別根據主類別選擇適當的子分類
地區選項：新加坡, 香港, 中國, 日本, 韓國, 台灣, 美國, 歐洲, 亞太, 全球
重要程度：高=影響整個市場或監管變革, 中=特定公司或地區重要動態, 低=一般資訊"""

BATCH_SYSTEM_PROMPT = """你是保險產業資訊分析專家。你會收到多篇新聞，請對每篇產出結構化分析。

回傳嚴格的 JSON array 格式（不要加 markdown code block）：
[
  {
    "index": 0,
    "title_zh": "中文標題",
    "summary_zh": "中文摘要，150字以內",
    "category": "主類別",
    "subcategory": "子類別",
    "region": "地區",
    "companies": ["相關公司"],
    "keywords": ["關鍵字1", "關鍵字2"],
    "importance": "高/中/低"
  }
]

主類別選項：監管動態, 產品創新, 市場趨勢, 科技應用, 再保市場, ESG永續, 消費者保護, 人才與組織
地區選項：新加坡, 香港, 中國, 日本, 韓國, 台灣, 美國, 歐洲, 亞太, 全球
重要程度：高=影響整個市場或監管變革, 中=特定公司或地區重要動態, 低=一般資訊"""

# Track which models have hit rate limits (reset after cooldown)
_rate_limited = {}  # model_label -> timestamp
RATE_LIMIT_COOLDOWN = 300  # 5 min cooldown before retrying a rate-limited model


def _is_rate_limited(label):
    if label not in _rate_limited:
        return False
    elapsed = time.time() - _rate_limited[label]
    if elapsed > RATE_LIMIT_COOLDOWN:
        del _rate_limited[label]
        return False
    return True


def _call_groq(model, messages, max_tokens=500):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set")
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.1,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def _call_openrouter(model, messages, max_tokens=500):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _call_model(provider, model, messages, max_tokens=500):
    if provider == "groq":
        return _call_groq(model, messages, max_tokens)
    elif provider == "openrouter":
        return _call_openrouter(model, messages, max_tokens)
    else:
        raise ValueError(f"Unknown provider: {provider}")


def _parse_json(raw):
    """Parse JSON from AI response, stripping markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def _try_models(messages, max_tokens=500):
    """Try each model in order, skipping rate-limited ones."""
    last_error = None
    for m in MODELS:
        label = m["label"]
        if _is_rate_limited(label):
            logger.info(f"Skipping {label} (rate limited)")
            continue
        try:
            raw = _call_model(m["provider"], m["model"], messages, max_tokens)
            result = _parse_json(raw)
            return result, label
        except json.JSONDecodeError as e:
            logger.warning(f"{label} returned invalid JSON: {e}")
            last_error = e
            continue
        except Exception as e:
            err_str = str(e).lower()
            if "rate" in err_str or "limit" in err_str or "429" in err_str or "quota" in err_str:
                logger.warning(f"{label} hit rate limit, marking cooldown")
                _rate_limited[label] = time.time()
            else:
                logger.error(f"{label} failed: {e}")
            last_error = e
            continue
    raise RuntimeError(f"All models failed. Last error: {last_error}")


def process_article(title, snippet="", source_name="", source_region=""):
    """對單篇文章做 AI 分析，自動 fallback 到其他模型"""
    user_msg = f"""來源：{source_name}（{source_region}）
標題：{title}
摘要：{snippet[:500] if snippet else '無'}"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    try:
        result, used_model = _try_models(messages)
        if used_model != "Groq-70B":
            logger.info(f"Used fallback model: {used_model}")
        return result
    except RuntimeError:
        logger.error(f"All models exhausted for: {title[:50]}")
        return _fallback(title, source_region)


def process_batch(articles, batch_size=5):
    """批次處理多篇文章，減少 API 呼叫次數和 token 用量。

    articles: list of dict with keys: title, snippet, source_name, source_region
    Returns: list of AI result dicts (same order as input)
    """
    results = [None] * len(articles)

    for i in range(0, len(articles), batch_size):
        batch = articles[i:i + batch_size]
        batch_text = ""
        for j, art in enumerate(batch):
            snippet = (art.get("snippet") or "")[:300]
            batch_text += f"\n---\n文章 {j}:\n來源：{art.get('source_name', '')}（{art.get('source_region', '')}）\n標題：{art.get('title', '')}\n摘要：{snippet or '無'}\n"

        messages = [
            {"role": "system", "content": BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": batch_text},
        ]

        try:
            parsed, used_model = _try_models(messages, max_tokens=300 * len(batch))
            logger.info(f"Batch {i//batch_size + 1}: {len(batch)} articles via {used_model}")

            if isinstance(parsed, list):
                for item in parsed:
                    idx = item.get("index", 0)
                    if 0 <= idx < len(batch):
                        results[i + idx] = item
            elif isinstance(parsed, dict):
                # Single article returned as dict
                results[i] = parsed
        except RuntimeError:
            logger.error(f"Batch {i//batch_size + 1}: all models failed")

        # Fill missing with fallback
        for j, art in enumerate(batch):
            if results[i + j] is None:
                results[i + j] = _fallback(
                    art.get("title", ""),
                    art.get("source_region", ""),
                )

        time.sleep(1)  # Brief pause between batches

    return results


def _fallback(title, region):
    """AI 失敗時的降級處理"""
    return {
        "title_zh": title,
        "summary_zh": "",
        "category": "市場趨勢",
        "subcategory": "一般資訊",
        "region": region or "全球",
        "companies": [],
        "keywords": [],
        "importance": "低",
    }
