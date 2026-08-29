#!/bin/bash
# Установка Telegram-бота на macOS через LaunchAgent
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PLIST_SRC="$SCRIPT_DIR/com.notebook-bot.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.notebook-bot.plist"
LOG_DIR="$HOME/Library/Logs/notebook-bot"
HTTP_PROXY_URL=""
ALL_PROXY_URL=""

usage() {
    echo "Usage: $0 [--http-proxy URL] [--all-proxy URL]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --http-proxy)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            HTTP_PROXY_URL="$2"
            shift 2
            ;;
        --all-proxy)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            ALL_PROXY_URL="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

echo "=== Установка Telegram Notebook Bot ==="
echo "Проект: $PROJECT_DIR"

# 1. Установка зависимостей из lockfile
echo "Устанавливаю зависимости..."
if ! command -v uv >/dev/null 2>&1; then
    brew install uv
fi
uv sync --project "$PROJECT_DIR" --frozen --no-dev --extra stt

PROXY_ARGS=()
if [ -n "$HTTP_PROXY_URL" ]; then
    echo "Проверяю явно заданный HTTP proxy..."
    /usr/bin/curl --silent --show-error --head --max-time 10 \
        --proxy "$HTTP_PROXY_URL" https://api.telegram.org >/dev/null
    PROXY_ARGS+=(--http-proxy "$HTTP_PROXY_URL")
fi
if [ -n "$ALL_PROXY_URL" ]; then
    echo "Проверяю явно заданный SOCKS proxy..."
    /usr/bin/curl --silent --show-error --head --max-time 10 \
        --proxy "$ALL_PROXY_URL" https://api.telegram.org >/dev/null
    PROXY_ARGS+=(--all-proxy "$ALL_PROXY_URL")
fi

echo "Загружаю и проверяю локальную STT-модель..."
cd "$PROJECT_DIR"
DAILYPLANNER_STT_CACHE="$HOME/Library/Caches/notebook-bot/huggingface" \
    "$VENV_DIR/bin/python" scripts/prefetch_stt_model.py

# 3. Создание директории логов
mkdir -p "$LOG_DIR"

# 4. Копирование и настройка plist
echo "Настраиваю LaunchAgent..."
"$VENV_DIR/bin/python" "$SCRIPT_DIR/render_launchagent.py" \
    --template "$PLIST_SRC" \
    --output "$PLIST_DST" \
    --project "$PROJECT_DIR" \
    --home "$HOME" \
    "${PROXY_ARGS[@]}"

# 5. Загрузка LaunchAgent
echo "Загружаю LaunchAgent..."
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo ""
echo "=== Готово! ==="
echo "Бот запущен как LaunchAgent."
echo "Логи: $LOG_DIR/"
echo ""
echo "Управление:"
echo "  Остановить: launchctl unload $PLIST_DST"
echo "  Запустить:  launchctl load $PLIST_DST"
echo "  Логи:       tail -f $LOG_DIR/stdout.log"
