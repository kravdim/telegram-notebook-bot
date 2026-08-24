# CLAUDE.md — Telegram-бот «Умная записная книжка»

## Обзор проекта

Персональный AI-ассистент по управлению временем в Telegram на базе методологии Глеба Архангельского. Бот работает 24/7, общение свободным текстом и голосом. Ключевые механики: лягушка дня, слоны/бифштексы, мемуарник, хронометраж с AI-реакциями, режим фокуса, режим командировки.

**Надёжность — главный приоритет.** Пропущенное напоминание или потерянная задача недопустимы. Этот бот — инструмент планирования, от которого пользователь зависит.

## Технический стек

- **Python 3.12+**, aiogram 3.x (async Telegram framework)
- **PostgreSQL 15** + pgvector + pg_trgm
- **SQLAlchemy 2.x** async + asyncpg
- **APScheduler** (AsyncIOScheduler) — планировщик
- **LLM**: облачные API напрямую — MiniMax M2.7 (main), OpenAI SDK; fallback опционален
- **Embedding**: Ollama + nomic-embed-text (macOS) / API провайдера (VPS)
- **STT**: faster-whisper (macOS) / Groq или OpenAI Whisper API (VPS)
- **Конфигурация**: Pydantic Settings, config.yaml (модели/параметры) + .env (секреты)
- **Миграции БД**: Alembic

## Архитектурные принципы

### Единая кодовая база, два деплоя
Один репозиторий. Различия macOS/VPS — только в конфигурации, embedding-клиенте, STT-клиенте и способе запуска. Бизнес-логика идентична. Абстрактные интерфейсы для embedding и STT с двумя реализациями каждый.

### LLM-клиент
Один LLMClient на базе OpenAI Python SDK. Основной провайдер — MiniMax M2.7 из config.yaml, ключ из .env. Fallback можно добавить отдельной секцией `llm.fallback`, но по умолчанию он выключен. Health check main каждые 5 минут — при восстановлении возврат.

### Надёжность 24/7
1. **Напоминания независимы от LLM** — если API лежит, дайджесты и напоминания отправляются из БД.
2. **Write-ahead** — сначала запись в БД, потом подтверждение, потом фоновая обработка.
3. **Двойной контур** — APScheduler + sweep каждые 5 минут (remind_at < now() AND is_sent = false).
4. **Идемпотентность** — digest_sent_date, memoir_asked_date, is_sent предотвращают дублирование.
5. **Graceful shutdown** — SIGTERM: дождаться LLM (30 сек), сохранить APScheduler, закрыть пул PG.
6. **json_repair** — автокоррекция невалидного JSON от LLM перед парсингом function calls.

### Конкурентная обработка
- Персональная asyncio.Queue для каждого пользователя.
- Typing-индикатор пока предыдущее сообщение обрабатывается.
- Приоритеты в общей LLM-очереди: напоминания > intent detection > хронометраж > декомпозиция > суммаризация.

## Структура проекта

