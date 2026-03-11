# Insurance KB Pro 版改版規格書

> 狀態：規劃中（v1 穩定運行後啟動）
> 建立日期：2026-03-11

---

## 一、改版目標

將現有系統從「新聞爬取 + 展示」升級為具備**來源品質追蹤**、**AI 可信度量化**、**自動補漏**能力的完整知識庫平台。

核心價值：**每一筆資料都可追溯來源、可驗證品質、可量化可信度。**

---

## 二、新增來源：Gemini 週報

### 2.1 功能說明
透過 MCP `llm_chat`（provider=gemini）定期產出國際壽險週報，與現有知識庫比對找出覆蓋缺口，自動補漏。

### 2.2 工作流程
```
┌──────────────────────────────────────────────────┐
│  1. gap_report.py --export                       │
│     匯出本週 KB 已有文章摘要                       │
│                                                  │
│  2. MCP llm_chat (Gemini)                        │
│     依 prompt 模板產出週報（含 URL）                │
│                                                  │
│  3. gap_report.py --compare report.md            │
│     模糊比對標題/關鍵字/公司名，找出遺漏             │
│                                                  │
│  4. gap_report.py --fill（Pro 版新增）             │
│     ├── 驗證遺漏項目的 URL（HTTP HEAD）             │
│     ├── URL 有效 → 餵入 crawler 抓取 + AI 分類     │
│     ├── URL 無效 → 標記為 Gemini 幻覺              │
│     └── 結果寫入 source_tracker                    │
└──────────────────────────────────────────────────┘
```

### 2.3 Gemini Prompt 模板
已設計完成（見 gap_report.py 註解），重點規格：
- 地區優先：香港、新加坡、中國、日本、韓國
- 第一部分：商品/服務動態（8-15 則）
- 第二部分：產業議題（6-10 則）
- 來源偏好：官網 Press Release > 產業媒體 > 權威媒體 > 信評/再保 > 四大
- 嚴格要求：不捏造、附超連結、去重

---

## 三、來源品質追蹤系統

### 3.1 架構

```
src/source_tracker.py            ← 統一追蹤模組（已建立骨架）
├── record(event_type, source, result, ...)   寫入事件
├── load_events(days, type, source)           查詢事件
├── get_stats(days, type, source)             聚合統計
├── cleanup_log()                             清理損壞行
└── CLI: python3 -m src.source_tracker --stats

data/source_tracking.jsonl       ← 累積日誌（append-only, 永久保留）
```

### 3.2 五個追蹤維度

| 維度 | event_type | 寫入來源 | 追蹤目標 | result 值 |
|------|-----------|---------|---------|-----------|
| 爬取品質 | `crawl` | crawler.py | 來源可用性 | ok / timeout / 404 / encoding_error / empty |
| 內容相關性 | `relevance` | content_filter.py | 來源信噪比 | relevant / filtered:原因 |
| AI 分類品質 | `ai_classify` | ai_processor.py | 模型準確度 | ok / fixed:欄位 / failed / fallback:model |
| AI 過濾品質 | `ai_filter` | content_filter.py | 過濾判斷準確度 | ok / error:誤判 |
| Gemini 真實性 | `gemini_verify` | gap_report.py | Gemini 幻覺率 | real / hallucinated / 404 / mismatch |

### 3.3 每筆事件結構（JSONL）

```json
{
  "ts": "2026-03-11 09:30:00",
  "type": "ai_classify",
  "source": "GNews: 亞洲保險產業",
  "result": "fallback",
  "detail": "primary model rate limited",
  "url": "https://...",
  "model": "llama-3.1-8b-instant",
  "uid": "6f1d82909254"
}
```

### 3.4 各元件接入方式

#### crawler.py
```python
from src.source_tracker import record as track_event

# 在 crawl_source() 回傳前
track_event(
    event_type="crawl",
    source=source["id"],
    result=health["status"],       # ok / error
    detail=health.get("error", f"{len(results)} articles"),
    url=source["url"],
)
```

#### content_filter.py
```python
from src.source_tracker import record as track_event

# 每篇文章 AI 判斷後
track_event(
    event_type="relevance",
    source=article["source"],
    result="relevant" if is_relevant else f"filtered:{reason}",
    uid=article["uid"],
    model="llama-3.3-70b-versatile",
)

# AI 過濾品質（人工回報誤判時）
track_event(
    event_type="ai_filter",
    source=article["source"],
    result="error:false_positive",  # 被誤過濾
    uid=article["uid"],
)
```

#### ai_processor.py
```python
from src.source_tracker import record as track_event

# 每篇文章 AI 分類後
track_event(
    event_type="ai_classify",
    source=article_source,
    result="ok",                    # or "fallback" / "fixed" / "failed"
    model=used_model,
    uid=article_uid,
    detail="category: 產品創新",
)
```

