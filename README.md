# Telegram Notebook Bot

Персональный AI-ассистент по управлению временем в Telegram на базе методологии Глеба Архангельского.

Бот работает 24/7, общение свободным текстом и голосом. Ключевые механики: лягушка дня, слоны/бифштексы, мемуарник, хронометраж с AI-реакциями, режим фокуса, режим командировки, база знаний по тайм-менеджменту.

## Возможности

- **Свободный текст** — пишешь «Напомни завтра в 14:00 позвонить Ивану», бот создаёт задачу и напоминание
- **Множественные действия** — «Встреча в 15, заехать в банкомат, напомни в полдень» → 2 задачи + напоминание за один раз
- **Лягушка дня** (`/frog`) — самое неприятное дело съедаем первым
- **Слоны** (`/projects`) — большие проекты разбиваются на бифштексы (задачи на 1-2 часа) через AI-декомпозицию
- **Мемуарник** (`/memoir`) — каждый вечер сначала хронологический список занятий по ответам трекера, затем вопрос «что было самым ярким?», анализ ценностей. Недельный обзор по воскресеньям отсортирован по дате (сверху — раньше)
- **Хронометраж** (`/chrono`) — периодический опрос с AI-реакциями, фотография рабочего дня
- **Reply-контекст** — ответ reply'ем на конкретное сообщение бота направляется в нужный обработчик (хронометраж или LLM)
- **Дайджесты** — утренний (с днями рождения!) и вечерний с разбором невыполненных задач
- **Sunday Review** — еженедельный обзор: распределение времени, лягушки, ценности, прогресс по слонам
- **Периодические напоминания** — список задач каждые 2 часа в рабочее время (9, 11, 13, 15, 17)
- **Режим фокуса** (`/focus`) — хронометраж не беспокоит
- **Командировки** (`/trip`) — отдельный список задач, адаптированный дайджест
- **Голосовые сообщения** — STT + confirm перед обработкой, лимит размера 20 МБ
- **Семантический поиск** — pgvector + pg_trgm, гибридный RAG (vector + trigram) по всем записям
- **База знаний** — советы по методике Архангельского через RAG-поиск
- **Дни рождения** (`/birthdays`) — запоминает и показывает в утреннем дайджесте
- **Экспорт** (`/export`) — выгрузка в Markdown (Obsidian-совместимый)
- **Статистика** (`/stats`) — лягушки, продуктивность, ценности
- **Повторяющиеся задачи** — бот распознаёт и комментирует рутину с юмором

## Безопасность

- **Whitelist** — доступ только для разрешённых Telegram ID (middleware на message + callback_query)
- **Rate limiting** — анти-флуд middleware: макс. 20 сообщений/минуту на пользователя
- **Prompt injection protection** — жёсткие границы роли, игнорирование попыток смены поведения, лимит длины ответов
- **Валидация LLM-вывода** — json_repair + проверка допустимых полей и значений в dispatcher
- **user_id** проверяется во всех CRUD-операциях
- **SQL injection** — параметризованные запросы SQLAlchemy
- **API-ключи** — .env + Pydantic Settings, вне репозитория

## Технический стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.12+ |
| Telegram | aiogram 3.x (async) |
| БД | PostgreSQL 15 + pgvector + pg_trgm |
| ORM | SQLAlchemy 2.x async + asyncpg |
| Миграции | Alembic |
| LLM | Gemini 3.1 Pro Preview (main) + DeepSeek (fallback) через OpenAI SDK |
| Embedding | Ollama + nomic-embed-text (macOS) / API (VPS) |
| STT | faster-whisper (macOS) / Groq API (VPS) |
| Конфиг | Pydantic Settings + config.yaml + .env |

## Быстрый старт

### Требования

- Python 3.12+
- PostgreSQL 15+ с расширениями `pgvector`, `pg_trgm`, `uuid-ossp`
- API-ключи: Telegram Bot Token, Gemini / DeepSeek

### Установка

```bash
# Клонировать
git clone https://github.com/kravdim/telegram-notebook-bot.git
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

# Загрузка базы знаний (тайм-менеджмент советы)
PYTHONPATH=. python scripts/seed_knowledge.py

# Запуск
PYTHONPATH=. python -m bot.main
```

### macOS — автозапуск через LaunchAgent

```bash
chmod +x platform/macos/install.sh
./platform/macos/install.sh
```

Бот будет автоматически запускаться при загрузке macOS и перезапускаться при сбоях.

### VPS — Docker

```bash
cd platform/linux
docker-compose up -d
```