```
telegram-notebook-bot/
├── bot/
│   ├── __init__.py
│   ├── main.py                     # Точка входа, инициализация, запуск polling
│   ├── config.py                   # Pydantic Settings + загрузка config.yaml
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── messages.py             # Свободный текст → LLM → function call
│   │   ├── voice.py                # Голосовые → STT → confirm → обработка
│   │   ├── commands.py             # /today, /tasks, /frog, /notes, /projects, /memoir, /chrono, /focus, /trip, /stats, /done, /settings, /help
│   │   ├── admin.py                # /status, /adduser, /removeuser, /listusers, /prompts, /logs
│   │   ├── onboarding.py           # FSM онбординга (6 шагов)
│   │   ├── chronometry.py          # Ответы хронометража, inline-кнопки, фокус
│   │   ├── trip.py                 # Режим командировки
│   │   ├── callbacks.py            # Callback query handlers (snooze, confirm, evening review)
│   │   └── evening_review.py       # Разбор невыполненных задач
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py               # LLMClient: MiniMax M2.7 + опциональный fallback
│   │   ├── queue.py                # asyncio.PriorityQueue для LLM-запросов
│   │   ├── functions.py            # JSON Schema всех function tools
│   │   ├── prompts.py              # Загрузка из prompt_versions, кэширование
│   │   ├── dispatcher.py           # Исполнение function calls + валидация + json_repair
│   │   └── decompose.py            # Многошаговый диалог декомпозиции слонов
│   ├── embeddings/
│   │   ├── __init__.py
│   │   ├── base.py                 # Абстрактный интерфейс
│   │   ├── ollama.py               # Ollama + nomic-embed-text (macOS)
│   │   ├── cloud.py                # Embedding через API (VPS)
│   │   └── indexer.py              # Фоновая индексация + metadata-обогащение
│   ├── stt/
│   │   ├── __init__.py
│   │   ├── base.py                 # Абстрактный интерфейс
│   │   ├── local_whisper.py        # faster-whisper (macOS)
│   │   └── cloud_stt.py            # Groq / OpenAI Whisper API (VPS)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py               # async engine + session factory
│   │   ├── models.py               # SQLAlchemy модели всех таблиц
│   │   ├── crud/
│   │   │   ├── __init__.py
│   │   │   ├── tasks.py
│   │   │   ├── projects.py
│   │   │   ├── notes.py
│   │   │   ├── diary.py
│   │   │   ├── memoir.py
│   │   │   ├── reminders.py
│   │   │   ├── chronometry.py
│   │   │   ├── trips.py
│   │   │   ├── users.py
│   │   │   └── llm_logs.py
│   │   └── migrations/             # Alembic
│   │       ├── env.py
│   │       └── versions/
│   ├── scheduler/
│   │   ├── __init__.py
│   │   ├── setup.py                # Инициализация APScheduler + регистрация всех jobs
│   │   ├── reminders.py            # Отправка напоминаний + snooze
│   │   ├── digest.py               # Утренний + вечерний дайджесты
│   │   ├── memoir.py               # Вопрос мемуарника + недельный/месячный ревью
│   │   ├── chronometry.py          # Периодический опрос + адаптивная частота
│   │   ├── sweep.py                # Двойной контур: пропущенные напоминания
│   │   ├── backup.py               # pg_dump + ротация
│   │   ├── reindex.py              # Повтор NULL embeddings
│   │   ├── healthcheck.py          # Health check + алерт админу
│   │   └── log_rotation.py         # Ротация llm_logs (90 дней)
│   └── formatters/
│       ├── __init__.py
│       ├── digest.py               # Форматирование утреннего/вечернего (вкл. выходные)
│       ├── memoir.py               # Мемуарник + аналитика ценностей
│       ├── chronometry.py          # Фотография рабочего дня + тренды
│       ├── evening_review.py       # Разбор невыполненных + inline-кнопки
│       └── stats.py                # /stats frogs, productivity, values
├── platform/
│   ├── macos/
│   │   ├── com.notebook-bot.plist  # LaunchAgent конфиг
│   │   └── install.sh              # Установка на macOS
│   └── linux/
│       ├── docker-compose.yml
│       ├── Dockerfile
│       ├── notebook-bot.service    # systemd юнит
│       └── install.sh              # Установка на VPS
├── config.yaml.example             # Модели, роутинг, параметры
├── .env.example                    # API-ключи
├── pyproject.toml / uv.lock    # зависимости и воспроизводимый lockfile
├── alembic.ini
├── CLAUDE.md                       # Этот файл
└── docs/
    └── stages/                     # Инструкции по этапам (stage_01.md ... stage_13.md)
```

## Модель данных

