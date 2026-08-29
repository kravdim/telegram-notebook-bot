# Независимое приёмочное ревью DailyPlanner

**Дата:** 26 августа 2026 года<br>
**Ревизия:** `935ebbe` (`main`, поверх проверенного кодового коммита `5fa100c`)<br>
**Роль проверяющего:** технический заказчик, senior code reviewer, security/privacy reviewer и конечный пользователь<br>
**Статус:** **не принят как образцовый production/portfolio-релиз; условно принят как сильная beta-версия**

## 1. Резюме для заказчика

DailyPlanner — уже не прототип. Сервис запускается с нуля, миграции и PostgreSQL работают, текущий production-инстанс здоров, автоматические проверки зелёные, а реальный Telegram-прогон прошёл 82 из 82 сценариев. В проекте заметно больше эксплуатационной зрелости, чем обычно бывает у портфолио-проектов: есть резервное копирование и restore drill, delivery outbox, миграции, privacy runbook, мониторинг SLO, типизация, статический анализ и воспроизводимый lock-файл.

Тем не менее продукт пока нельзя честно показывать как «образцово идеальный» и передавать платящему заказчику без оговорок. Обнаружены семь блокирующих приёмку проблем уровня P1. Наиболее существенные из них:

1. разрешённый пользователь может вызвать приватные команды в групповом чате и непреднамеренно раскрыть свои задачи или экспорт;
2. аварийное логирование LLM способно записать исходный пользовательский текст вопреки настройке `store_llm_payloads: false`;
3. `/export` не является полным экспортом пользовательских данных;
4. пользователь не получает понятного privacy disclosure о передаче чувствительного текста внешним AI-провайдерам;
5. история диалога не ограничивается на ряде ранних веток возврата и может бесконтрольно расти;
6. live E2E-gate проверяет преимущественно текст ответа и способен пропустить ошибку фактической записи в БД;
7. заявленная установка systemd с чистого хоста не доводит конфигурацию до реально запускаемого состояния.

**Решение заказчика:** принять текущую сборку для демонстрации разработчиком и контролируемого личного использования, но не принимать как финальный оплачиваемый релиз. После закрытия P1, повторного security/privacy review и усиления тестового oracle решение можно пересмотреть. Для статуса «образцовый портфолио-проект» также должны быть закрыты P2.

## 2. Шкала приоритетов

| Приоритет | Смысл |
|---|---|
| P0 | критический инцидент: эксплуатацию нужно остановить немедленно |
| P1 | блокирует приёмку и публичное позиционирование как production-ready |
| P2 | обязательно для уровня «образцовый портфолио-проект» |
| P3 | полировка, воспроизводимость и контролируемый технический долг |

P0-дефектов и признаков активной компрометации не обнаружено.

## 3. Что и как проверялось

Проверка выполнялась по следующему плану:

1. инвентаризация репозитория, истории, зависимостей, конфигурации и границ продукта;
2. архитектурный разбор обработчиков, application/domain-логики, persistence, фоновых задач и интеграций;
3. security/privacy review: доступ, user scoping, секреты, логи, HTML, prompt injection, supply chain, удаление и экспорт данных;
4. UX review основных Telegram-сценариев, онбординга, ошибок, настроек и нетекстовых сообщений;
5. анализ тестов, документации, CI/CD, backup/restore и эксплуатационных инструкций;
6. локальный unit/integration прогон, линтеры, типизация, SAST, dependency audit и contract eval;
7. проверка production preflight/SLO, реальный E2E через авторизованный userbot и Docker cold-start;
8. сопоставление заявленного поведения с фактическим кодом и формирование backlog.

### 3.1. Фактические результаты

