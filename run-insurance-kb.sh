#!/bin/bash
# 保險知識庫 Cron 包裝腳本
# - flock 防止重複執行
# - 環境變數設定（gemini CLI）
# - 失敗 Telegram 通知

LOCK_FILE="/tmp/insurance-kb.lock"
LOG_DIR="/home/ac-macmini2/insurance-kb/logs"
CRON_LOG="$LOG_DIR/cron.log"
WORK_DIR="/home/ac-macmini2/insurance-kb"

# 載入環境變數
if [ -f /home/ac-macmini2/world-monitor/.env ]; then
    set -a
    source /home/ac-macmini2/world-monitor/.env
    set +a
fi

# 確保 gemini CLI 在 PATH 中
export PATH="/usr/local/bin:$PATH"

# 確保日誌目錄存在
mkdir -p "$LOG_DIR"

# flock 防止重複執行
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] Another instance running" >> "$CRON_LOG"
    exit 0
fi

# 執行主程式
cd "$WORK_DIR"
echo "$(date '+%Y-%m-%d %H:%M:%S') [START] Cron job started" >> "$CRON_LOG"

/usr/bin/python3 run.py >> "$CRON_LOG" 2>&1
EXIT_CODE=$?

# 內容過濾：標記非保險相關文章（filter 失敗不影響整體退出碼）
echo "$(date '+%Y-%m-%d %H:%M:%S') [FILTER] Running content filter" >> "$CRON_LOG"
/usr/bin/python3 content_filter.py --today-only >> "$CRON_LOG" 2>&1 || echo "$(date '+%Y-%m-%d %H:%M:%S') [FILTER] Filter failed (non-critical)" >> "$CRON_LOG"

# 過濾後重建靜態頁面並推送（確保 Card View 反映 filter 結果）
echo "$(date '+%Y-%m-%d %H:%M:%S') [REBUILD] Rebuilding site after filter" >> "$CRON_LOG"
/usr/bin/python3 build_site.py >> "$CRON_LOG" 2>&1
if git diff --quiet docs/index.html 2>/dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [REBUILD] No changes to Card View" >> "$CRON_LOG"
else
    git add docs/index.html index/master-index.json
    git commit -m "chore: update filtered articles $(date +%Y-%m-%d)" >> "$CRON_LOG" 2>&1
    git push >> "$CRON_LOG" 2>&1
    echo "$(date '+%Y-%m-%d %H:%M:%S') [REBUILD] Pushed filtered Card View" >> "$CRON_LOG"
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') [END] Exit code: $EXIT_CODE" >> "$CRON_LOG"

# 失敗通知
if [ $EXIT_CODE -ne 0 ]; then
    TG_TOKEN="${TG_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
    TG_CHAT="${TG_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}"
    if [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ]; then
        LAST_LINES=$(tail -20 "$CRON_LOG" | sed 's/[^a-zA-Z0-9 _\-\.\/\:\[\]()]//g' | tail -5)
        curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="$TG_CHAT" \
            -d text="🔴 保險知識庫 Cron 失敗 (exit=$EXIT_CODE)
$(date '+%Y-%m-%d %H:%M')
最後日誌:
$LAST_LINES" \
            -d parse_mode="" > /dev/null 2>&1
    fi
fi

# 日誌輪替：保留最近 5000 行
if [ -f "$CRON_LOG" ] && [ $(wc -l < "$CRON_LOG") -gt 10000 ]; then
    tail -5000 "$CRON_LOG" > "$CRON_LOG.tmp" && mv "$CRON_LOG.tmp" "$CRON_LOG"
fi