### users
```sql
CREATE TABLE users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    digest_morning_time TIME NOT NULL DEFAULT '08:00',
    digest_evening_time TIME NOT NULL DEFAULT '21:00',
    memoir_prompt_time TIME NOT NULL DEFAULT '20:45',
    digest_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    frog_prompt_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    chronometry_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    chronometry_interval_min INT NOT NULL DEFAULT 60,
    work_start_time TIME NOT NULL DEFAULT '09:00',
    work_end_time TIME NOT NULL DEFAULT '18:00',
    work_days INT[] NOT NULL DEFAULT '{1,2,3,4,5}',
    focus_until TIMESTAMPTZ,
    onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE,
    digest_sent_date DATE,
    memoir_asked_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### tasks
```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    trip_id UUID REFERENCES trips(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'work' CHECK (category IN ('work', 'personal')),
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('high', 'medium', 'normal')),
    is_frog BOOLEAN NOT NULL DEFAULT FALSE,
    due_date DATE,
    due_time TIME,
    remind_at TIMESTAMPTZ,
    remind_before_min INT,
    repeat_rule TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled')),
    resolution TEXT CHECK (resolution IN ('completed', 'cancelled', 'deferred', 'expired')),
    tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX idx_tasks_due_date ON tasks(due_date) WHERE status = 'open';
CREATE INDEX idx_tasks_frog ON tasks(user_id) WHERE is_frog = TRUE AND status = 'open';
```

### projects (слоны)
```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL DEFAULT 'work' CHECK (category IN ('work', 'personal')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'done')),
    task_ids_ordered JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### trips (командировки)
```sql
CREATE TABLE trips (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    destination TEXT,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    timezone TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### memoir_entries
```sql
CREATE TABLE memoir_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    event_date DATE NOT NULL,
    content TEXT NOT NULL,
    value_tag TEXT,
    period_type TEXT NOT NULL DEFAULT 'day' CHECK (period_type IN ('day', 'week', 'month', 'year')),
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, event_date, period_type)
);
CREATE INDEX ON memoir_entries USING gin(content gin_trgm_ops);
```

### time_tracking_entries
```sql
CREATE TABLE time_tracking_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL,
    activity_text TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('work', 'personal', 'rest', 'waste', 'focus')),
    is_planned BOOLEAN NOT NULL DEFAULT FALSE,
    matched_task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    productivity_score INT CHECK (productivity_score BETWEEN 1 AND 5),
    bot_reaction TEXT,
    duration_minutes INT NOT NULL DEFAULT 15,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### notes
```sql
CREATE TABLE notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    title TEXT,
    content TEXT NOT NULL,
    tags TEXT[] DEFAULT '{}',
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON notes USING gin(content gin_trgm_ops);
```

### diary_entries
```sql
CREATE TABLE diary_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    entry_date DATE NOT NULL,
    summary TEXT,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON diary_entries USING gin(content gin_trgm_ops);
```

### reminders
```sql
CREATE TABLE reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    remind_at TIMESTAMPTZ NOT NULL,
    repeat_rule TEXT,
    is_sent BOOLEAN NOT NULL DEFAULT FALSE,
    snooze_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_reminders_pending ON reminders(remind_at) WHERE is_sent = FALSE;
```

### llm_queue
```sql
CREATE TABLE llm_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    raw_message TEXT NOT NULL,
    voice_transcript TEXT,
    priority INT NOT NULL DEFAULT 5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    error TEXT
);
```

### prompt_versions
```sql
CREATE TABLE prompt_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_key TEXT NOT NULL,
    version INT NOT NULL,
    content TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(prompt_key, version)
);
CREATE UNIQUE INDEX idx_prompt_active ON prompt_versions(prompt_key) WHERE is_active = TRUE;
```

### llm_logs
```sql
CREATE TABLE llm_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT REFERENCES users(telegram_id) ON DELETE SET NULL,
    prompt_key TEXT,
    prompt_version INT,
    model TEXT NOT NULL,
    input_messages JSONB NOT NULL,
    output_content TEXT,
    function_call JSONB,
    total_tokens INT,
    latency_ms INT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_llm_logs_key_time ON llm_logs(prompt_key, created_at);
```

