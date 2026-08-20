#!/bin/bash
# Установка Telegram Notebook Bot на VPS (Ubuntu/Debian)
set -e

DB_PASSWORD="${NOTEBOOK_DB_PASSWORD:-}"
if [ -z "$DB_PASSWORD" ]; then
    echo "NOTEBOOK_DB_PASSWORD is required. Generate one, for example: openssl rand -hex 24" >&2
    exit 2
fi
case "$DB_PASSWORD" in
    *[!A-Za-z0-9_-]*)
        echo "NOTEBOOK_DB_PASSWORD may contain only letters, digits, _ and -" >&2
        exit 2
        ;;
esac

echo "=== Установка Telegram Notebook Bot ==="

# Создаём пользователя
sudo useradd -r -m -s /bin/bash notebook 2>/dev/null || true

# Устанавливаем зависимости
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv postgresql-15 postgresql-15-pgvector

# Копируем проект
sudo mkdir -p /opt/notebook-bot
sudo mkdir -p /var/lib/notebook-bot/backups
sudo cp -r . /opt/notebook-bot/
sudo chown -R notebook:notebook /opt/notebook-bot
sudo chown -R notebook:notebook /var/lib/notebook-bot

# Создаём venv
sudo -u notebook python3.12 -m venv /opt/notebook-bot/.venv
sudo -u notebook /opt/notebook-bot/.venv/bin/pip install uv==0.11.21
sudo -u notebook /opt/notebook-bot/.venv/bin/uv sync \
    --project /opt/notebook-bot --frozen --no-dev --extra stt

# Настраиваем PostgreSQL
sudo -u postgres psql -c "CREATE USER notebook;" 2>/dev/null || true
sudo -u postgres psql -c "ALTER USER notebook WITH PASSWORD '$DB_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE notebook_bot OWNER notebook;" 2>/dev/null || true
sudo -u postgres psql -d notebook_bot -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d notebook_bot -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
sudo -u postgres psql -d notebook_bot -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"

if [ ! -f /opt/notebook-bot/.env ]; then
    sudo -u notebook cp /opt/notebook-bot/.env.example /opt/notebook-bot/.env
fi
sudo -u notebook sed -i \
    "s#^DATABASE_URL=.*#DATABASE_URL=postgresql+asyncpg://notebook:${DB_PASSWORD}@localhost:5432/notebook_bot#" \
    /opt/notebook-bot/.env

# Миграции
cd /opt/notebook-bot
sudo -u notebook PYTHONPATH=/opt/notebook-bot /opt/notebook-bot/.venv/bin/alembic upgrade head
sudo -u notebook PYTHONPATH=/opt/notebook-bot /opt/notebook-bot/.venv/bin/python scripts/preflight.py

# Systemd
sudo cp platform/linux/notebook-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable notebook-bot
sudo systemctl start notebook-bot

echo "=== Установка завершена ==="
echo "Проверь статус: sudo systemctl status notebook-bot"
echo "Логи: sudo journalctl -u notebook-bot -f"
