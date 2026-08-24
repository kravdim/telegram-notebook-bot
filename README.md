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
- **user_id** проверяется во всех пользовательских мутациях по UUID
- **Fail-closed доступ** — production-запуск прекращается при пустом whitelist;
  `ALLOW_ALL_USERS=true` допускается только как явный режим разработки
- **Приватные LLM-логи** — тексты запросов и ответов по умолчанию не сохраняются
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
| LLM | MiniMax M2.7 через OpenAI SDK |
| Embedding | Ollama + nomic-embed-text (production) / experimental cloud adapter |
| STT | faster-whisper (production) / experimental Groq/OpenAI adapter |
| Конфиг | Pydantic Settings + config.yaml + .env |

## Быстрый старт

### Требования

- Python 3.12+
- PostgreSQL 15+ с расширениями `pgvector`, `pg_trgm`, `pgcrypto`
- API-ключи: Telegram Bot Token, MiniMax

### Установка

```bash
# Клонировать
git clone https://github.com/kravdim/telegram-notebook-bot.git
cd telegram-notebook-bot

# Воспроизводимое окружение из lockfile
uv sync --frozen --extra stt
source .venv/bin/activate

# Конфигурация
cp .env.example .env
cp config.yaml.example config.yaml
# Отредактировать .env — вписать BOT_TOKEN и API-ключи
# Отредактировать config.yaml — вписать allowed_telegram_ids

# База данных
createdb notebook_bot
psql -d notebook_bot -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS pgcrypto;"

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

### VPS — Docker (cloud adapters)

Основной production target остаётся macOS LaunchAgent на Mac mini. Переносимый
Docker/VPS target использует cloud LLM, embedding и STT: образ не содержит
локальную Whisper-модель или Ollama. В CI образ поднимается вместе с чистым
PostgreSQL, применяет миграции и проходит schema/vector/readiness E2E.

```bash
cd platform/linux
cp config.docker.yaml.example config.docker.yaml
# Задайте POSTGRES_PASSWORD, BOT_TOKEN, OPENAI_API_KEY, EMBEDDING_API_KEY,
# ALLOWED_TELEGRAM_IDS и ADMIN_TELEGRAM_IDS в shell или env-файле.
docker compose up -d --wait
```

Или через systemd:

```bash
chmod +x platform/linux/install.sh
NOTEBOOK_DB_PASSWORD='replace-with-a-long-random-password' sudo -E ./platform/linux/install.sh
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
│   ├── client.py               # LLMClient: MiniMax M2.7 + опциональный fallback
│   ├── queue.py                # PriorityQueue для LLM-запросов
│   ├── functions.py            # JSON Schema function tools (14 функций)
│   ├── dispatcher.py           # Исполнение function calls + валидация
│   ├── decompose.py            # LLM-декомпозиция проектов на бифштексы
│   ├── prompts.py              # Промпты из БД + 5-мин кэш
│   └── context.py              # История диалога + компрессия
├── db/
│   ├── engine.py               # Async engine + session factory
│   ├── models.py               # SQLAlchemy модели (19 таблиц)
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

19 таблиц, включая доменные данные, `interaction_states`, DB-backed FSM,
`processed_requests`, delivery outbox, версии промптов и обезличенные LLM-логи. Неиспользуемая
таблица `llm_queue` удалена; runtime-очередь живёт в процессе, а входящие
Telegram-сообщения дедуплицируются в PostgreSQL.

Подробная схема — в [CLAUDE.md](CLAUDE.md).

## LLM Function Calling

Бот использует 14 функций через OpenAI-совместимый function calling. Поддерживает множественные tool_calls из одного сообщения.

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
| `complete_project` | Завершить «слона» с подтверждением открытых задач |
| `get_advice` | Совет по тайм-менеджменту из базы знаний Архангельского |
| `add_birthday` | Запомнить день рождения |
| `respond_to_user` | Свободный ответ (с ограничением роли) |

## Надёжность

- **Напоминания независимы от LLM** — если API лежит, напоминания отправляются из БД
- **Write-ahead** — сначала запись в БД, потом подтверждение, потом фон
- **Двойной контур** — основной (30 сек) + sweep (5 мин) для пропущенных напоминаний
- **LLM** — основной провайдер MiniMax M2.7, fallback опционален через config.yaml
- **Health check** — реальный короткий probe main-провайдера каждые 5 минут
- **Идемпотентность** — durable per-part outbox для digest/memoir,
  `chronometry_last_asked`, `tasks_reminder_last_hour` и reminder occurrences