| Проверка | Результат | Комментарий |
|---|---:|---|
| Pytest + coverage | PASS | 209 passed, 17 skipped; 46,07% total coverage |
| PostgreSQL integration локально | SKIP | 17 тестов требуют отдельной integration-БД; на соответствующем кодовом SHA GitHub CI зелёный |
| Ruff (проектная конфигурация) | PASS | ошибок нет |
| mypy | PASS | проверены 98 production/ops-файлов |
| Bandit medium/high | PASS | значимых находок нет |
| `pip-audit` по frozen production deps | PASS | известных уязвимостей не найдено |
| Secret scan tracked-файлов | PASS | проверены 206 файлов |
| `compileall` | PASS | синтаксических ошибок нет |
| LLM contract evaluator | PASS | parser 6/6, utterance 17/17, invalid tool 0 |
| Markdown local links | PASS | отсутствующих локальных ссылок: 0 |
| Shell syntax | PASS | проверены `.sh`-скрипты |
| GitHub Actions | PASS | кодовый SHA `5fa100c` прошёл CI; HEAD содержит только documentation commit с `[skip ci]` |
| Production preflight | PASS | БД доступна, миграция `f4b8c2d6e1a0` |
| Production SLO | PASS | reminder lag 0 сек.; pending 0; backup age около 2,1 ч., checksum/metadata OK |
| Live Telegram E2E | PASS | 82/82 за 779 сек.; run `DP-20260826T202755-8ad5b3`; teardown выполнен |
| Docker cold-start | PASS | clean build, PostgreSQL healthy, bot healthy, healthcheck подтвердил PID и миграцию; тестовые контейнеры и тома удалены |

Live-отчёт создан runner-ом вне репозитория: `/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260826_234058.md`.

## 4. Сильные стороны

- Конфигурация и доступ по умолчанию fail-closed: пустой allowlist не превращает бота в публичный сервис; опасный override выделен явно.
- CRUD и tool-dispatch в основном последовательно ограничивают запросы `user_id`; явных cross-tenant запросов в основном пользовательском потоке не найдено.
- Для Telegram-разметки применяется централизованное экранирование, а live-набор содержит XSS и prompt-injection сценарии.
- Мутации, напоминания и составная доставка спроектированы значительно надёжнее обычного pet-проекта: есть idempotency/processed requests, leases, durable batches и сформулированная модель at-least-once.
- Есть singleton/advisory lock, preflight, миграционный drift-check, backup checksum, restore drill, least-privilege recovery procedure и журнал удаления данных.
- Архитектурный документ честно фиксирует оставшийся долг вокруг compatibility executors, а не маскирует его.
- Зависимости зафиксированы lock-файлом; lint, types, SAST, secret scan, audit и CI проходят.
- Реальная среда работоспособна: команды, напоминание, snooze, голос, экспорт, онбординг, инъекции и cleanup прошли сквозной сценарий.

## 5. Блокирующие замечания P1

### REV-001 — Приватные данные могут уйти в групповой чат

**Риск:** высокий, confidentiality/privacy.

