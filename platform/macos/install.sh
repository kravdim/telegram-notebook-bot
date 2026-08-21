#!/bin/bash
# Установка Telegram-бота на macOS через LaunchAgent
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PLIST_SRC="$SCRIPT_DIR/com.notebook-bot.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.notebook-bot.plist"
LOG_DIR="$HOME/Library/Logs/notebook-bot"

echo "=== Установка Telegram Notebook Bot ==="
echo "Проект: $PROJECT_DIR"

# 1. Установка зависимостей из lockfile
echo "Устанавливаю зависимости..."
if ! command -v uv >/dev/null 2>&1; then
    brew install uv
fi
uv sync --project "$PROJECT_DIR" --frozen --no-dev --extra stt
echo "Загружаю и проверяю локальную STT-модель..."
cd "$PROJECT_DIR"
DAILYPLANNER_STT_CACHE="$HOME/Library/Caches/notebook-bot/huggingface" \
    "$VENV_DIR/bin/python" scripts/prefetch_stt_model.py

# 3. Создание директории логов
mkdir -p "$LOG_DIR"

# 4. Копирование и настройка plist
echo "Настраиваю LaunchAgent..."
sed \
    -e "s|__PROJECT_PATH__|$PROJECT_DIR|g" \
    -e "s|__HOME__|$HOME|g" \
    "$PLIST_SRC" > "$PLIST_DST"

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
