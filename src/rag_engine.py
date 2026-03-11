"""
RAG 引擎 — TF-IDF 搜尋 + Groq API 對話
從 master-index.json 載入知識庫，用 TF-IDF 找相關文章，
組成 context 餵給 Groq API 回答用戶問題
"""

import json
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path

import requests

logger = logging.getLogger("rag_engine")

AI_HUB_URL = "http://localhost:8760/api/llm/chat"

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

# 追蹤目前使用的模型與狀態
_current_model = None
_api_status = "unknown"

# 中文停用詞
STOP_WORDS = set(
    "的 了 在 是 我 有 和 就 不 人 都 一 一個 上 也 很 到 說 要 去 你 會 著 沒有 看 好 "
    "自己 這 他 她 它 們 那 裡 為 什麼 麼 吧 嗎 呢 把 被 讓 給 從 向 對 跟 以 但 而 "
    "或 如果 因為 所以 雖然 但是 然後 可以 已經 還 又 再 更 最 這個 那個 "
    "the a an and or but in on at to for of is are was were be been has have had "
    "with from by this that it its as not will can do does did may might shall should would could".split()
)


def get_api_status():
    """回傳目前 API 狀態"""
    return {"status": _api_status, "model": _current_model}


def _call_groq(prompt, system_prompt="", model=None):
    """呼叫 Groq API，指定模型"""
    global _current_model, _api_status
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        logger.warning("GROQ_API_KEY not set")
        return None
    if model is None:
        model = GROQ_MODELS[0]
    try:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=60,
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"].strip()
            _current_model = model
            _api_status = "ok"
            logger.info(f"Groq [{model}] replied ({len(content)} chars)")
            return content
        if resp.status_code == 429:
            logger.warning(f"Groq [{model}] rate limited")
            return None
        logger.warning(f"Groq [{model}] HTTP {resp.status_code}: {resp.text[:200]}")
    except requests.Timeout:
        logger.warning(f"Groq [{model}] timeout")
    except Exception as e:
        logger.warning(f"Groq [{model}] error: {e}")
    return None


