# Этап 1. Фундамент

## Цель
Бот запускается, принимает сообщения, проходит онбординг, работает с БД, автоматически стартует через launchd.

## Задачи

### 1.1 Окружение
- Создать виртуальное окружение Python 3.12+
- Установить зависимости: aiogram>=3.4, sqlalchemy[asyncio]>=2.0, asyncpg, apscheduler>=3.10, pydantic-settings, pendulum, alembic, pyyaml
- Создать config.py на базе Pydantic Settings: загрузка .env + config.yaml
- Создать .env и config.yaml из примеров в CLAUDE.md

### 1.2 База данных
- Установить/проверить PostgreSQL 15 + расширения pgvector, pg_trgm
- Создать базу данных `notebook_bot`
- Настроить Alembic
- Создать первую миграцию со ВСЕМИ таблицами из CLAUDE.md (users, tasks, projects, trips, memoir_entries, time_tracking_entries, notes, diary_entries, reminders, llm_queue, prompt_versions, llm_logs)
- Применить миграцию
- Создать db/engine.py с async engine и session factory
- Создать db/models.py со всеми SQLAlchemy моделями

### 1.3 Telegram бот
- Создать main.py: инициализация Bot, Dispatcher, подключение к БД, запуск polling
- Реализовать whitelist middleware: проверка telegram_id по allowed_telegram_ids из конфига. Если пользователя нет в whitelist — вежливый отказ
- Обработчик /start: проверка onboarding_completed → если нет, запуск FSM
- Обработчик /help: справка с примерами фраз

### 1.4 Онбординг (FSM aiogram)
6 шагов, каждый — состояние FSM:

**Шаг 1 — Приветствие:**
```
Привет! 👋 Я — твой персональный ассистент по управлению временем.
Помогу планировать день, следить за задачами и находить баланс.

Как тебя зовут? Или подтверди имя из профиля:
[Использовать "{first_name}"]  [Ввести другое]
```
Сохранить username.

**Шаг 2 — Часовой пояс:**
```
Отлично, {username}! Настроим часовой пояс.
Предполагаю Europe/Moscow — верно?
[Да, верно]  [Изменить]
```
При "Изменить" — принять текстовый ввод.

**Шаг 3 — Время дайджестов:**
```
Утренний дайджест: 08:00
Вечерний итог: 21:00
Вопрос мемуарника: 20:45

Подходит?
[Да, отлично]  [Изменить время]
```

**Шаг 4 — Рабочий график:**
```
Для хронометража нужен рабочий график:
Рабочие дни: Пн-Пт
Время: 09:00 — 18:00

Подходит?
[Да]  [Изменить]  [Пропустить]
```

**Шаг 5 — Знакомство с системой:**
```
Вот три главных концепции, которые мы используем:

🐸 Лягушка — самое неприятное дело дня. Съедаем первой — остаток дня легче.

🐘 Слон — большой проект. Режем на маленькие бифштексы и едим по одному в день.

📔 Мемуарник — каждый вечер я спрошу: что сегодня было самым ярким? Через месяц увидишь свои настоящие ценности.

⏱ Хронометраж — в рабочее время буду спрашивать чем занят. Это помогает понять куда реально уходит время.

🎯 Фокус — когда нужно сосредоточиться, скажи /focus и я не буду беспокоить.

[Понятно, поехали! 🚀]
```

**Шаг 6 — Первая задача:**
```
Хочешь создать первую задачу? Напиши что-нибудь, что нужно сделать — я сохраню.
[Пропустить]
```
При текстовом вводе — создать задачу (пока без LLM, просто title=текст, category=work, priority=normal).

После завершения: onboarding_completed = true, FSM очищается.
Если пользователь прервал — при следующем /start предложить продолжить.

### 1.5 CRUD users
- crud/users.py: get_or_create, update_settings, get_all (для admin)
- При первом обращении пользователя из whitelist — создать запись

### 1.6 LaunchAgent (macOS)
Создать platform/macos/com.notebook-bot.plist:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.notebook-bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/venv/bin/python</string>
        <string>-m</string>
        <string>bot.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/telegram-notebook-bot</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/Users/USERNAME/Library/Logs/notebook-bot/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/USERNAME/Library/Logs/notebook-bot/stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

Создать platform/macos/install.sh:
```bash
#!/bin/bash
# Скрипт установки на macOS
# 1. Создать venv, установить зависимости
# 2. Скопировать .plist с подстановкой путей
# 3. launchctl load
# 4. Создать директорию логов
```

### 1.7 Проверка
- [ ] Бот запускается и отвечает на /start
- [ ] Онбординг проходит все 6 шагов
- [ ] Данные пользователя сохраняются в БД
- [ ] Whitelist работает (незнакомые пользователи получают отказ)
- [ ] При прерывании онбординга — возобновление с последнего шага
- [ ] launchd автоматически запускает бота при загрузке macOS