#### gap_report.py --fill（新增）
```python
from src.source_tracker import record as track_event

# 驗證 Gemini 給的 URL
track_event(
    event_type="gemini_verify",
    source="gemini_weekly_report",
    result="real",                  # or "hallucinated" / "404" / "mismatch"
    url=gemini_url,
    detail=f"title: {title}",
)
```

### 3.5 關鍵設計決策

| 問題 | 決策 | 原因 |
|------|------|------|
| 被過濾的文章算錯誤嗎？ | **不算**，記為 `relevance:filtered` | 來源信噪比指標，不是錯誤率 |
| AI fallback 算錯誤嗎？ | **算 warning**，記為 `fallback:model` | 代表 primary model 不可用，品質可能下降 |
| 日誌要輪替嗎？ | **永久保留** | 每天幾百行，一年幾 MB，長期趨勢分析價值 > 儲存成本 |
| 人工修正怎麼記？ | 寫入 `ai_filter:error:誤判類型` | 需要前端或 CLI 提供「標記誤判」入口 |

---

## 四、統計報告與視覺化

### 4.1 CLI 報告
```bash
# 全維度統計（最近 30 天）
python3 src/source_tracker.py --stats

# 指定維度
python3 src/source_tracker.py --stats --type crawl --days 7

# 指定來源
python3 src/source_tracker.py --stats --source "GNews"

# JSON 輸出（供前端或 TG Bot 使用）
python3 src/source_tracker.py --stats --json
```

### 4.2 輸出範例
```
============================================================
來源品質追蹤報告（最近 30 天）
總事件數: 12,345
============================================================

按追蹤維度:
──────────────────────────────────────────────────────────────
  [crawl] 共 2700 筆, 成功率 99.2%
    ok                   2678  ████████████████████████████
    timeout                12  
    404                    10  

  [relevance] 共 2500 筆, 信噪比 78.4%
    relevant             1960  ████████████████████████
    filtered:一般新聞      340  ████
    filtered:體育           200  ██

  [ai_classify] 共 2500 筆, 成功率 94.0%
    ok                   2350  ███████████████████████
    fixed:category         80  █
    fallback:8b-instant    60  
    failed                 10  

  [ai_filter] 共 50 筆（人工回報）
    ok                     42  ████████████████████
    error:false_positive    8  ████

  [gemini_verify] 共 56 筆, 真實率 71.4%
    real                   40  ████████████████████
    hallucinated           10  █████
    404                     6  ███

AI 模型品質:
──────────────────────────────────────────────────────────────
  [llama-3.3-70b-versatile] 共 2400 筆, 成功率 95.8%
  [llama-3.1-8b-instant] 共 100 筆, 成功率 82.0%（fallback）

需要關注的來源（成功率 < 90%）:
──────────────────────────────────────────────────────────────
  GNews: 日本體育: 23.5% (340 筆) ← 信噪比極低，考慮移除
  Ping An Press: 87.5% (40 筆) ← 偶爾亂碼
```

### 4.3 未來可擴展
- **Chat UI 儀表板頁面** (`/dashboard`)：視覺化追蹤數據
- **TG Bot 指令** (`/quality`)：推送品質報告
- **自動警報**：某來源連續 N 次失敗時 TG 通知

---

## 五、帳號系統（Phase 2）

### 5.1 現有基礎
- `sessions.user_id` 已預埋（目前用 localStorage UUID）
- 遷移路徑：`UPDATE sessions SET user_id = 帳號ID WHERE user_id = 瀏覽器UUID`

### 5.2 帳號功能規劃
```sql
CREATE TABLE users (
    id           TEXT PRIMARY KEY,
    username     TEXT UNIQUE NOT NULL,
    display_name TEXT,
    password_hash TEXT NOT NULL,
    role         TEXT DEFAULT 'user',    -- user / admin
    created_at   TEXT NOT NULL,
    last_login   TEXT
);
```

- 登入/註冊 API
- Admin 角色可查看所有 session、品質報告
- 匿名用戶可在登入後遷移舊對話

---

## 六、實施優先順序

| 階段 | 功能 | 前置條件 |
|------|------|---------|
| **Pro v1** | source_tracker 接入 crawler + content_filter | v1 穩定運行 |
| **Pro v2** | source_tracker 接入 ai_processor + verifier | Pro v1 |
| **Pro v3** | gap_report --fill 自動補漏 + gemini_verify | Pro v2 |
| **Pro v4** | 品質儀表板 + TG Bot /quality 指令 | Pro v3 |
| **Pro v5** | 帳號系統 + 權限管理 | Pro v4 |

---

## 七、現有 v1 檔案清單（不動）

| 檔案 | 狀態 |
|------|------|
| `src/source_tracker.py` | ✅ 核心模組已建立（尚未接入各元件） |
| `gap_report.py` | ✅ 已可 export + compare |
| `src/chat_history.py` | ✅ 已上線運行 |
| `chat_server.py` | ✅ 含 session 側邊欄 |
| `content_filter.py` | ✅ tag 模式運行中 |
| `run-insurance-kb.sh` | ✅ 含 filter + rebuild + push |
