"""
AI 處理管線 — Gemini CLI 主力 + Groq/OpenRouter fallback
"""

import json
import logging
import os
import subprocess
import time

import requests
from groq import Groq

logger = logging.getLogger("ai_processor")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Gemini CLI 無 rate limit（OAuth 免費帳號），作為主力
# Groq/OpenRouter 作為 fallback
FALLBACK_MODELS = [
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "label": "Groq-70B"},
    {"provider": "groq", "model": "llama-3.1-8b-instant", "label": "Groq-8B"},
    {"provider": "openrouter", "model": "meta-llama/llama-3.1-70b-instruct", "label": "OR-Llama70B"},
]

DEFAULT_INTERVAL = 3  # Gemini 不受 Groq rate limit，間隔可以短一些

SYSTEM_PROMPT = """你是保險產業資訊分析專家。根據提供的新聞標題和摘要，產出結構化分析。

回傳嚴格的 JSON 格式（不要加 markdown code block）：
{
  "title_zh": "中文標題（如原文非中文則翻譯）",
  "summary_zh": "詳細中文摘要，800-1000字，完整翻譯原文內容為中文，包含：新聞背景、事件經過、各方觀點、市場影響、未來展望。如原文資訊不足則根據專業知識補充相關背景說明",
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

_rate_limited = {}
RATE_LIMIT_COOLDOWN = 300
_session_count = 0


def get_interval():
    return DEFAULT_INTERVAL


def _is_rate_limited(label):
    if label not in _rate_limited:
        return False
    elapsed = time.time() - _rate_limited[label]
    if elapsed > RATE_LIMIT_COOLDOWN:
        del _rate_limited[label]
        return False
    return True


def _call_gemini(prompt):
    """透過 Gemini CLI 呼叫（OAuth 免費帳號，無 API key 限制）"""
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


def _call_groq(model, messages, max_tokens=1500):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set")
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=model, messages=messages,
        temperature=0.1, max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def _call_openrouter(model, messages, max_tokens=1500):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model, "messages": messages,
            "temperature": 0.1, "max_tokens": max_tokens,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _parse_json(raw):
    """Parse JSON from AI response, stripping markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def _try_fallback_models(messages):
    """Groq/OpenRouter fallback"""
    last_error = None
    for m in FALLBACK_MODELS:
        label = m["label"]
        if _is_rate_limited(label):
            logger.info(f"Skipping {label} (rate limited)")
            continue
        try:
            if m["provider"] == "groq":
                raw = _call_groq(m["model"], messages)
            else:
                raw = _call_openrouter(m["model"], messages)
            result = _parse_json(raw)
            return result, label
        except json.JSONDecodeError as e:
            logger.warning(f"{label} returned invalid JSON: {e}")
            last_error = e
        except Exception as e:
            err_str = str(e).lower()
            if "rate" in err_str or "limit" in err_str or "429" in err_str:
                logger.warning(f"{label} hit rate limit")
                _rate_limited[label] = time.time()
            else:
                logger.error(f"{label} failed: {e}")
            last_error = e
    raise RuntimeError(f"All fallback models failed: {last_error}")


def process_article(title, snippet="", source_name="", source_region=""):
    """對單篇文章做 AI 分析：Gemini CLI 主力 → Groq/OR fallback"""
    global _session_count

    user_msg = f"""來源：{source_name}（{source_region}）
標題：{title}
摘要：{snippet[:500] if snippet else '無'}"""

    full_prompt = f"{SYSTEM_PROMPT}\n\n{user_msg}"

    # 嘗試 Gemini CLI（主力）
    try:
        raw = _call_gemini(full_prompt)
        result = _parse_json(raw)
        _session_count += 1
        return result
    except json.JSONDecodeError:
        logger.warning(f"Gemini returned invalid JSON for: {title[:50]}")
    except Exception as e:
        logger.warning(f"Gemini CLI failed: {e}")

    # Fallback 到 Groq/OpenRouter
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    try:
        result, used_model = _try_fallback_models(messages)
        _session_count += 1
        logger.info(f"Used fallback: {used_model}")
        return result
    except RuntimeError:
        logger.error(f"All models exhausted for: {title[:50]}")
        return _fallback(title, source_region)


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
