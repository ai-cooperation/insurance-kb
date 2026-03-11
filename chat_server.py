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

from flask import Flask, jsonify, request, Response, send_from_directory

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# 載入 .env
env_path = Path("/home/ac-macmini2/world-monitor/.env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from src.rag_engine import RAGEngine, get_api_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("chat_server")

app = Flask(__name__)

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


@app.route("/api/chat", methods=["POST"])
def api_chat():
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
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "missing query param q"}), 400
    top_k = min(int(request.args.get("k", 5)), 20)
    rag = get_rag()
    results = rag.search(q, top_k=top_k)
    return jsonify({"query": q, "results": results})


@app.route("/api/stats", methods=["GET"])
def api_stats():
    rag = get_rag()
    return jsonify(rag.get_stats())


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(get_api_status())


@app.route("/api/reload", methods=["POST"])
def api_reload():
    global _rag
    with _rag_lock:
        _rag = None
    get_rag()
    return jsonify({"status": "reloaded", "stats": get_rag().get_stats()})


@app.route("/cards")
def cards_view():
    return send_from_directory(str(ROOT / "docs"), "index.html")


@app.route("/")
def chat_ui():
    return Response(CHAT_HTML, mimetype="text/html")


CHAT_HTML = r"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Insurance KB Chat</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
html, body { height:100%; overflow:hidden; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", sans-serif;
  background:#f0f2f5; display:flex; flex-direction:column; height:100vh;
}

