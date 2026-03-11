#!/usr/bin/env python3
"""
保險知識庫 RAG Chat Server
Flask API + 內嵌 Chat UI（含對話紀錄）
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
from src.chat_history import (
    init_db, create_session, save_message,
    get_sessions, get_messages, delete_session,
)

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


# --------------- Chat API ---------------

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json()
    if not data or not data.get("question"):
        return jsonify({"error": "missing question"}), 400
    question = data["question"].strip()
    if len(question) > 500:
        return jsonify({"error": "question too long (max 500)"}), 400

    user_id = data.get("user_id", "anonymous")
    session_id = data.get("session_id", "")

    # 自動建 session
    if not session_id:
        session_id = create_session(user_id)

    history = data.get("history", [])
    rag = get_rag()
    result = rag.chat(question, history)

    # 存對話紀錄
    save_message(session_id, "user", question)
    save_message(
        session_id, "assistant", result.get("answer", ""),
        sources=result.get("sources"), model=result.get("model", ""),
    )

    result["session_id"] = session_id
    return jsonify(result)


# --------------- Session API ---------------

@app.route("/api/sessions", methods=["GET"])
def api_sessions():
    user_id = request.args.get("user_id", "anonymous")
    return jsonify(get_sessions(user_id))


@app.route("/api/sessions/<session_id>/messages", methods=["GET"])
def api_session_messages(session_id):
    return jsonify(get_messages(session_id))


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    user_id = request.args.get("user_id", "anonymous")
    ok = delete_session(session_id, user_id)
    if ok:
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not found or not owner"}), 404


# --------------- Existing APIs ---------------

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


# --------------- Chat UI HTML ---------------

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
  background:#f0f2f5; display:flex; height:100vh;
}

/* ---- Sidebar ---- */
.sidebar {
  width: 260px; background: #1e1e2e; color: #cdd6f4;
  display: flex; flex-direction: column; flex-shrink: 0;
  transition: transform 0.3s ease;
  z-index: 20;
}
.sidebar.hidden { transform: translateX(-260px); position: absolute; height: 100%; }
.sidebar-header {
  padding: 14px 16px; border-bottom: 1px solid #313244;
  display: flex; align-items: center; justify-content: space-between;
}
.sidebar-header h2 { font-size: 14px; color: #cdd6f4; font-weight: 600; }
.btn-new-chat {
  padding: 6px 12px; background: #89b4fa; color: #1e1e2e;
  border: none; border-radius: 6px; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: background 0.2s;
}
.btn-new-chat:hover { background: #74c7ec; }
.session-list { flex: 1; overflow-y: auto; padding: 8px; }
.session-item {
  padding: 10px 12px; margin-bottom: 4px; border-radius: 8px;
  cursor: pointer; display: flex; align-items: center; justify-content: space-between;
  transition: background 0.15s; font-size: 13px;
}
.session-item:hover { background: #313244; }
.session-item.active { background: #45475a; }
.session-item .title {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.session-item .date {
  font-size: 10px; color: #6c7086; margin-left: 8px; flex-shrink: 0;
}
.session-item .btn-delete {
  display: none; background: none; border: none; color: #f38ba8;
  cursor: pointer; font-size: 14px; padding: 2px 6px; margin-left: 4px;
  flex-shrink: 0; border-radius: 4px;
}
.session-item:hover .btn-delete { display: block; }
.session-item .btn-delete:hover { background: rgba(243,139,168,0.15); }
.sidebar-footer {
  padding: 10px 16px; border-top: 1px solid #313244;
  font-size: 11px; color: #6c7086; text-align: center;
}
.empty-sessions {
  text-align: center; padding: 30px 16px; color: #6c7086; font-size: 13px;
}

/* ---- Main area ---- */
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }

.header {
  background: linear-gradient(135deg, #1a2980, #26d0ce);
  color: white; padding: 12px 16px;
  display: flex; align-items: center; justify-content: space-between;
  flex-shrink: 0; z-index: 10;
}
.header-left { display: flex; align-items: center; gap: 10px; }
.btn-toggle-sidebar {
  background: none; border: none; color: white; font-size: 20px;
  cursor: pointer; padding: 4px 8px; border-radius: 4px;
}
.btn-toggle-sidebar:hover { background: rgba(255,255,255,0.15); }
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
.chat-inner { max-width: 900px; width: 100%; margin: 0 auto; }

.message { margin-bottom: 14px; display: flex; animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }
.message.user { justify-content: flex-end; }
.message.assistant { justify-content: flex-start; flex-direction: column; align-items: flex-start; }

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
.model-tag { font-size: 10px; color: #999; margin-top: 4px; }
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
  flex-shrink: 0; background: white; border-top: 1px solid #e0e0e0; z-index: 10;
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

/* ---- Overlay & Dialog ---- */
.sidebar-overlay {
  display: none; position: fixed; top: 0; left: 0;
  width: 100%; height: 100%; background: rgba(0,0,0,0.4); z-index: 15;
}
.sidebar-overlay.show { display: block; }

.confirm-overlay {
  display: none; position: fixed; top: 0; left: 0;
  width: 100%; height: 100%; background: rgba(0,0,0,0.5);
  z-index: 100; align-items: center; justify-content: center;
}
.confirm-overlay.show { display: flex; }
.confirm-box {
  background: white; padding: 24px; border-radius: 12px;
  max-width: 360px; width: 90%; text-align: center;
  box-shadow: 0 8px 30px rgba(0,0,0,0.2);
}
.confirm-box p { margin-bottom: 16px; font-size: 14px; color: #333; }
.confirm-box .btn-row { display: flex; gap: 10px; justify-content: center; }
.confirm-box button {
  padding: 8px 20px; border-radius: 8px; border: none; font-size: 14px; cursor: pointer;
}
.btn-cancel { background: #e0e0e0; color: #333; }
.btn-cancel:hover { background: #d0d0d0; }
.btn-confirm-delete { background: #f38ba8; color: white; }
.btn-confirm-delete:hover { background: #e06080; }

@media (max-width: 768px) {
  .sidebar { position: absolute; height: 100%; }
  .sidebar.hidden { transform: translateX(-260px); }
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

<div class="sidebar-overlay" id="sidebarOverlay" onclick="toggleSidebar()"></div>

<div class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <h2>Chat History</h2>
    <button class="btn-new-chat" onclick="newChat()">+ New</button>
  </div>
  <div class="session-list" id="sessionList">
    <div class="empty-sessions">No conversations yet</div>
  </div>
  <div class="sidebar-footer">Insurance KB</div>
</div>

<div class="main">
  <div class="header">
    <div class="header-left">
      <button class="btn-toggle-sidebar" onclick="toggleSidebar()">&#9776;</button>
      <div>
        <h1>Insurance KB Chat</h1>
        <div class="stats" id="statsBar">Loading...</div>
      </div>
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
</div>

<div class="confirm-overlay" id="confirmOverlay">
  <div class="confirm-box">
    <p>Delete this conversation?</p>
    <div class="btn-row">
      <button class="btn-cancel" onclick="closeConfirm()">Cancel</button>
      <button class="btn-confirm-delete" id="btnConfirmDelete">Delete</button>
    </div>
  </div>
</div>

<script>
const chatArea = document.getElementById('chatArea');
const chatInner = document.getElementById('chatInner');
const questionInput = document.getElementById('questionInput');
const sendBtn = document.getElementById('sendBtn');
const suggestionsBar = document.getElementById('suggestions');
const welcomeEl = document.getElementById('welcome');
const sessionListEl = document.getElementById('sessionList');
const sidebar = document.getElementById('sidebar');
const sidebarOverlay = document.getElementById('sidebarOverlay');

let history = [];
let isSending = false;
let currentSessionId = '';
let deleteTargetId = '';

// ---- User ID ----
function getUserId() {
  let uid = localStorage.getItem('ikb_user_id');
  if (!uid) {
    uid = 'u_' + crypto.randomUUID().replace(/-/g, '').slice(0, 16);
    localStorage.setItem('ikb_user_id', uid);
  }
  return uid;
}
const userId = getUserId();

// ---- Sidebar toggle ----
function toggleSidebar() {
  sidebar.classList.toggle('hidden');
  sidebarOverlay.classList.toggle('show', !sidebar.classList.contains('hidden'));
}
if (window.innerWidth <= 768) sidebar.classList.add('hidden');

// ---- Sessions ----
async function loadSessions() {
  try {
    const resp = await fetch('/api/sessions?user_id=' + encodeURIComponent(userId));
    const sessions = await resp.json();
    if (!sessions.length) {
      sessionListEl.innerHTML = '<div class="empty-sessions">No conversations yet</div>';
      return;
    }
    sessionListEl.innerHTML = sessions.map(function(s) {
      const active = s.id === currentSessionId ? ' active' : '';
      const title = escapeHtml(s.title || 'New conversation');
      const date = (s.updated_at || '').slice(5, 16);
      return '<div class="session-item' + active + '" onclick="loadSession(\'' + s.id + '\')">'
        + '<span class="title">' + title + '</span>'
        + '<span class="date">' + date + '</span>'
        + '<button class="btn-delete" onclick="event.stopPropagation();confirmDelete(\'' + s.id + '\')" title="Delete">&#128465;</button>'
        + '</div>';
    }).join('');
  } catch(e) {}
}

async function loadSession(sessionId) {
  currentSessionId = sessionId;
  history = [];
  chatInner.innerHTML = '';
  suggestionsBar.style.display = 'none';
  try {
    const resp = await fetch('/api/sessions/' + sessionId + '/messages');
    const msgs = await resp.json();
    msgs.forEach(function(m) {
      addMessage(m.role, m.content, m.sources, m.model, true);
      if (m.role === 'user') {
        history.push({ question: m.content, answer: '' });
      } else if (m.role === 'assistant' && history.length > 0) {
        history[history.length - 1].answer = m.content;
      }
    });
  } catch(e) {
    addMessage('assistant', 'Failed to load conversation.', null, null, true);
  }
  loadSessions();
  if (window.innerWidth <= 768) toggleSidebar();
}

function newChat() {
  currentSessionId = '';
  history = [];
  chatInner.innerHTML = '';
  suggestionsBar.style.display = 'none';
  // Re-show welcome
  const w = document.createElement('div');
  w.className = 'welcome';
  w.id = 'welcome';
  w.innerHTML = '<h2>Insurance Knowledge Base</h2>'
    + '<p>Ask me anything about insurance industry news across Asia Pacific</p>'
    + '<div class="welcome-suggestions">'
    + '<button onclick="askSuggestion(this)">Singapore insurance market</button>'
    + '<button onclick="askSuggestion(this)">Swiss Re latest news</button>'
    + '<button onclick="askSuggestion(this)">Japan insurance regulation</button>'
    + '<button onclick="askSuggestion(this)">ESG in insurance</button>'
    + '</div>';
  chatInner.appendChild(w);
  loadSessions();
  if (window.innerWidth <= 768) toggleSidebar();
  questionInput.focus();
}

// ---- Delete ----
function confirmDelete(sessionId) {
  deleteTargetId = sessionId;
  document.getElementById('confirmOverlay').classList.add('show');
}
function closeConfirm() {
  deleteTargetId = '';
  document.getElementById('confirmOverlay').classList.remove('show');
}
document.getElementById('btnConfirmDelete').onclick = async function() {
  if (!deleteTargetId) return;
  try {
    await fetch('/api/sessions/' + deleteTargetId + '?user_id=' + encodeURIComponent(userId), { method: 'DELETE' });
    if (deleteTargetId === currentSessionId) newChat();
    loadSessions();
  } catch(e) {}
  closeConfirm();
};

// ---- Stats & API Status ----
fetch('/api/stats').then(function(r){ return r.json(); }).then(function(data){
  document.getElementById('statsBar').textContent = data.total + ' articles | ' + (data.date_range || 'N/A');
}).catch(function(){});

function updateApiStatus() {
  fetch('/api/status').then(function(r){ return r.json(); }).then(function(data){
    var el = document.getElementById('apiStatus');
    var model = data.model || 'standby';
    var status = data.status || 'unknown';
    var dotClass = 'unknown';
    if (status === 'ok') dotClass = 'ok';
    else if (status === 'unavailable') dotClass = 'err';
    el.innerHTML = '<span class="dot ' + dotClass + '"></span> ' + (dotClass === 'err' ? 'Unavailable' : model);
  }).catch(function(){});
}
updateApiStatus();
setInterval(updateApiStatus, 30000);

// ---- Chat ----
function askSuggestion(btn) {
  questionInput.value = btn.textContent;
  sendMessage();
}

function scrollToBottom() {
  chatArea.scrollTop = chatArea.scrollHeight;
}

function addMessage(role, content, sources, model, noAnim) {
  var w = document.getElementById('welcome');
  if (w) w.style.display = 'none';
  var div = document.createElement('div');
  div.className = 'message ' + role;
  if (noAnim) div.style.animation = 'none';

  var bubbleContent = escapeHtml(content);
  if (role === 'assistant' && sources && sources.length > 0) {
    var srcHtml = '<div class="sources"><strong>References:</strong>';
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

  var html = '<div class="bubble">' + bubbleContent + '</div>';
  if (role === 'assistant' && model) {
    html += '<div class="model-tag">' + escapeHtml(model) + '</div>';
  }
  div.innerHTML = html;
  chatInner.appendChild(div);
  scrollToBottom();
}

function showTyping() {
  var div = document.createElement('div');
  div.className = 'message assistant';
  div.id = 'typing';
  div.innerHTML = '<div class="typing-indicator show"><span></span><span></span><span></span></div>';
  chatInner.appendChild(div);
  scrollToBottom();
}
function hideTyping() {
  var el = document.getElementById('typing');
  if (el) el.remove();
}

async function sendMessage() {
  if (isSending) return;
  var question = questionInput.value.trim();
  if (!question) return;

  isSending = true;
  questionInput.value = '';
  sendBtn.disabled = true;
  suggestionsBar.style.display = 'none';
  addMessage('user', question);
  showTyping();

  try {
    var resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        question: question,
        history: history.slice(-3),
        user_id: userId,
        session_id: currentSessionId,
      }),
    });
    var data = await resp.json();
    hideTyping();

    if (data.error) {
      addMessage('assistant', 'Error: ' + data.error);
    } else {
      addMessage('assistant', data.answer, data.sources, data.model);
      history.push({ question: question, answer: data.answer });
      if (data.session_id && !currentSessionId) {
        currentSessionId = data.session_id;
      }
      loadSessions();
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
  var d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

// ---- Init ----
loadSessions();
questionInput.focus();
</script>
</body>
</html>"""


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("CHAT_PORT", 5000))
    logger.info(f"Starting chat server on port {port}")
    get_rag()
    app.run(host="0.0.0.0", port=port, debug=False)
