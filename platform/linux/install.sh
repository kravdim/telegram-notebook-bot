#!/bin/bash
# Установка Telegram Notebook Bot на VPS (Ubuntu/Debian)
set -e

echo "=== Установка Telegram Notebook Bot ==="

# Создаём пользователя
sudo useradd -r -m -s /bin/bash notebook 2>/dev/null || true

# Устанавливаем зависимости
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv postgresql-15 postgresql-15-pgvector

# Копируем проект
sudo mkdir -p /opt/notebook-bot
sudo cp -r . /opt/notebook-bot/
sudo chown -R notebook:notebook /opt/notebook-bot

# Создаём venv
sudo -u notebook python3.12 -m venv /opt/notebook-bot/.venv
sudo -u notebook /opt/notebook-bot/.venv/bin/pip install -r /opt/notebook-bot/requirements.txt

# Настраиваем PostgreSQL
sudo -u postgres psql -c "CREATE USER notebook WITH PASSWORD 'password';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE notebook_bot OWNER notebook;" 2>/dev/null || true
sudo -u postgres psql -d notebook_bot -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d notebook_bot -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
sudo -u postgres psql -d notebook_bot -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"

# Миграции
cd /opt/notebook-bot
sudo -u notebook PYTHONPATH=/opt/notebook-bot /opt/notebook-bot/.venv/bin/alembic upgrade head

# Systemd
sudo cp platform/linux/notebook-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable notebook-bot
sudo systemctl start notebook-bot

echo "=== Установка завершена ==="
echo "Проверь статус: sudo systemctl status notebook-bot"
echo "Логи: sudo journalctl -u notebook-bot -f"
