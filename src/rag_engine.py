"""
RAG 引擎 — TF-IDF 搜尋 + Gemini CLI 對話
從 master-index.json 載入知識庫，用 TF-IDF 找相關文章，
組成 context 餵給 Gemini CLI 回答用戶問題
"""

import json
import logging
import math
import re
import subprocess
from collections import Counter
from pathlib import Path

logger = logging.getLogger("rag_engine")

# 中文停用詞（高頻但無意義的詞）
STOP_WORDS = set(
    "的 了 在 是 我 有 和 就 不 人 都 一 一個 上 也 很 到 說 要 去 你 會 著 沒有 看 好 "
    "自己 這 他 她 它 們 那 裡 為 什麼 麼 吧 嗎 呢 把 被 讓 給 從 向 對 跟 以 但 而 "
    "或 如果 因為 所以 雖然 但是 然後 可以 已經 還 又 再 更 最 這個 那個 "
    "the a an and or but in on at to for of is are was were be been has have had "
    "with from by this that it its as not will can do does did may might shall should would could".split()
)


class RAGEngine:
    """TF-IDF 搜尋 + Gemini CLI 對話引擎"""

    def __init__(self, index_path):
        self.index_path = Path(index_path)
        self.articles = []
        self.doc_tokens = []  # 每篇文章的 token list
        self.idf = {}         # 全局 IDF
        self.doc_tfidf = []   # 每篇文章的 TF-IDF 向量
        self._load_and_index()

    def _load_and_index(self):
        """載入 master-index.json 並建立 TF-IDF 索引"""
        if not self.index_path.exists():
            logger.error(f"Index not found: {self.index_path}")
            return

        self.articles = json.loads(self.index_path.read_text(encoding="utf-8"))
        logger.info(f"Loaded {len(self.articles)} articles")

        # Tokenize 每篇文章
        for article in self.articles:
            text = self._article_to_text(article)
            tokens = self._tokenize(text)
            self.doc_tokens.append(tokens)

        # 計算 IDF
        n = len(self.articles)
        df = Counter()
        for tokens in self.doc_tokens:
            unique_tokens = set(tokens)
            for t in unique_tokens:
                df[t] += 1

        self.idf = {t: math.log(n / (1 + count)) for t, count in df.items()}

        # 計算每篇的 TF-IDF 向量
        for tokens in self.doc_tokens:
            tf = Counter(tokens)
            total = len(tokens) if tokens else 1
            tfidf = {}
            for t, count in tf.items():
                tfidf[t] = (count / total) * self.idf.get(t, 0)
            self.doc_tfidf.append(tfidf)

        logger.info(f"TF-IDF index built: {len(self.idf)} terms")

    def _article_to_text(self, article):
        """文章欄位合併為可搜尋文字"""
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
        """簡單分詞：中文按字元 bigram，英文按空格"""
        text = text.lower().strip()
        tokens = []

        # 英文單詞
        eng_words = re.findall(r'[a-z][a-z0-9]+', text)
        tokens.extend(w for w in eng_words if w not in STOP_WORDS and len(w) > 1)

        # 中文 bigram
        chinese = re.findall(r'[\u4e00-\u9fff]+', text)
        for seg in chinese:
            for i in range(len(seg) - 1):
                bigram = seg[i:i+2]
                if bigram not in STOP_WORDS:
                    tokens.append(bigram)
            # 也加單字（用於短查詢匹配）
            for c in seg:
                if c not in STOP_WORDS:
                    tokens.append(c)

        return tokens

    def search(self, query, top_k=5):
        """TF-IDF 相似度搜尋，回傳最相關的 top_k 篇文章"""
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        # Query TF-IDF
        qtf = Counter(query_tokens)
        total = len(query_tokens)
        query_tfidf = {}
        for t, count in qtf.items():
            query_tfidf[t] = (count / total) * self.idf.get(t, 0)

        # Cosine similarity
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
        """RAG 對話：搜尋相關文章 → 組成 context → Gemini CLI 回答"""
        # 搜尋相關文章
        results = self.search(question, top_k=5)

        if not results:
            return {
                "answer": "抱歉，知識庫中沒有找到相關的保險資訊。請嘗試換個關鍵字提問。",
                "sources": [],
            }

        # 組成 context
        context_parts = []
        for i, r in enumerate(results, 1):
            context_parts.append(
                f"[文章{i}] {r['title']}\n"
                f"地區: {r['region']} | 類別: {r['category']} | 日期: {r['date']}\n"
                f"摘要: {r['summary'][:800]}\n"
                f"關鍵字: {', '.join(r['keywords'])}\n"
            )

        context = "\n---\n".join(context_parts)

        # 對話歷史
        history_text = ""
        if history:
            for h in history[-3:]:  # 最近 3 輪
                history_text += f"用戶: {h['question']}\n助手: {h['answer'][:200]}\n\n"

        prompt = f"""你是保險產業資訊分析助手。根據以下知識庫中的相關文章回答用戶問題。

規則：
1. 只根據提供的文章內容回答，不要捏造資訊
2. 用中文回答，專業但易懂
3. 引用具體的文章標題和數據
4. 如果資料不足以回答，誠實說明

知識庫相關文章：
{context}

{f"對話歷史：{chr(10)}{history_text}" if history_text else ""}

用戶問題：{question}

請提供詳細、有依據的回答："""

        # 呼叫 Gemini CLI
        try:
            result = subprocess.run(
                ["gemini", "-p", prompt],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                answer = f"AI 回應失敗：{result.stderr[:200]}"
            else:
                answer = result.stdout.strip()
        except subprocess.TimeoutExpired:
            answer = "AI 回應超時，請稍後再試。"
        except Exception as e:
            answer = f"AI 錯誤：{str(e)[:200]}"

        sources = [
            {"title": r["title"], "url": r["source_url"], "score": r["score"]}
            for r in results[:3]
        ]

        return {"answer": answer, "sources": sources}

    def get_stats(self):
        """回傳知識庫統計"""
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