### Extensions (в начале миграции)
```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

## Function Calling Schema

LLM управляет ботом через function calling. Вот полный список функций, которые LLM может вызвать:

```python
FUNCTIONS = [
    {
        "name": "create_task",
        "description": "Создать новую задачу",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Название задачи"},
                "category": {"type": "string", "enum": ["work", "personal"]},
                "priority": {"type": "string", "enum": ["high", "medium", "normal"]},
                "is_frog": {"type": "boolean", "description": "Пометить как лягушку"},
                "due_date": {"type": "string", "description": "Дедлайн YYYY-MM-DD или null"},
                "due_time": {"type": "string", "description": "Время HH:MM или null"},
                "remind_at": {"type": "string", "description": "ISO datetime напоминания или null"},
                "remind_before_min": {"type": "integer", "description": "За N мин до события"},
            },
            "required": ["title"]
        }
    },
    {
        "name": "update_task",
        "description": "Изменить существующую задачу",
        "parameters": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string", "description": "Текст для поиска задачи"},
                "updates": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "priority": {"type": "string", "enum": ["high", "medium", "normal"]},
                        "is_frog": {"type": "boolean"},
                        "due_date": {"type": "string"},
                        "due_time": {"type": "string"},
                        "status": {"type": "string", "enum": ["open", "done", "cancelled"]},
                    }
                }
            },
            "required": ["search_query", "updates"]
        }
    },
    {
        "name": "complete_task",
        "description": "Отметить задачу выполненной",
        "parameters": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string", "description": "Текст для поиска задачи"}
            },
            "required": ["search_query"]
        }
    },
    {
        "name": "delete_task",
        "description": "Удалить задачу (требует подтверждения)",
        "parameters": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string"}
            },
            "required": ["search_query"]
        }
    },
    {
        "name": "create_note",
        "description": "Создать заметку",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["content"]
        }
    },
    {
        "name": "create_diary_entry",
        "description": "Создать запись в дневнике",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"}
            },
            "required": ["content"]
        }
    },
    {
        "name": "search",
        "description": "Семантический поиск по заметкам, дневнику, задачам",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "string", "enum": ["all", "tasks", "notes", "diary", "memoir"]}
            },
            "required": ["query"]
        }
    },
    {
        "name": "create_reminder",
        "description": "Создать напоминание",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "remind_at": {"type": "string", "description": "ISO datetime"},
                "repeat_rule": {"type": "string", "description": "RRULE или null"}
            },
            "required": ["message", "remind_at"]
        }
    },
    {
        "name": "create_project",
        "description": "Создать слона (крупный проект) и начать декомпозицию",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "category": {"type": "string", "enum": ["work", "personal"]}
            },
            "required": ["title"]
        }
    },
    {
        "name": "respond_to_user",
        "description": "Ответить пользователю текстом (когда не нужен function call)",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string"}
            },
            "required": ["message"]
        }
    }
]
```

## Ключевые промпты (prompt_key → содержание)

### intent_detection
Системный промпт для определения намерения пользователя. Получает сообщение + контекст (время, задачи дня). Возвращает function call или respond_to_user.

### chronometry_reaction
Получает: ответ пользователя + задачи дня + время + предыдущий ответ. Возвращает JSON: {category, is_planned, productivity_score, reaction_text, matched_task_title}.

### memoir_value_extraction
Получает текст ГСД. Возвращает: {value_tag: "семья" | "работа" | "здоровье" | "дружба" | "развитие" | "отдых" | "другое"}.

### morning_digest
Форматирование утреннего дайджеста из данных БД. LLM добавляет мотивирующий контекст.

### evening_summary
Суммаризация дневниковых записей за день.

### context_compression
Суммаризация старых сообщений диалога при превышении 3000 токенов.

### decompose_project
Многошаговый диалог декомпозиции слона на бифштексы.

## Конфигурация

### config.yaml.example
```yaml
llm:
  main:
    provider: minimax
    model: MiniMax-M2.7
    base_url: https://api.minimax.io/v1
    timeout_sec: 25
    max_retries: 2

embedding:
  provider: ollama  # или "cloud"
  model: nomic-embed-text
  base_url: http://localhost:11434
  dimensions: 768

stt:
  provider: local_whisper  # или "groq", "openai"
  model: medium
  language: ru

bot:
  admin_telegram_ids: []
  allowed_telegram_ids: []
  default_timezone: Europe/Moscow

scheduler:
  healthcheck_interval_min: 5
  sweep_interval_min: 5
  backup_hour: 3
  backup_retention_days: 30
  llm_log_retention_days: 90

chronometry:
  default_interval_min: 60
  onboarding_week1_interval: 60
  onboarding_week2_interval: 30
  target_interval: 15
  ignore_threshold: 3  # после 3 пропусков — увеличить интервал