Или через systemd:

```bash
chmod +x platform/linux/install.sh
sudo ./platform/linux/install.sh
```

## Структура проекта

```
bot/
├── main.py                     # Точка входа, инициализация, polling
├── config.py                   # Pydantic Settings + config.yaml
├── middleware.py                # Whitelist + Rate Limiting middleware
├── handlers/
│   ├── onboarding.py           # FSM онбординга (6 шагов)
│   ├── commands.py             # /today, /tasks, /frog, /done, /notes, /projects,
│   │                           # /memoir, /chrono, /focus, /stats, /birthdays,
│   │                           # /export, /settings, /help
│   ├── messages.py             # Свободный текст → LLM → function call (с reply-контекстом)
│   ├── callbacks.py            # Snooze, confirm удаления
│   ├── evening_review.py       # Разбор невыполненных задач
│   ├── chronometry.py          # Обработка ответов хронометража
│   ├── trip.py                 # Управление командировками
│   ├── voice.py                # Голосовые → STT → confirm → обработка (лимит 20 МБ)
│   └── admin.py                # /status, /prompts, /adduser, /removeuser, /listusers
├── llm/
│   ├── client.py               # LLMClient с fallback (Gemini → DeepSeek)
│   ├── queue.py                # PriorityQueue для LLM-запросов
│   ├── functions.py            # JSON Schema function tools (13 функций)
│   ├── dispatcher.py           # Исполнение function calls + валидация
│   ├── decompose.py            # LLM-декомпозиция проектов на бифштексы
│   ├── prompts.py              # Промпты из БД + 5-мин кэш
│   └── context.py              # История диалога + компрессия
├── db/
│   ├── engine.py               # Async engine + session factory
│   ├── models.py               # SQLAlchemy модели (14 таблиц)
│   ├── crud/                   # CRUD-операции
│   │   ├── tasks.py            # Задачи, лягушки, поиск, повторяющиеся
│   │   ├── users.py            # Пользователи, настройки
│   │   ├── projects.py         # Проекты (слоны), прогресс
│   │   ├── memoir.py           # Мемуарник, ценности, статистика
│   │   ├── chronometry.py      # Хронометраж, дневная/недельная статистика
│   │   ├── trips.py            # Командировки
│   │   ├── reminders.py        # Напоминания, snooze
│   │   ├── notes.py            # Заметки
│   │   ├── diary.py            # Дневник
│   │   ├── knowledge.py        # База знаний (гибридный RAG-поиск)
│   │   ├── birthdays.py        # Дни рождения
│   │   └── llm_logs.py         # Логи LLM-запросов
│   └── migrations/             # Alembic миграции
├── scheduler/
│   ├── reminders.py            # Основной контур напоминаний (30 сек)
│   ├── sweep.py                # Двойной контур: пропущенные (5 мин)
│   ├── digest.py               # Утренний/вечерний дайджесты
│   ├── memoir.py               # Вопросы мемуарника
│   ├── chronometry.py          # Периодический опрос хронометража (с reply-tracking)
│   ├── task_reminders.py       # Напоминания задач каждые 2 часа (9/11/13/15/17)
│   ├── weekly_review.py        # Еженедельный обзор (воскресенье 21:00)
│   ├── healthcheck.py          # Health check (DB, LLM, Embedding)
│   ├── backup.py               # pg_dump + ротация (30 дней)
│   ├── log_rotation.py         # Ротация llm_logs (90 дней)
│   └── reindex.py              # Переиндексация NULL embeddings
├── embeddings/
│   ├── base.py                 # Абстрактный интерфейс
│   ├── ollama.py               # Ollama + nomic-embed-text (macOS)
│   ├── cloud.py                # Облачный API (VPS)
│   └── indexer.py              # Фоновая индексация
├── stt/
│   ├── base.py                 # Абстрактный интерфейс
│   ├── local_whisper.py        # faster-whisper (macOS)
│   └── cloud_stt.py            # Groq / OpenAI Whisper API (VPS)
└── formatters/
    ├── digest.py               # Утренний/вечерний дайджест
    ├── evening_review.py       # Разбор невыполненных
    ├── memoir.py               # Мемуарник + ценности
    ├── chronometry.py          # Фотография рабочего дня + недельная сводка
    └── stats.py                # Статистика лягушек, продуктивности
scripts/
└── seed_knowledge.py           # Загрузка базы знаний Архангельского
```

## Модель данных