.header {
  background: linear-gradient(135deg, #1a2980, #26d0ce);
  color: white; padding: 12px 16px;
  display: flex; align-items: center; justify-content: space-between;
  flex-shrink: 0; z-index: 10;
}
.header-left h1 { font-size: 18px; }
.header-left .stats { font-size: 12px; opacity: 0.85; margin-top: 2px; }
.header-right { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.header-right a { color: white; text-decoration: none; font-size: 12px; opacity: 0.8; }
.header-right a:hover { opacity: 1; }
.api-status {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: rgba(255,255,255,0.15);
}
.api-status .dot {
  display: inline-block; width: 6px; height: 6px;
  border-radius: 50%; margin-right: 4px; vertical-align: middle;
}
.api-status .dot.ok { background: #4caf50; }
.api-status .dot.warn { background: #ff9800; }
.api-status .dot.err { background: #f44336; }
.api-status .dot.unknown { background: #888; }

.chat-area {
  flex: 1; overflow-y: auto; padding: 16px;
  -webkit-overflow-scrolling: touch;
}
.chat-inner {
  max-width: 900px; width: 100%; margin: 0 auto;
}

.message { margin-bottom: 14px; display: flex; animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }
.message.user { justify-content: flex-end; }
.message.assistant { justify-content: flex-start; }

.bubble {
  max-width: 80%; padding: 10px 14px;
  border-radius: 16px; line-height: 1.6;
  font-size: 14px; white-space: pre-wrap; word-break: break-word;
}
.message.user .bubble {
  background: #1a2980; color: white; border-bottom-right-radius: 4px;
}
.message.assistant .bubble {
  background: white; color: #333; border-bottom-left-radius: 4px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.model-tag {
  font-size: 10px; color: #999; margin-top: 4px;
  text-align: left;
}

.sources {
  margin-top: 8px; padding-top: 8px;
  border-top: 1px solid #eee; font-size: 12px; color: #888;
}
.sources a { color: #1a73e8; text-decoration: none; display: block; margin: 2px 0; }
.sources a:hover { text-decoration: underline; }

.typing-indicator {
  display: none; padding: 10px 14px;
  background: white; border-radius: 16px; border-bottom-left-radius: 4px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1); max-width: 80px;
}
.typing-indicator.show { display: block; }
.typing-indicator span {
  display: inline-block; width: 7px; height: 7px;
  background: #ccc; border-radius: 50%;
  margin: 0 2px; animation: blink 1.4s infinite both;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink { 0%,80%,100% { opacity:0.3; } 40% { opacity:1; } }

.bottom-bar {
  flex-shrink: 0; background: white; border-top: 1px solid #e0e0e0;
  z-index: 10;
}

.suggestions-bar {
  max-width: 900px; width: 100%; margin: 0 auto;
  padding: 8px 16px; display: none; gap: 6px; flex-wrap: wrap;
}
.suggestions-bar button {
  padding: 6px 12px; background: #f5f5f5; border: 1px solid #ddd;
  border-radius: 14px; font-size: 12px; cursor: pointer;
  transition: all 0.2s; color: #555;
}
.suggestions-bar button:hover { border-color: #1a2980; color: #1a2980; }

.input-bar {
  max-width: 900px; width: 100%; margin: 0 auto;
  padding: 10px 16px; display: flex; gap: 8px;
}
.input-bar input {
  flex: 1; padding: 10px 14px; border: 2px solid #e0e0e0;
  border-radius: 22px; font-size: 15px; outline: none;
}
.input-bar input:focus { border-color: #1a2980; }
.input-bar button {
  padding: 10px 20px; background: #1a2980; color: white;
  border: none; border-radius: 22px; font-size: 15px;
  cursor: pointer; transition: background 0.2s;
}
.input-bar button:hover { background: #26d0ce; }
.input-bar button:disabled { background: #ccc; cursor: not-allowed; }

.welcome { text-align: center; padding: 50px 20px; color: #666; }
.welcome h2 { font-size: 22px; color: #333; margin-bottom: 8px; }
.welcome p { font-size: 14px; margin-bottom: 20px; }
.welcome-suggestions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.welcome-suggestions button {
  padding: 8px 14px; background: white; border: 1px solid #ddd;
  border-radius: 16px; font-size: 13px; cursor: pointer; color: #555;
}
.welcome-suggestions button:hover { border-color: #1a2980; color: #1a2980; }

@media (max-width: 640px) {
  .header { padding: 10px 12px; }
  .header-left h1 { font-size: 16px; }
  .chat-area { padding: 12px; }
  .bubble { max-width: 88%; font-size: 13px; }
  .input-bar { padding: 8px 12px; }
  .input-bar input { font-size: 14px; padding: 8px 12px; }
  .input-bar button { padding: 8px 16px; font-size: 14px; }
}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1>Insurance KB Chat</h1>
    <div class="stats" id="statsBar">Loading...</div>
  </div>
  <div class="header-right">
    <div class="api-status" id="apiStatus">
      <span class="dot unknown"></span> Checking...
    </div>
    <a href="https://insurance-kb.cooperation.tw/cards" target="_blank">Card View &#8599;</a>
  </div>
</div>

<div class="chat-area" id="chatArea">
  <div class="chat-inner" id="chatInner">
    <div class="welcome" id="welcome">
      <h2>Insurance Knowledge Base</h2>
      <p>Ask me anything about insurance industry news across Asia Pacific</p>
      <div class="welcome-suggestions">
        <button onclick="askSuggestion(this)">Singapore insurance market</button>
        <button onclick="askSuggestion(this)">Swiss Re latest news</button>
        <button onclick="askSuggestion(this)">Japan insurance regulation</button>
        <button onclick="askSuggestion(this)">ESG in insurance</button>
      </div>
    </div>
  </div>
</div>

<div class="bottom-bar">
  <div class="suggestions-bar" id="suggestions"></div>
  <div class="input-bar">
    <input type="text" id="questionInput" placeholder="Ask about insurance industry news..."
      onkeydown="if(event.key==='Enter'){event.preventDefault();sendMessage();}">
    <button id="sendBtn" onclick="sendMessage()">Send</button>
  </div>
</div>

<script>
const chatArea = document.getElementById('chatArea');
const chatInner = document.getElementById('chatInner');
const questionInput = document.getElementById('questionInput');
const sendBtn = document.getElementById('sendBtn');
const suggestionsBar = document.getElementById('suggestions');
const welcome = document.getElementById('welcome');
let history = [];
let isSending = false;
let lastModel = '';

// Load stats
fetch('/api/stats').then(r => r.json()).then(data => {
  document.getElementById('statsBar').textContent =
    data.total + ' articles | ' + (data.date_range || 'N/A');
}).catch(() => {
  document.getElementById('statsBar').textContent = 'Loading...';
});

// Load API status
function updateApiStatus() {
  fetch('/api/status').then(r => r.json()).then(data => {
    const el = document.getElementById('apiStatus');
    const model = data.model || 'standby';
    const status = data.status || 'unknown';
    let dotClass = 'unknown';
    let label = model;
    if (status === 'ok') {
      dotClass = 'ok';
    } else if (status === 'unavailable') {
      dotClass = 'err';
      label = 'Unavailable';
    }
    el.innerHTML = '<span class="dot ' + dotClass + '"></span> ' + label;
  }).catch(() => {});
}
updateApiStatus();
setInterval(updateApiStatus, 30000);

function askSuggestion(btn) {
  questionInput.value = btn.textContent;
  sendMessage();
}

function scrollToBottom() {
  chatArea.scrollTop = chatArea.scrollHeight;
}

function addMessage(role, content, sources, model) {
  if (welcome) welcome.style.display = 'none';

  const div = document.createElement('div');
  div.className = 'message ' + role;

  let bubbleContent = escapeHtml(content);

  if (role === 'assistant' && sources && sources.length > 0) {
    let srcHtml = '<div class="sources"><strong>References:</strong>';
    sources.forEach(function(s) {
      if (s.url && s.url !== '#') {
        srcHtml += '<a href="' + escapeHtml(s.url) + '" target="_blank">' + escapeHtml(s.title) + '</a>';
      } else {
        srcHtml += '<span>' + escapeHtml(s.title) + '</span>';
      }
    });
    srcHtml += '</div>';
    bubbleContent += srcHtml;
  }

  let html = '<div class="bubble">' + bubbleContent + '</div>';

  if (role === 'assistant' && model) {
    html += '<div class="model-tag">' + escapeHtml(model) + '</div>';
  }

  div.innerHTML = html;
  chatInner.appendChild(div);
  scrollToBottom();
}

function showTyping() {
  const div = document.createElement('div');
  div.className = 'message assistant';
  div.id = 'typing';
  div.innerHTML = '<div class="typing-indicator show"><span></span><span></span><span></span></div>';
  chatInner.appendChild(div);
  scrollToBottom();
}

function hideTyping() {
  const el = document.getElementById('typing');
  if (el) el.remove();
}

async function sendMessage() {
  if (isSending) return;
  const question = questionInput.value.trim();
  if (!question) return;

  isSending = true;
  questionInput.value = '';
  sendBtn.disabled = true;
  suggestionsBar.style.display = 'none';
  addMessage('user', question);
  showTyping();

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ question: question, history: history.slice(-3) }),
    });
    const data = await resp.json();
    hideTyping();

    if (data.error) {
      addMessage('assistant', 'Error: ' + data.error);
    } else {
      addMessage('assistant', data.answer, data.sources, data.model);
      history.push({ question: question, answer: data.answer });
      if (data.suggested_questions && data.suggested_questions.length > 0) {
        showSuggestions(data.suggested_questions);
      }
      updateApiStatus();
    }
  } catch (e) {
    hideTyping();
    addMessage('assistant', 'Connection error. Please try again.');
  }

  isSending = false;
  sendBtn.disabled = false;
  questionInput.focus();
}

function showSuggestions(questions) {
  suggestionsBar.innerHTML = questions.map(function(q) {
    return '<button onclick="askSuggestion(this)">' + escapeHtml(q) + '</button>';
  }).join('');
  suggestionsBar.style.display = 'flex';
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
    get_rag()
    app.run(host="0.0.0.0", port=port, debug=False)