def _call_ai_hub(prompt, system_prompt=""):
    """備援：呼叫本機 AI Hub 2"""
    global _current_model, _api_status
    try:
        resp = requests.post(
            AI_HUB_URL,
            json={"prompt": prompt, "system_prompt": system_prompt, "provider": "auto"},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("content"):
                _current_model = "ai-hub-" + str(data.get("provider_used", "unknown"))
                _api_status = "ok"
                logger.info(f"AI Hub replied via {data.get('provider_used')}")
                return data["content"].strip()
    except Exception as e:
        logger.warning(f"AI Hub fallback error: {e}")
    return None


def _call_ai(prompt, system_prompt=""):
    """Groq 70B → Groq 8B → AI Hub"""
    global _api_status
    # 1. 主力模型
    result = _call_groq(prompt, system_prompt, GROQ_MODELS[0])
    if result:
        return result
    # 2. Fallback 小模型
    result = _call_groq(prompt, system_prompt, GROQ_MODELS[1])
    if result:
        return result
    # 3. AI Hub
    result = _call_ai_hub(prompt, system_prompt)
    if result:
        return result
    _api_status = "unavailable"
    return None


class RAGEngine:
    """TF-IDF 搜尋 + Groq API 對話引擎"""

    def __init__(self, index_path):
        self.index_path = Path(index_path)
        self.articles = []
        self.doc_tokens = []
        self.idf = {}
        self.doc_tfidf = []
        self._load_and_index()

    def _load_and_index(self):
        if not self.index_path.exists():
            logger.error(f"Index not found: {self.index_path}")
            return
        self.articles = json.loads(self.index_path.read_text(encoding="utf-8"))
        logger.info(f"Loaded {len(self.articles)} articles")
        for article in self.articles:
            text = self._article_to_text(article)
            tokens = self._tokenize(text)
            self.doc_tokens.append(tokens)
        n = len(self.articles)
        df = Counter()
        for tokens in self.doc_tokens:
            for t in set(tokens):
                df[t] += 1
        self.idf = {t: math.log(n / (1 + count)) for t, count in df.items()}
        for tokens in self.doc_tokens:
            tf = Counter(tokens)
            total = len(tokens) if tokens else 1
            tfidf = {}
            for t, count in tf.items():
                tfidf[t] = (count / total) * self.idf.get(t, 0)
            self.doc_tfidf.append(tfidf)
        logger.info(f"TF-IDF index built: {len(self.idf)} terms")

    def _article_to_text(self, article):
        parts = [
            article.get("title", ""),
            article.get("summary", ""),
            article.get("category", ""),
            article.get("subcategory", ""),
            article.get("region", ""),
            " ".join(article.get("keywords", [])),
            " ".join(article.get("companies", [])),
        ]
        return " ".join(parts)

    def _tokenize(self, text):
        text = text.lower().strip()
        tokens = []
        eng_words = re.findall(r"[a-z][a-z0-9]+", text)
        tokens.extend(w for w in eng_words if w not in STOP_WORDS and len(w) > 1)
        chinese = re.findall(r"[\u4e00-\u9fff]+", text)
        for seg in chinese:
            for i in range(len(seg) - 1):
                bigram = seg[i:i+2]
                if bigram not in STOP_WORDS:
                    tokens.append(bigram)
            for c in seg:
                if c not in STOP_WORDS:
                    tokens.append(c)
        return tokens

    def search(self, query, top_k=5):
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        qtf = Counter(query_tokens)
        total = len(query_tokens)
        query_tfidf = {}
        for t, count in qtf.items():
            query_tfidf[t] = (count / total) * self.idf.get(t, 0)
        scores = []
        for i, doc_vec in enumerate(self.doc_tfidf):
            dot = sum(query_tfidf.get(t, 0) * doc_vec.get(t, 0) for t in query_tfidf)
            mag_q = math.sqrt(sum(v**2 for v in query_tfidf.values())) or 1
            mag_d = math.sqrt(sum(v**2 for v in doc_vec.values())) or 1
            sim = dot / (mag_q * mag_d)
            if sim > 0:
                scores.append((sim, i))
        scores.sort(reverse=True)
        results = []
        for sim, idx in scores[:top_k]:
            article = self.articles[idx]
            results.append({
                "score": round(sim, 4),
                "title": article.get("title", ""),
                "summary": article.get("summary", ""),
                "category": article.get("category", ""),
                "region": article.get("region", ""),
                "date": article.get("date", ""),
                "source": article.get("source", ""),
                "source_url": article.get("source_url", ""),
                "keywords": article.get("keywords", []),
                "companies": article.get("companies", []),
                "importance": article.get("importance", ""),
            })
        return results

    def chat(self, question, history=None):
        results = self.search(question, top_k=5)
        if not results:
            return {
                "answer": "抱歉，知識庫中沒有找到相關的保險資訊。請嘗試換個關鍵字提問。",
                "sources": [],
                "suggested_questions": [],
                "model": _current_model,
            }
        context_parts = []
        for i, r in enumerate(results, 1):
            context_parts.append(
                f"[文章{i}] {r['title']}\n"
                f"地區: {r['region']} | 類別: {r['category']} | 日期: {r['date']}\n"
                f"摘要: {r['summary'][:800]}\n"
                f"關鍵字: {', '.join(r['keywords'])}\n"
            )
        context = "\n---\n".join(context_parts)
        history_text = ""
        if history:
            for h in history[-3:]:
                history_text += "用戶: " + h["question"] + "\n"
                history_text += "助手: " + h["answer"][:200] + "\n\n"
        history_block = "對話歷史:\n" + history_text if history_text else ""
        system_prompt = (
            "你是保險產業資訊分析助手。只根據提供的文章內容回答，不要捏造資訊。"
            "用中文回答，專業但易懂。引用具體的文章標題和數據。如果資料不足以回答，誠實說明。"
        )
        prompt = (
            f"知識庫相關文章：\n{context}\n\n"
            f"{history_block}\n\n"
            f"用戶問題：{question}\n\n"
            "請根據以上文章提供詳細、有依據的回答。\n\n"
            "回答完畢後，請在最後一行輸出 JSON 格式的 3 個建議延伸問題，格式如下：\n"
            "SUGGESTIONS: [\"問題1\", \"問題2\", \"問題3\"]\n"
            "建議問題要與用戶問題相關但角度不同，每個 15 字以內，用繁體中文。"
        )
        raw_answer = _call_ai(prompt, system_prompt)
        if not raw_answer:
            return {
                "answer": "AI 暫時無法回應，請稍後再試。",
                "sources": [
                    {"title": r["title"], "url": r["source_url"], "score": r["score"]}
                    for r in results
                ],
                "suggested_questions": [],
                "model": _current_model,
            }
        answer, suggested = self._parse_answer_and_suggestions(raw_answer)
        sources = [
            {"title": r["title"], "url": r["source_url"], "score": r["score"]}
            for r in results
        ]
        return {
            "answer": answer,
            "sources": sources,
            "suggested_questions": suggested,
            "model": _current_model,
        }

    def _parse_answer_and_suggestions(self, raw_answer):
        lines = raw_answer.strip().split("\n")
        suggestions = []
        answer_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("SUGGESTIONS:"):
                json_part = stripped[len("SUGGESTIONS:"):].strip()
                try:
                    parsed = json.loads(json_part)
                    if isinstance(parsed, list):
                        suggestions = [str(q) for q in parsed[:3]]
                except (json.JSONDecodeError, ValueError):
                    pass
            else:
                answer_lines.append(line)
        answer = "\n".join(answer_lines).strip()
        return answer, suggestions

    def get_stats(self):
        if not self.articles:
            return {"total": 0}
        regions = Counter(a.get("region", "未知") for a in self.articles)
        categories = Counter(a.get("category", "未知") for a in self.articles)
        dates = sorted(set(a.get("date", "") for a in self.articles))
        return {
            "total": len(self.articles),
            "terms": len(self.idf),
            "regions": dict(regions),
            "categories": dict(categories),
            "date_range": f"{dates[0]} ~ {dates[-1]}" if dates else "N/A",
        }