14 таблиц: `users`, `tasks`, `projects`, `trips`, `memoir_entries`, `time_tracking_entries`, `notes`, `diary_entries`, `reminders`, `knowledge_base`, `birthdays`, `llm_queue`, `prompt_versions`, `llm_logs`.

Подробная схема — в [CLAUDE.md](CLAUDE.md).

## LLM Function Calling

Бот использует 13 функций через OpenAI-совместимый function calling. Поддерживает множественные tool_calls из одного сообщения.

| Функция | Описание |
|---------|----------|
| `create_task` | Создать задачу с датой, приоритетом, напоминанием |
| `complete_task` | Отметить задачу выполненной |
| `update_task` | Изменить задачу |
| `delete_task` | Удалить задачу (с подтверждением) |
| `list_tasks` | Список задач (today / all / overdue / done_today) |
| `create_note` | Создать заметку |
| `create_diary_entry` | Запись в дневник |
| `create_reminder` | Напоминание на конкретное время |
| `search` | Гибридный RAG-поиск по задачам, заметкам, дневнику, мемуарнику |
| `create_project` | Создать «слона» с AI-декомпозицией на бифштексы |
| `get_advice` | Совет по тайм-менеджменту из базы знаний Архангельского |
| `add_birthday` | Запомнить день рождения |
| `respond_to_user` | Свободный ответ (с ограничением роли) |

## Надёжность

- **Напоминания независимы от LLM** — если API лежит, напоминания отправляются из БД
- **Write-ahead** — сначала запись в БД, потом подтверждение, потом фон
- **Двойной контур** — основной (30 сек) + sweep (5 мин) для пропущенных напоминаний
- **LLM fallback** — при сбое Gemini автоматический переход на DeepSeek
- **Health check** — восстановление main каждые 5 минут
- **Идемпотентность** — `digest_sent_date`, `memoir_asked_date`, `chronometry_last_asked`, `tasks_reminder_last_hour`, `is_sent`
- **json_repair** — автокоррекция невалидного JSON от LLM
- **Бэкапы** — ежедневный pg_dump с ротацией 30 дней
- **Graceful shutdown** — корректное завершение при SIGTERM

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Онбординг (6 шагов) |
| `/help` | Справка |
| `/today` | Задачи на сегодня |
| `/tasks` | Все открытые задачи |
| `/frog` | Лягушка дня |
| `/done [текст]` | Отметить задачу выполненной |
| `/projects` | Слоны с прогресс-барами |
| `/notes` | Последние заметки |
| `/memoir` | Мемуарник + статистика ценностей |
| `/chrono` | Фотография рабочего дня |
| `/chrono week` | Недельная сводка хронометража |
| `/focus [мин]` | Режим фокуса (по умолчанию 30 мин) |
| `/trip` | Командировки (on/off) |
| `/birthdays` | Список дней рождения |
| `/stats frogs` | Статистика лягушек |
| `/stats productivity` | Статистика продуктивности |
| `/stats values` | Статистика ценностей |
| `/export` | Экспорт данных в Markdown (ZIP) |
| `/settings` | Текущие настройки |
| `/status` | Статус сервисов (admin) |
| `/prompts` | Список промптов (admin) |
| `/adduser ID` | Добавить в whitelist (admin) |
| `/removeuser ID` | Удалить из whitelist (admin) |
| `/listusers` | Список пользователей (admin) |

## Конфигурация

### config.yaml

```yaml
llm:
  main:
    provider: gemini
    model: gemini-3.1-pro-preview
    base_url: https://generativelanguage.googleapis.com/v1beta/openai/
    timeout_sec: 25
    max_retries: 2
  fallback:
    provider: deepseek
    model: deepseek-chat
    base_url: https://api.deepseek.com/v1

embedding:
  provider: ollama          # или "cloud"
  model: nomic-embed-text
  base_url: http://localhost:11434

stt:
  provider: local_whisper   # или "groq", "openai"
  model: medium
  language: ru

bot:
  admin_telegram_ids: []
  allowed_telegram_ids: []
  default_timezone: Europe/Moscow
```

### .env

```bash
BOT_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=...
DEEPSEEK_API_KEY=...
DATABASE_URL=postgresql+asyncpg://notebook:password@localhost:5432/notebook_bot
```

## Деплой

### macOS (LaunchAgent)
Бот запускается как LaunchAgent с `KeepAlive=true`. Автоматический перезапуск при сбоях, логи в `~/Library/Logs/notebook-bot/`.

### VPS (Docker)
`docker-compose.yml` с PostgreSQL (pgvector) + ботом. Или standalone через systemd.

## Лицензия

MIT