search:
  semantic_weight: 0.6
  text_weight: 0.4
  top_k: 5

context:
  max_tokens: 3000
  keep_recent_pairs: 5
```

### .env.example
```bash
# Telegram
BOT_TOKEN=your_telegram_bot_token

# LLM API Keys
DEEPSEEK_API_KEY=sk-...
MINIMAX_API_KEY=...
# GEMINI_API_KEY=...  # опционально

# STT (для VPS)
# GROQ_API_KEY=...
# OPENAI_API_KEY=...

# Database
DATABASE_URL=postgresql+asyncpg://notebook:password@localhost:5432/notebook_bot

# Embedding (для VPS, если cloud)
# EMBEDDING_API_KEY=...
# EMBEDDING_BASE_URL=...
```

## Правила разработки

### Стиль кода
- Python 3.12+, type hints везде
- async/await для всех IO-операций
- Docstrings на русском (это русскоязычный проект)
- Логирование через `logging` модуль, уровень INFO по умолчанию
- Обработка ошибок: НИКОГДА не глотать исключения молча. Всегда логировать и, если это пользовательская операция, сообщать пользователю

### Telegram UX
- Все сообщения бота на русском языке
- Тон: живой, дружелюбный, поддерживающий. Не робот, а помощник
- Inline-кнопки для всех действий, где это уместно
- Для голосовых: ОБЯЗАТЕЛЬНЫЙ confirm-шаг после транскрибации
- Для деструктивных операций: confirm-кнопки
- Typing-индикатор при обработке
- Не более 4096 символов на сообщение (лимит Telegram), разбивать при необходимости

### БД
- Все операции через SQLAlchemy async сессии
- Транзакции для связанных операций
- Индексы на часто используемых полях (уже в схеме выше)
- UUID для всех ID кроме telegram_id

### Тестирование
- Каждый модуль CRUD покрыт базовыми тестами
- Мок для LLM-клиента в тестах
- pytest + pytest-asyncio

## Исторические этапы разработки

Первоначальные этапы 1–13 завершены. Их постановки сохранены только как
исторический audit trail в `docs/archive/stages/` и не являются текущим
чек-листом разработки.

| Этап | Что делаем | Результат |
|------|-----------|-----------|
| 1 | Фундамент: окружение, БД, Telegram, whitelist, онбординг, launchd | Бот принимает сообщения |
| 2 | LLM-клиент: MiniMax M2.7, function calling, валидация, промпты в БД, очередь | LLM работает |
| 3 | Задачи + лягушка: CRUD, is_frog, приоритеты, confirm, snooze, двойной контур | Задачи надёжно |
| 4 | Дайджесты: утренний, вечерний + разбор невыполненных, идемпотентность, выходные | Дайджесты стабильны |
| 5 | Слоны: Project, декомпозиция LLM, бифштексы, дайджест, пауза | Слоны работают |
| 6 | Мемуарник: memoir_entries, ГСД, ценности, ревью, grace period 24ч | Мемуарник работает |
| 7 | Хронометраж: опрос, адаптивная частота, фокус, умная пауза, реакции, фотография | Хронометраж с фокусом |
| 8 | Командировки: trips, trip_id, адаптация дайджеста | Командировки |
| 9 | Заметки + RAG: CRUD, embedding, гибридный поиск, чанкинг | Семантический поиск |
| 10 | Голос + мониторинг: STT, confirm для голосовых, healthcheck, бэкапы | Голос, мониторинг |
| 11 | Статистика: /stats frogs, productivity, values, тренды | Аналитика |
| 12 | VPS-версия: Docker, systemd, cloud STT, cloud embedding | VPS готов |
| 13 | Полировка: тюнинг промптов, UX, нагрузочный тест, edge cases | Production-ready |

## Начало работы

1. Прочитай этот файл полностью
2. Проверь актуальные `README.md`, `docs/ARCHITECTURE.md` и `docs/OPERATIONS.md`
3. Перед релизом выполни release checklist из `docs/OPERATIONS.md`
4. Исторические планы используй только для объяснения принятых решений
