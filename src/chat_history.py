"""
對話紀錄 SQLite 儲存層
- 現階段用瀏覽器 UUID 識別用戶
- 未來接入帳號系統時，只需 UPDATE sessions SET user_id = 新ID WHERE user_id = 舊UUID
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "chat.db"
TZ_UTC8 = timezone(timedelta(hours=8))


def _now():
    return datetime.now(TZ_UTC8).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """建立資料表（冪等）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                title       TEXT DEFAULT '',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user
                ON sessions(user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role        TEXT NOT NULL CHECK(role IN ('user','assistant')),
                content     TEXT NOT NULL,
                sources     TEXT DEFAULT '[]',
                model       TEXT DEFAULT '',
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, created_at);
        """)


def create_session(user_id):
    """建立新 session，回傳 session_id"""
    session_id = uuid.uuid4().hex[:16]
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (session_id, user_id, "", now, now),
        )
    return session_id


def save_message(session_id, role, content, sources=None, model=""):
    """儲存一則訊息，同時更新 session 標題（取第一則 user 訊息）和 updated_at"""
    now = _now()
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, sources, model, created_at) VALUES (?,?,?,?,?,?)",
            (session_id, role, content, sources_json, model, now),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        # 自動設 title = 第一則 user 訊息前 50 字
        row = conn.execute(
            "SELECT title FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row and not row["title"] and role == "user":
            title = content[:50].replace("\n", " ")
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )


def get_sessions(user_id, limit=50):
    """取得用戶的 session 列表（最近優先）"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions "
            "WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_messages(session_id):
    """取得某 session 的所有訊息"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, sources, model, created_at FROM messages "
            "WHERE session_id = ? ORDER BY created_at, id",
            (session_id,),
        ).fetchall()
        result = []
        for r in rows:
            msg = dict(r)
            msg["sources"] = json.loads(msg["sources"]) if msg["sources"] else []
            result.append(msg)
        return result


def delete_session(session_id, user_id):
    """刪除 session（須驗證 user_id 擁有權）。回傳是否有刪除。"""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        return cur.rowcount > 0


def get_session_owner(session_id):
    """取得 session 的 user_id，用於驗證"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row["user_id"] if row else None