**Доказательство:** middleware проверяет только `from_user` и allowlist ([`bot/middleware.py`](../bot/middleware.py#L31-L53)). Ограничения на `ChatType.PRIVATE` или эквивалентного глобального фильтра нет. Private scope в [`bot/main.py`](../bot/main.py#L188-L208) ограничивает отображение меню команд, но не вызов handlers. `/tasks`, `/today`, `/birthdays` и особенно `/export` отвечают в текущий чат ([`bot/handlers/commands.py`](../bot/handlers/commands.py#L144-L161), [`bot/handlers/commands.py`](../bot/handlers/commands.py#L1169-L1232)).

Разрешённый пользователь может добавить бота в группу и случайно опубликовать там личные задачи, дневник или ZIP-архив. Тестов middleware для group/channel/anonymous sender нет.

**Требуемое исправление:** добавить глобальную fail-closed проверку приватного чата для сообщений и callback query. Исключения, если они действительно нужны, должны быть явно перечислены и не иметь доступа к персональным данным.

**Критерии приёмки:**

- message и callback из group/supergroup/channel не доходят до бизнес-handlers;
- callback дополнительно проверяет тип исходного чата;
- тесты покрывают group, supergroup, channel post, anonymous admin и отсутствие `from_user`;
- `/export` и административные команды невозможно направить в общий чат;
- приватные сценарии и live gate остаются зелёными.

### REV-002 — Raw LLM payload попадает в обычные логи

**Риск:** высокий, sensitive-data leakage.

**Доказательство:** при malformed arguments логируются первые 200 символов исходных аргументов ([`bot/llm/dispatcher.py`](../bot/llm/dispatcher.py#L99-L114)). Pydantic `ValidationError` и общий exception также логируются целиком ([`bot/llm/dispatcher.py`](../bot/llm/dispatcher.py#L150-L160)); такие сообщения часто содержат входные значения. Это обходит аккуратную metadata-only запись в [`bot/db/crud/llm_logs.py`](../bot/db/crud/llm_logs.py#L41-L61) и противоречит смыслу `store_llm_payloads: false`.

**Требуемое исправление:** ввести централизованную redaction-политику для application logs. Хранить только tool name, имена полей, безопасный error code, request/run ID и размер payload — без значений.

**Критерии приёмки:**

- canary-строки из title/note/diary/transcript не появляются в логах при malformed JSON, validation error, provider exception и retry;
- `exc_info` не раскрывает request body или validated input;
- `store_llm_payloads: false` имеет сквозной автоматический тест;
- privacy-документация перечисляет допустимые поля журналирования и сроки хранения логов.

### REV-003 — `/export` не экспортирует все данные пользователя

**Риск:** высокий, data portability/trust.

**Доказательство:** команда экспортирует только tasks, notes, diary, memoirs и birthdays ([`bot/handlers/commands.py`](../bot/handlers/commands.py#L1185-L1224)). Не включены projects, trips, reminders, time-tracking/chronometry, settings/profile и связанные пользовательские метаданные. Мемуары запрашиваются с лимитом 365. День рождения без года сериализуется с искусственным 1900 годом ([`bot/handlers/commands.py`](../bot/handlers/commands.py#L76-L80)). Существующие тесты проверяют в основном архивирование/UTF-8, но не полноту состава.

**Требуемое исправление:** определить канонический inventory пользовательских таблиц и сделать полный версионированный экспорт без скрытого усечения.

**Критерии приёмки:**

- архив содержит manifest со schema version, временем, пользователем и перечнем наборов;
- экспортируются все user-owned сущности, перечисленные также в deletion workflow;
- большие наборы выгружаются полностью либо с явно согласованной пагинацией/частями;
- неизвестный год рождения остаётся неизвестным, а не превращается в 1900;
- integration-тест заполняет каждую пользовательскую таблицу и доказывает полноту и отсутствие чужих данных.

### REV-004 — Нет пользовательского privacy disclosure и согласия на cloud AI

**Риск:** высокий, privacy/compliance/product trust.

**Доказательство:** [`docs/PRIVACY.md`](PRIVACY.md) описывает в основном хранение и операторские процедуры. В онбординге нет ясного сообщения, что задачи, заметки, дневник, голос или embeddings могут обрабатываться MiniMax/OpenAI/Gemini/Zhipu/другими выбранными провайдерами. Нет доступной пользователю `/privacy`, понятного data deletion/export workflow или выбора cloud processing.

**Требуемое исправление:** согласовать privacy notice с фактической конфигурацией провайдеров и встроить его в продукт до ввода чувствительных данных.

**Критерии приёмки:**

- до первого cloud-вызова пользователь видит категории данных, цели, провайдеров/получателей, сроки хранения и способы export/delete;
- есть постоянно доступная команда/экран privacy;
- отказ от cloud processing имеет предсказуемое поведение или явно блокирует соответствующие функции;
- документация и UI генерируются из согласованного inventory интеграций;
- текст проходит отдельную юридическую проверку для целевого рынка.

### REV-005 — История диалога может расти без ограничения

**Риск:** высокий, availability/cost/privacy.

**Доказательство:** в [`bot/handlers/messages.py`](../bot/handlers/messages.py#L313) много deterministic и error-веток добавляют сообщения в контекст и делают ранний `return`. Единственный trim находится близко к завершению LLM-ветки ([`bot/handlers/messages.py`](../bot/handlers/messages.py#L865-L869)). При недоступном/ошибающемся провайдере контекст также пополняется, но не обрезается. Следующий LLM-запрос получает `get_history()` до гарантированной нормализации. Сам [`bot/llm/context.py`](../bot/llm/context.py#L39-L67) поддерживает trim только при явном вызове.

**Требуемое исправление:** перенести bounded-history invariant внутрь API контекста и обрезать историю до формирования каждого запроса, а не только в одном happy path.

**Критерии приёмки:**

- любое добавление пары user/assistant сохраняет установленный лимит;
- ранние return, deterministic commands, provider outage, validation error и cancellation покрыты тестами;
- стресс-тест после сотен сообщений подтверждает предел элементов/токенов и памяти;
- в provider request никогда не передаётся история сверх лимита;
- метрика размера контекста позволяет заметить регрессию без логирования содержимого.

### REV-006 — Live E2E даёт ложную уверенность в мутациях

**Риск:** высокий, quality gate integrity.

**Доказательство:** runner считает `has_substring` успешным, если найден любой из вариантов, то есть применяет OR. Несколько кейсов заявляют две-три мутации, но подтверждают одно слово в ответе. Например «заметка + задача» может пройти по одному упоминанию; сценарий «три намерения» не проверяет три созданные сущности. Независимого DB/API oracle и проверки точного изменения состояния нет. Текущий результат 82/82 поэтому подтверждает связность UI, но не полноту side effects.

**Требуемое исправление:** превратить live-набор в state-verifying acceptance gate.

**Критерии приёмки:**

- multi-intent кейсы используют AND assertions и проверяют точное число эффектов;
- мутации подтверждаются независимым read-back через UI/API либо read-only DB oracle;
- проверяются не только созданные, но и отсутствующие/удалённые/неизменённые сущности;
- teardown отдельно доказывает нулевой остаток тестового namespace;
- отчёт различает `response matched`, `state verified` и `cleanup verified`;
- ложноположительный намеренно сломанный handler заставляет gate упасть.

### REV-007 — systemd installer не создаёт работоспособную конфигурацию с чистого хоста

**Риск:** высокий, deployability/acceptance.

**Доказательство:** README обещает установку одной командой, однако [`platform/linux/install.sh`](../platform/linux/install.sh#L1-L77) при отсутствии `.env` копирует placeholder и фактически настраивает только DB URL. Скрипт не создаёт законченный `config.yaml`, не получает реальный bot token, allowlist и provider configuration. Preflight и приложение эти значения требуют. При этом Linux-путь тянет extra `stt` и локальную Whisper-модель, хотя документация позиционирует cloud adapters; default-конфигурация также предполагает локальные компоненты, которые installer не поднимает.

**Требуемое исправление:** либо убрать systemd как поддерживаемый deployment target, либо сделать его полностью воспроизводимым и fail-fast.

**Критерии приёмки:**

- disposable VM устанавливает сервис по опубликованной инструкции без ручного редактирования скрытых файлов;
- placeholders, пустой allowlist и недоступный provider блокируют `systemctl start` понятной ошибкой;
- явно выбран один поддерживаемый профиль: cloud или local, с полным набором зависимостей;
- installer проверяет итоговый health/status, а не только успешность команды `systemctl start`;
- CI или периодический release-test воспроизводит clean-host install.

## 6. Замечания P2

### REV-008 — Покрытие и сложность ниже образцового уровня

Общее покрытие составляет 46,07% при пороге 45%. Особенно слабо покрыты `main` (18%), dispatcher (37%), commands (36%), middleware (29%), backup (19%), task reminders (46%) и local Whisper (0%). Дополнительный complexity-прогон дал десятки замечаний; `_process_text_message_unlocked` имеет цикломатическую сложность около 59, `_handle_create_task` — около 43, `main` — около 43. Файлы handlers/messages, commands и dispatcher стали крупными coordination-монолитами.

**Задача:** разделить transport, use cases и presentation, закончить вынос compatibility executors, поднять coverage минимум до 70% overall и 85% для access/privacy/export/delivery/reminder paths. Включить разумные C90/PLR-пороги в CI с постепенным ratchet.

### REV-009 — Supply-chain и release provenance неполны

GitHub Actions используют плавающие major tags (`checkout@v5`, `setup-uv@v6`, `gitleaks@v2`), базовые образы и pgvector не везде закреплены digest-ом. Нет автоматического dependency updater, SBOM, подписи/attestation релиза и container scan. В `pyproject.toml` указана версия 0.2.0, но нет соответствующего release tag, а опубликованные изменения остаются в секции Unreleased.

**Задача:** закрепить Actions и images по SHA/digest с Dependabot/Renovate, генерировать CycloneDX/SPDX SBOM, сканировать итоговый image, выпускать подписанный/tagged artifact и привести changelog к релизной дисциплине.

### REV-010 — Контейнер запускается с избыточными правами

[`platform/linux/Dockerfile`](../platform/linux/Dockerfile) не задаёт `USER`, поэтому application process работает как root. В compose не зафиксированы `cap_drop`, read-only root filesystem, resource/pid limits и log rotation.

**Задача:** добавить непривилегированного UID/GID, минимальные capabilities, writable tmpfs/volumes только там, где нужны, лимиты и ротацию. Cold-start/backup/restore должны пройти после hardening.

### REV-011 — Документация расходится с кодом

- README обещает напоминания о задачах в 09:00, 11:00, 13:00, 15:00 и 17:00, а [`bot/jobs/task_reminders.py`](../bot/jobs/task_reminders.py#L20-L21) содержит только 11/13/15/17.
- README указывает 14 function tools, фактически их 15, включая `clarify_request`.
- scheduler docstring упоминает APScheduler, но production использует собственные циклы; пакет `apscheduler` остаётся зависимостью без реального использования.

**Задача:** назначить код/config единым источником истины, генерировать или тестировать перечисления/расписания, удалить неиспользуемую зависимость либо действительно применять её.

### REV-012 — Неподдерживаемые типы сообщений молча игнорируются

Catch-all handler в [`bot/handlers/messages.py`](../bot/handlers/messages.py#L879-L885) без ответа возвращается для фото, документов, стикеров и прочих нетекстовых типов. Для пользователя это выглядит как зависание или потеря данных.

**Задача:** дать короткий безопасный ответ с поддерживаемыми форматами; отдельно определить продуктовую политику для файлов/фото. Добавить UX-тесты.

### REV-013 — Настройка timezone непоследовательна

Онбординг принимает произвольную IANA timezone, а последующая настройка предлагает только восемь российских зон ([`bot/handlers/onboarding.py`](../bot/handlers/onboarding.py#L212-L235), [`bot/handlers/commands.py`](../bot/handlers/commands.py#L857-L891)). Пользователь с другой валидной зоной не может нормально вернуть или изменить её.

**Задача:** поддержать ввод/поиск любой IANA timezone в settings либо явно ограничить географию продукта и одинаково валидировать её во всех путях.

### REV-014 — `/stats frogs` строит границы дня без timezone пользователя

[`bot/handlers/commands.py`](../bot/handlers/commands.py#L624-L649) получает пользовательскую дату, но затем создаёт timestamp из naive `datetime.combine`. Это способно интерпретировать локальную полночь как UTC и сместить статистику для не-UTC зон.

**Задача:** создавать aware local boundaries в timezone пользователя и переводить их в UTC перед запросом. Нужны тесты для UTC±, DST и событий рядом с полуночью.

### REV-015 — Часть доменных инвариантов существует только в handlers

В БД есть полезные ограничения status/category, но не хватает `CHECK` для очевидных условий: `end_date >= start_date`, положительные duration/interval, допустимые work days и другие числовые границы. Обход handler или ошибка импорта способна сохранить некорректное состояние.

**Задача:** составить inventory доменных инвариантов, закрепить критичные правила на уровне schema/migrations и добавить migration/integration tests.

### REV-016 — Логи администрирования содержат полные Telegram ID

При изменении allowlist логируется полный список идентификаторов ([`bot/handlers/admin.py`](../bot/handlers/admin.py#L307)). Для эксплуатационной диагностики достаточно количества, change/request ID и при необходимости необратимого hash.

**Задача:** минимизировать персональные идентификаторы в логах и включить этот сценарий в canary/redaction tests из REV-002.

## 7. Замечания P3

### REV-017 — Live trip-кейсы привязаны к фиксированной дате

Userbot runner использует диапазон `25.08–28.08`. После окончания диапазона кейс «текущая поездка» станет календарно нестабилен. Нужно генерировать даты относительно даты запуска и фиксировать timezone теста.

### REV-018 — Временные voice/log artifacts не имеют lifecycle

В `/private/tmp/dp_test_voices` на момент проверки накопилось 113 синтетических аудиофайлов общим объёмом около 16 МБ; каталог/файлы доступны шире необходимого. Неуспешные wrapper-логи также остаются в `/private/tmp` без retention policy.

**Задача:** использовать run-specific `mktemp` с mode 0700, удалять artifacts в `finally`, сохранять только явно запрошенный failure bundle и регулярно чистить старые данные.

### REV-019 — Packaging metadata не завершены

В `pyproject.toml` не хватает полного набора project URLs, author/maintainer, license/readme/classifiers. Для внутреннего приложения это не runtime-дефект, но для публичного portfolio/release снижает профессиональную завершённость.

### REV-020 — Нужен контроль утечки ресурсов PyAV/STT

Во время локальных проверок наблюдалось предупреждение `resource_tracker` об оставшемся semaphore. Оно не повлияло на успешность live voice-сценариев, но требует воспроизводимого теста многократной транскрибации и проверки освобождения процессов/IPC ресурсов.

## 8. Backlog разработчику

| Очередь | ID | Результат задачи | Зависимости |
|---:|---|---|---|
| 1 | REV-001 | глобальный private-chat boundary + негативные transport tests | — |
| 1 | REV-002, REV-016 | единая redaction policy и canary-тесты всех error paths | — |
| 1 | REV-003 | полный версионированный data export с integration oracle | inventory из delete workflow |
| 1 | REV-004 | фактический privacy notice, consent/choice, export/delete UX | legal/product decision |
| 1 | REV-005 | bounded context invariant и stress tests | — |
| 1 | REV-006 | state-verifying live gate и проверяемый teardown | тестовая read-only роль/API |
| 1 | REV-007 | воспроизводимый systemd install либо снятие target с поддержки | deployment profile decision |
| 2 | REV-008 | декомпозиция application layer и coverage ratchet | после P1, чтобы тесты фиксировали новый контракт |
| 2 | REV-009 | pinned supply chain, updater, SBOM, scan, signed release | release pipeline |
| 2 | REV-010 | non-root/hardened container | повторить smoke и recovery |
| 2 | REV-011 | синхронизация README/config/code и cleanup dependencies | — |
| 2 | REV-012, REV-013 | ясная UX-обратная связь и единая timezone-модель | product decision |
| 2 | REV-014, REV-015 | timezone correctness и DB invariants | migrations |
| 3 | REV-017, REV-018 | детерминированный и чистый userbot runner | REV-006 |
| 3 | REV-019 | release-grade package metadata | REV-009 |
| 3 | REV-020 | STT resource lifecycle test/fix | — |

## 9. Рекомендуемый порядок повторной приёмки

1. Закрыть REV-001–REV-005 и провести targeted security/privacy regression.
2. Усилить oracle по REV-006; только после этого считать 82/82 доказательством бизнес-поведения.
3. Принять решение по systemd target и закрыть REV-007.
4. Выполнить P2, поднять quality gates и собрать release candidate из чистого checkout.
5. На release candidate повторить unit/integration, migration drift, dependency/SAST scan, container cold-start, backup/restore и live E2E.
6. Выпустить tag, changelog, SBOM и зафиксировать evidence bundle с SHA артефакта.

## 10. Definition of Done для статуса «образцовый релиз»

- все P1 и P2 закрыты кодом, тестами и актуальной документацией;
- нет raw user content или Telegram ID в application logs по canary suite;
- transport boundary запрещает утечку данных в неприватные чаты;
- export и delete опираются на один проверяемый inventory пользовательских данных;
- live gate доказывает состояние БД, а не только похожий текст ответа;
- coverage не ниже 70% overall и 85% для критичных privacy/access/delivery модулей;
- оба заявленных deployment target воспроизводятся с чистого окружения либо неподдерживаемый target удалён из обещаний;
- container работает non-root и проходит cold-start, healthcheck и recovery;
- privacy notice соответствует реально выбранным провайдерам и доступен до обработки данных;
- release имеет tag, changelog, SBOM, dependency/container scan и привязку к проверенному commit SHA;
- финальный live run завершается без остаточных данных и временных artifacts.

---

Это ревью является независимым срезом фактического состояния репозитория и среды на указанную дату. Зелёные проверки подтверждают текущую работоспособность, но не отменяют блокирующие архитектурные и privacy-риски, перечисленные выше.
