# Telegram Notebook Bot

Персональный AI-ассистент по управлению временем в Telegram на базе методологии Глеба Архангельского.

Бот работает 24/7, общение свободным текстом и голосом. Ключевые механики: лягушка дня, слоны/бифштексы, мемуарник, хронометраж с AI-реакциями, режим фокуса, режим командировки.

## Возможности

- **Свободный текст** — пишешь «Напомни завтра в 14:00 позвонить Ивану», бот создаёт задачу и напоминание
- **Лягушка дня** (`/frog`) — самое неприятное дело съедаем первым
- **Слоны** (`/projects`) — большие проекты разбиваются на бифштексы через AI-декомпозицию
- **Мемуарник** (`/memoir`) — каждый вечер вопрос «что было самым ярким?», анализ ценностей
- **Хронометраж** (`/chrono`) — периодический опрос с AI-реакциями, фотография рабочего дня
- **Дайджесты** — утренний и вечерний с разбором невыполненных задач
- **Режим фокуса** (`/focus`) — хронометраж не беспокоит
- **Командировки** (`/trip`) — отдельный список задач, адаптированный дайджест
- **Голосовые сообщения** — STT + confirm перед обработкой
- **Семантический поиск** — pgvector + pg_trgm, гибридный поиск по всем записям

## Технический стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.9+ |
| Telegram | aiogram 3.x (async) |
| БД | PostgreSQL 15 + pgvector + pg_trgm |
| ORM | SQLAlchemy 2.x async + asyncpg |
| Миграции | Alembic |
| LLM | DeepSeek V3.2 (main) + MiniMax M2.5 (fallback) через OpenAI SDK |
| Embedding | Ollama + nomic-embed-text (macOS) / API (VPS) |
| STT | faster-whisper (macOS) / Groq API (VPS) |
| Конфиг | Pydantic Settings + config.yaml + .env |

## Быстрый старт

### Требования

- Python 3.9+
- PostgreSQL 15+ с расширениями `pgvector`, `pg_trgm`, `uuid-ossp`
- API-ключи: Telegram Bot Token, DeepSeek, MiniMax (опционально)

### Установка

```bash
# Клонировать
git clone https://github.com/<your-user>/telegram-notebook-bot.git
cd telegram-notebook-bot

# Виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Конфигурация
cp .env.example .env
cp config.yaml.example config.yaml
# Отредактировать .env — вписать BOT_TOKEN и API-ключи
# Отредактировать config.yaml — вписать allowed_telegram_ids

# База данных
createdb notebook_bot
psql -d notebook_bot -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"

# Миграции
PYTHONPATH=. alembic upgrade head

# Запуск
PYTHONPATH=. python -m bot.main
```

### macOS — автозапуск через LaunchAgent

```bash
chmod +x platform/macos/install.sh
./platform/macos/install.sh
```

Бот будет автоматически запускаться при загрузке macOS и перезапускаться при сбоях.

## Структура проекта

```
bot/
├── main.py                     # Точка входа, инициализация, polling
├── config.py                   # Pydantic Settings + config.yaml
├── middleware.py                # Whitelist middleware
├── handlers/
│   ├── onboarding.py           # FSM онбординга (6 шагов)
│   ├── commands.py             # /today, /tasks, /frog, /done, /help
│   ├── messages.py             # Свободный текст → LLM → function call
│   ├── callbacks.py            # Snooze, confirm удаления
│   └── admin.py                # /status, /prompts (admin)
├── llm/
│   ├── client.py               # LLMClient с fallback
│   ├── queue.py                # PriorityQueue для LLM-запросов
│   ├── functions.py            # JSON Schema function tools
│   ├── dispatcher.py           # Исполнение function calls
│   ├── prompts.py              # Промпты из БД + кэш
│   └── context.py              # История диалога + компрессия
├── db/
│   ├── engine.py               # Async engine + session factory
│   ├── models.py               # SQLAlchemy модели (12 таблиц)
│   ├── crud/                   # CRUD-операции
│   │   ├── tasks.py
│   │   ├── users.py
│   │   ├── reminders.py
│   │   ├── notes.py
│   │   ├── diary.py
│   │   └── llm_logs.py
│   └── migrations/             # Alembic миграции
├── scheduler/
│   ├── reminders.py            # Основной контур напоминаний
│   ├── sweep.py                # Двойной контур (пропущенные)
│   └── healthcheck.py          # Health check LLM
├── embeddings/                 # (этап 9)
├── stt/                        # (этап 10)
└── formatters/                 # (этап 4+)
```

## Модель данных

12 таблиц: `users`, `tasks`, `projects`, `trips`, `memoir_entries`, `time_tracking_entries`, `notes`, `diary_entries`, `reminders`, `llm_queue`, `prompt_versions`, `llm_logs`.

Подробная схема — в [CLAUDE.md](CLAUDE.md).

## LLM Function Calling

Бот использует 10 функций через OpenAI-совместимый function calling:

| Функция | Описание |
|---------|----------|
| `create_task` | Создать задачу с датой, приоритетом, напоминанием |
| `complete_task` | Отметить задачу выполненной |
| `update_task` | Изменить задачу |
| `delete_task` | Удалить задачу (с подтверждением) |
| `create_note` | Создать заметку |
| `create_diary_entry` | Запись в дневник |
| `create_reminder` | Напоминание на конкретное время |
| `search` | Поиск по задачам, заметкам, дневнику |
| `create_project` | Создать «слона» |
| `respond_to_user` | Свободный ответ |

## Надёжность

- **Напоминания независимы от LLM** — если API лежит, напоминания отправляются из БД
- **Write-ahead** — сначала запись в БД, потом подтверждение, потом фон
- **Двойной контур** — основной (30 сек) + sweep (5 мин) для пропущенных напоминаний
- **LLM fallback** — при сбое DeepSeek автоматический переход на MiniMax
- **Health check** — восстановление main каждые 5 минут
- **Идемпотентность** — `digest_sent_date`, `memoir_asked_date`, `is_sent`
- **json_repair** — автокоррекция невалидного JSON от LLM

## Этапы разработки

| Этап | Статус | Описание |
|------|--------|----------|
| 1. Фундамент | ✅ | Окружение, БД, Telegram, whitelist, онбординг, launchd |
| 2. LLM-клиент | ✅ | DeepSeek/MiniMax fallback, function calling, промпты |
| 3. Задачи + лягушка | ✅ | CRUD, приоритеты, snooze, двойной контур |
| 4. Дайджесты | ⬜ | Утренний/вечерний, разбор невыполненных |
| 5. Слоны | ⬜ | Проекты, декомпозиция через LLM |
| 6. Мемуарник | ⬜ | ГСД, ценности, ревью |
| 7. Хронометраж | ⬜ | Опрос, фокус, фотография дня |
| 8. Командировки | ⬜ | Режим командировки |
| 9. Заметки + RAG | ⬜ | Embedding, семантический поиск |
| 10. Голос + мониторинг | ⬜ | STT, healthcheck, бэкапы |
| 11. Статистика | ⬜ | /stats frogs, productivity, values |
| 12. VPS-версия | ⬜ | Docker, systemd, cloud STT/embedding |
| 13. Полировка | ⬜ | Тюнинг промптов, UX, нагрузочный тест |

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Онбординг (6 шагов) |
| `/help` | Справка |
| `/today` | Задачи на сегодня |
| `/tasks` | Все открытые задачи |
| `/frog` | Лягушка дня |
| `/done [текст]` | Отметить задачу выполненной |
| `/prompts` | Список промптов (admin) |
| `/status` | Статус бота (admin) |

## Лицензия

MIT