- **Последовательность** — полный pipeline одного пользователя защищён per-user lock;
  входящие pipeline разных пользователей конкурентны, но LLM-вызовы намеренно
  проходят через один priority worker
- **Persistent UX** — onboarding, мемуарник, хронометраж и голосовые подтверждения
  переживают рестарт за счёт PostgreSQL-backed state
- **Повторения** — единый формат: `daily`, `weekdays`, `weekly:1,3`,
  `monthly:15`, `every:3d`, `every:2w`, `every:1m`
- **json_repair** — автокоррекция невалидного JSON от LLM
- **Бэкапы** — ежедневный pg_dump, SHA-256 checksum и ротация 30 дней
- **Graceful shutdown** — корректное завершение при SIGTERM
- **Singleton runtime** — PostgreSQL advisory lock исключает второй scheduler/polling
- **Restore drill** — отдельная CREATEDB-only role из Keychain восстанавливает
  SHA-256-проверенный backup в одноразовую БД и сохраняет измеренный RTO
- **SLO** — `/status` и Telegram-alert для задержки напоминаний и возраста backup
- **CI** — lockfile, lint/typecheck, unit/integration restart tests, Alembic single-head,
  restore drill, secret scan и полный container E2E

Эксплуатация и разработка: [runbook](docs/OPERATIONS.md),
[границы архитектуры](docs/ARCHITECTURE.md), [privacy/retention](docs/PRIVACY.md).

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
| `/adminhelp` | Справка по ручным scheduler-trigger (admin) |
| `/digest morning\|evening [ID]` | Запустить дайджест сейчас (admin) |
| `/review [ID]` | Запустить Sunday Review сейчас (admin) |
| `/chrono_ping [ID]` | Отправить вопрос хронометража сейчас (admin) |

## Конфигурация

### config.yaml

```yaml
llm:
  main:
    provider: minimax
    model: MiniMax-M2.7
    base_url: https://api.minimax.io/v1
    timeout_sec: 25
    max_retries: 2

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

testing:
  e2e_user_ids: []  # только выделенные аккаунты для destructive E2E teardown
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
Бот запускается как LaunchAgent с `KeepAlive=true`. Автоматический перезапуск при сбоях, логи в `~/Library/Logs/notebook-bot/`. Отдельный ежедневный LaunchAgent ограничивает stdout/stderr до семи поколений по 10 МиБ; установка описана в [operations runbook](docs/OPERATIONS.md).

### VPS (Docker, cloud adapters)
`docker-compose.yml` с PostgreSQL (pgvector) + ботом — проверяемый переносимый
target. CI запускает одноразовый Compose project, мигрирует пустую БД, проверяет
extensions, ORM/schema и 768-мерный vector roundtrip, затем ждёт application
readiness. Healthcheck требует свежий heartbeat event loop, валидную
конфигурацию, доступную БД и точное совпадение Alembic head.

Перед Docker-запуском скопируйте `config.docker.yaml.example` в отдельный файл,
передайте его через `DAILYPLANNER_CONFIG_PATH` при нестандартном пути и задайте
обязательные secrets/Telegram allowlists. Compose не содержит значений по
умолчанию для паролей, токенов или API keys. Контейнер сам ждёт БД, применяет
миграции и идемпотентно загружает базу знаний. Успешный readiness также означает,
что Telegram API принял настройку меню команд; состояние LLM/embedding/STT после
старта видно через `/status` как `ok` или `degraded`.

Бэкапы сохраняются вместе с SHA-256 checksum. Проверенное восстановление:

```bash
platform/macos/run-recovery-drill.sh
```

Drill никогда не изменяет исходную БД: он проверяет operator capabilities и
закрытую extension template, создаёт случайную `dailyplanner_restore_drill_*`,
валидирует migration и row counts, измеряет RTO и гарантированно удаляет базу.
На macOS operator password читается только из Keychain, а weekly LaunchAgent
пишет результаты в `~/Library/Logs/notebook-bot/recovery-drills.jsonl`.
На других платформах передавайте `OPERATOR_DATABASE_URL` через штатный secret
manager, не командную строку или env-файл.
Интерактивный `restore_backup.sh` предназначен только для явно выбранной target
БД и остаётся отдельной аварийной процедурой.
Для standalone-установки передайте пароль через
`NOTEBOOK_DB_PASSWORD=... sudo -E platform/linux/install.sh`.

## Лицензия

[MIT](LICENSE)
