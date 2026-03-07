#!/usr/bin/env python3
"""
保險知識庫 RAG Chat Server
Flask API + 內嵌 Chat UI
"""

import json
import logging
import os
import sys
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, request, Response

# 設定路徑
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src.rag_engine import RAGEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("chat_server")

app = Flask(__name__)

# RAG 引擎（全局單例）
_rag = None
_rag_lock = Lock()


def get_rag():
    global _rag
    if _rag is None:
        with _rag_lock:
            if _rag is None:
                index_path = ROOT / "index" / "master-index.json"
                _rag = RAGEngine(index_path)
    return _rag


# ===== API =====

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """RAG 對話 API"""
    data = request.get_json()
    if not data or not data.get("question"):
        return jsonify({"error": "missing question"}), 400

    question = data["question"].strip()
    if len(question) > 500:
        return jsonify({"error": "question too long (max 500)"}), 400

    history = data.get("history", [])

    rag = get_rag()
    result = rag.chat(question, history)
    return jsonify(result)


@app.route("/api/search", methods=["GET"])
def api_search():
    """搜尋 API"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "missing query param q"}), 400

    top_k = min(int(request.args.get("k", 5)), 20)
    rag = get_rag()
    results = rag.search(q, top_k=top_k)
    return jsonify({"query": q, "results": results})


@app.route("/api/stats", methods=["GET"])
def api_stats():
    """知識庫統計"""
    rag = get_rag()
    return jsonify(rag.get_stats())


@app.route("/api/reload", methods=["POST"])
def api_reload():
    """重新載入索引"""
    global _rag
    with _rag_lock:
        _rag = None
    get_rag()
    return jsonify({"status": "reloaded", "stats": get_rag().get_stats()})


# ===== Chat UI =====

@app.route("/")
def chat_ui():
    """內嵌 Chat UI"""
    return Response(CHAT_HTML, mimetype="text/html")


CHAT_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Insurance KB Chat</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", sans-serif; background:#f0f2f5; height:100vh; display:flex; flex-direction:column; }

.header {
  background: linear-gradient(135deg, #1a2980, #26d0ce);
  color: white; padding: 16px 24px;
  display: flex; align-items: center; justify-content: space-between;
}
.header h1 { font-size: 20px; }
.header .stats { font-size: 13px; opacity: 0.85; }
.header a { color: white; text-decoration: none; font-size: 13px; opacity: 0.8; }
.header a:hover { opacity: 1; }

.chat-container {
  flex: 1; overflow-y: auto; padding: 20px;
  max-width: 900px; width: 100%; margin: 0 auto;
}

.message {
  margin-bottom: 16px; display: flex;
  animation: fadeIn 0.3s ease;
}
@keyframes fadeIn { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }

.message.user { justify-content: flex-end; }
.message.assistant { justify-content: flex-start; }

.bubble {
  max-width: 75%; padding: 12px 16px;
  border-radius: 16px; line-height: 1.7;
  font-size: 14px; white-space: pre-wrap;
  word-break: break-word;
}
.message.user .bubble {
  background: #1a2980; color: white;
  border-bottom-right-radius: 4px;
}
.message.assistant .bubble {
  background: white; color: #333;
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.sources {
  margin-top: 8px; padding-top: 8px;
  border-top: 1px solid #eee; font-size: 12px; color: #888;
}
.sources a {
  color: #1a73e8; text-decoration: none;
  display: block; margin: 2px 0;
}
.sources a:hover { text-decoration: underline; }

.typing-indicator {
  display: none; padding: 12px 16px;
  background: white; border-radius: 16px;
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  max-width: 80px;
}
.typing-indicator.show { display: block; }
.typing-indicator span {
  display: inline-block; width: 8px; height: 8px;
  background: #ccc; border-radius: 50%;
  margin: 0 2px; animation: blink 1.4s infinite both;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink { 0%,80%,100% { opacity:0.3; } 40% { opacity:1; } }

.input-area {
  background: white; border-top: 1px solid #e0e0e0;
  padding: 12px; display: flex; gap: 8px;
  max-width: 900px; width: 100%; margin: 0 auto;
}
.input-area input {
  flex: 1; padding: 12px 16px; border: 2px solid #e0e0e0;
  border-radius: 24px; font-size: 15px; outline: none;
}
.input-area input:focus { border-color: #1a2980; }
.input-area button {
  padding: 12px 24px; background: #1a2980; color: white;
  border: none; border-radius: 24px; font-size: 15px;
  cursor: pointer; transition: background 0.2s;
}
.input-area button:hover { background: #26d0ce; }
.input-area button:disabled { background: #ccc; cursor: not-allowed; }

.suggestions {
  max-width: 900px; width: 100%; margin: 0 auto;
  padding: 12px 20px; display: flex; gap: 8px; flex-wrap: wrap;
}
.suggestions button {
  padding: 8px 14px; background: white; border: 1px solid #ddd;
  border-radius: 16px; font-size: 13px; cursor: pointer;
  transition: all 0.2s; color: #555;
}
.suggestions button:hover { border-color: #1a2980; color: #1a2980; }

.welcome {
  text-align: center; padding: 60px 20px; color: #666;
}
.welcome h2 { font-size: 24px; color: #333; margin-bottom: 8px; }
.welcome p { font-size: 14px; margin-bottom: 24px; }
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>Insurance KB Chat</h1>
    <div class="stats" id="statsBar">Loading...</div>
  </div>
  <a href="https://cooperation.tw/insurance-kb/" target="_blank">Card View</a>
</div>

<div class="chat-container" id="chatContainer">
  <div class="welcome" id="welcome">
    <h2>Insurance Knowledge Base</h2>
    <p>Ask me anything about insurance industry news across Asia Pacific</p>
  </div>
</div>

<div class="suggestions" id="suggestions">
  <button onclick="askSuggestion(this)">Singapore insurance market trends</button>
  <button onclick="askSuggestion(this)">Swiss Re latest news</button>
  <button onclick="askSuggestion(this)">Japan insurance regulation changes</button>
  <button onclick="askSuggestion(this)">ESG in insurance industry</button>
  <button onclick="askSuggestion(this)">China Ping An financial report</button>
</div>

<div class="input-area">
  <input type="text" id="questionInput" placeholder="Ask about insurance industry news..."
    onkeydown="if(event.key==='Enter')sendMessage()">
  <button id="sendBtn" onclick="sendMessage()">Send</button>
</div>

<script>
const chatContainer = document.getElementById('chatContainer');
const questionInput = document.getElementById('questionInput');
const sendBtn = document.getElementById('sendBtn');
const suggestions = document.getElementById('suggestions');
const welcome = document.getElementById('welcome');
let history = [];

// Load stats
fetch('/api/stats').then(r => r.json()).then(data => {
  document.getElementById('statsBar').textContent =
    `${data.total} articles | ${data.date_range || 'N/A'} | ${Object.keys(data.regions||{}).length} regions`;
}).catch(() => {
  document.getElementById('statsBar').textContent = 'Knowledge base loading...';
});

function askSuggestion(btn) {
  questionInput.value = btn.textContent;
  sendMessage();
}

function addMessage(role, content, sources) {
  if (welcome) welcome.style.display = 'none';
  if (suggestions) suggestions.style.display = 'none';

  const div = document.createElement('div');
  div.className = 'message ' + role;

  let html = '<div class="bubble">' + escapeHtml(content) + '</div>';

  if (role === 'assistant' && sources && sources.length > 0) {
    let srcHtml = '<div class="sources"><strong>Sources:</strong>';
    sources.forEach(s => {
      if (s.url && s.url !== '#') {
        srcHtml += '<a href="' + escapeHtml(s.url) + '" target="_blank">' + escapeHtml(s.title) + '</a>';
      } else {
        srcHtml += '<span>' + escapeHtml(s.title) + '</span>';
      }
    });
    srcHtml += '</div>';
    html = '<div class="bubble">' + escapeHtml(content) + srcHtml + '</div>';
  }

  div.innerHTML = html;
  chatContainer.appendChild(div);
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function showTyping() {
  const div = document.createElement('div');
  div.className = 'message assistant';
  div.id = 'typing';
  div.innerHTML = '<div class="typing-indicator show"><span></span><span></span><span></span></div>';
  chatContainer.appendChild(div);
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function hideTyping() {
  const el = document.getElementById('typing');
  if (el) el.remove();
}

async function sendMessage() {
  const question = questionInput.value.trim();
  if (!question) return;

  questionInput.value = '';
  sendBtn.disabled = true;
  addMessage('user', question);
  showTyping();

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ question, history: history.slice(-3) }),
    });
    const data = await resp.json();
    hideTyping();

    if (data.error) {
      addMessage('assistant', 'Error: ' + data.error);
    } else {
      addMessage('assistant', data.answer, data.sources);
      history.push({ question, answer: data.answer });
    }
  } catch (e) {
    hideTyping();
    addMessage('assistant', 'Connection error. Please try again.');
  }

  sendBtn.disabled = false;
  questionInput.focus();
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("CHAT_PORT", 5000))
    logger.info(f"Starting chat server on port {port}")

    # Pre-load RAG engine
    get_rag()

    app.run(host="0.0.0.0", port=port, debug=False)
