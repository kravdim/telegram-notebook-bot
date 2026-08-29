# Независимое приёмочное ревью DailyPlanner

**Дата:** 29 августа 2026 года
**Проверенная ревизия:** `13dca6b` (`main`, `origin/main`)
**Объект проверки:** последние remediation-коммиты, проект целиком, CI/CD,
документация и готовность к демонстрации заказчикам
**Статус:** **сильный production-проект, но пока не принят как безусловно
образцово-показательный**

## 1. Резюме

DailyPlanner заметно превосходит типичный портфолио-проект. У него есть
PostgreSQL как durable source of truth, миграции и schema-drift gate, typed
application boundary, access/privacy controls, восстановление из резервной
копии, production SLO, контейнерный E2E, dependency и secret scanning, SBOM,
проверка образа и воспроизводимый lock-файл.

Текущий `HEAD` технически исправен: обязательный GitHub Actions run полностью
зелёный, локальные статические и security-проверки прошли. Последний коммит
`13dca6b` тематически цельный и содержит regression-тесты.

Однако специалисты заказчика быстро заметят четыре незакрытых вопроса
презентационного уровня: host-specific macOS deployment, невоспроизводимый
README quick start, незавершённую release/tag/evidence chain и чрезмерную
сложность центральных модулей. В последнем коммите также обнаружены пробелы в
NLU-матрице и пограничных сценариях weekend digest.

**Решение:** проект можно демонстрировать как зрелую работающую систему и
сильный release candidate. До заявления «образец нашей инженерной работы»
закрыть P1; до финальной технической демонстрации желательно закрыть P2.

P0-дефектов и признаков компрометации не обнаружено.

## 2. Подтверждённые результаты

### 2.1. GitHub Actions для `13dca6b`

Canonical run:
[`33258286824`](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33258286824).

| Проверка | Результат |
|---|---:|
| CI jobs `quality`, `secrets`, `container-e2e` | PASS |
| PostgreSQL tests | 372 passed, 1 skipped |
| Overall coverage | 70,45% при gate 70% |
| Critical coverage | 85–100% при gate 85% |
| Ruff | PASS |
| mypy | PASS, 103 source files |
| Bandit medium/high | PASS |
| Frozen dependency audit | уязвимостей не найдено |
| Alembic upgrade, single head, schema drift | PASS |
| Backup/restore drill | PASS |
| LLM contracts | parser 6/6, utterances 17/17, invalid tools 0 |
| Gitleaks и tracked-file secret scan | PASS |
| Container readiness, SBOM и Trivy HIGH/CRITICAL | PASS |

### 2.2. Независимые локальные проверки

- рабочее дерево до ревью было чистым, `main` совпадал с `origin/main`;
- Ruff, mypy, Bandit, `pip-audit`, secret scan и LLM contract evaluator — PASS;
- `git diff --check`, shell syntax, Compose config и все macOS plist — PASS;
- Alembic имеет один head: `a6c9d1e4f7b2`;
- локальный тестовый прогон без PostgreSQL: 347 passed, 26 skipped, но общий
  coverage 62,27% не достиг документированного gate 70%; это отдельное
  замечание REV-20260829-007.

## 3. P1 — блокирует статус «образцово-показательный»

### REV-20260829-001 — macOS deployment привязан к локальному proxy

**Риск:** переносимость, production availability, доверие к инструкции
развёртывания.

**Доказательство:** основной production target объявлен macOS LaunchAgent, но
[`platform/macos/com.notebook-bot.plist`](../platform/macos/com.notebook-bot.plist#L29-L34)
безусловно задаёт `HTTP_PROXY`, `HTTPS_PROXY` и `ALL_PROXY` на
`127.0.0.1:1080/1081`. Установщик не спрашивает, существует ли такой proxy, и
не даёт отключить его штатной конфигурацией.

На чистой машине заказчика Telegram и AI HTTP-клиенты могут направить трафик в
несуществующий локальный сервис. При этом README представляет установку как
универсальную команду.

**Требуемое исправление:** сделать proxy необязательной deployment-настройкой;
генерировать EnvironmentVariables из явного профиля либо не добавлять proxy по
умолчанию.

**Критерии приёмки:**

- clean-host установка без proxy запускается и проходит Telegram/provider
  preflight;
- proxy-профиль включается только явным параметром и проверяется до restart;
- в plist отсутствуют site-specific endpoints по умолчанию;
- smoke test проверяет оба профиля и отсутствие секретов в сгенерированном
  plist.

### REV-20260829-002 — README quick start не воспроизводит рабочую БД и env

**Риск:** первая демонстрация и developer experience заканчиваются ошибкой
подключения либо конфигурации.

**Доказательство:** [`README.md`](../README.md#L85-L96) выполняет
`createdb notebook_bot` от имени текущего OS-пользователя, но пример
`DATABASE_URL` подключается как `notebook:password`. Роль `notebook`, пароль и
владение БД инструкция не создаёт. Пример `.env` в
[`README.md`](../README.md#L336-L343) предлагает `GEMINI_API_KEY` и
`DEEPSEEK_API_KEY`, хотя quick start требует MiniMax, актуальный
[`.env.example`](../.env.example) использует `MINIMAX_API_KEY`, а DeepSeek не
входит в поддерживаемый список `validate_runtime_config`.

**Требуемое исправление:** оставить один канонический и автоматически
проверяемый bootstrap-путь: dev Compose либо явное создание PostgreSQL role,
database, password и extensions. Не дублировать env inventory вручную.

**Критерии приёмки:**

- инструкция проходит на чистом поддерживаемом хосте копированием команд;
- созданная роль совпадает с `DATABASE_URL` и владеет нужной БД;
- README, `.env.example`, `config.yaml.example` и runtime validation содержат
  одинаковый inventory провайдеров;
- CI выполняет documentation/bootstrap smoke test без production credentials.

### REV-20260829-003 — release/tag/evidence chain не завершена

**Риск:** невозможно однозначно показать заказчику, какая версия является
принятым и воспроизводимым релизом.

**Доказательство:** `pyproject.toml` и `CHANGELOG.md` объявляют `0.2.0`, но Git
tags отсутствуют. Следовательно, tag-triggered
[`release.yml`](../.github/workflows/release.yml) ещё не создавал release SBOM,
checksums и provenance для конкретной версии. Последний handoff
[`SESSION_CONTEXT_2026-08-28.md`](SESSION_CONTEXT_2026-08-28.md#L5-L13)
подтверждает production deployment `b147525`, тогда как текущий `HEAD` —
`13dca6b`. Изменения weekend digest и task-list routing также не отражены в
`CHANGELOG.md`.

**Требуемое исправление:** после acceptance текущего кода выпустить новую
согласованную версию и связать commit, CI, live E2E, recovery evidence и
release artifacts.

**Критерии приёмки:**

- версия согласована между `pyproject.toml`, CHANGELOG и annotated tag;
- GitHub Release ссылается на точный SHA и содержит release notes;
- tag workflow публикует SBOM, checksums и provenance с достаточным сроком
  хранения;
- production deploy, live Telegram gate и recovery drill выполнены на том же
  SHA;
- документация явно различает current source HEAD и deployed release.

### REV-20260829-004 — центральные модули сохраняют чрезмерную сложность

**Риск:** сопровождаемость, reviewability, высокий regression radius; особенно
заметно при технической оценке заказчиком.

**Доказательство:**

- `bot/handlers/messages.py` — 1758 строк;
- `bot/handlers/commands.py` — 1223 строки;
- `bot/llm/dispatcher.py` — 1123 строки;
- `_process_text_message_unlocked` имеет cyclomatic complexity 60;
- проверка с conventional threshold 10 выявляет 28 production/ops функций выше
  порога;
- проектный gate в [`pyproject.toml`](../pyproject.toml#L63-L72) допускает
  complexity 60, 70 branches, 25 returns и 300 statements — то есть фиксирует
  текущий максимум, но почти не ограничивает рост обычных функций;
- mypy одновременно использует `ignore_missing_imports = true` и
  `no_implicit_optional = false`.

Архитектурный документ честно фиксирует compatibility executors, но для
показательного проекта одного описания долга недостаточно.

**Требуемое исправление:** отделить Telegram orchestration, deterministic
recognizers, application use cases и presentation adapters. Переносить
бизнес-ветки в маленькие typed services, вызываемые без Telegram/LLM объектов.

**Критерии приёмки:**

- central message pipeline разделён на явные use cases/state handlers;
- новые функции ограничены complexity 10–15, существующий ratchet поэтапно
  снижен хотя бы до 15–20;
- `dispatcher.py` перестаёт быть хранилищем всех compatibility workflows;
- архитектурные границы проверяются import/layer tests;
- mypy strictness повышается по модулям без массовых необоснованных ignore.

## 4. P2 — обязательная доводка перед финальной демонстрацией

### REV-20260829-005 — новый task-list recognizer имеет недостижимые scope

**Доказательство:** [`_extract_task_list_scope`](../bot/handlers/messages.py#L1061-L1074)
возвращает `overdue` и `done_today` только после совпадения с общим списком
regex. Однако сами patterns не принимают «Покажи просроченные задачи», «Какие
задачи выполнены сегодня?» или «Что выполнено сегодня?». Независимая матрица
дала `None` для этих фраз. Добавленный regression-тест
[`test_followup_task_list_question_bypasses_llm`](../tests/test_message_handler_scenarios.py#L128-L163)
проверяет только один `today` happy path.

**Рекомендация:** сделать parametrized contract matrix для `today`, `all`,
`overdue`, `done_today`, морфологических вариантов, punctuation и negative
examples. Убрать недостижимый код либо расширить patterns. Read-only intent
также не следует называть `common_mutation`.

### REV-20260829-006 — weekend digest не покрывает пустые пограничные состояния

**Доказательство:** formatter скрывает frog и projects в выходной, но empty-state
проверяет исходные `frog/projects`. При `tasks=[]` и существующем скрытом frog
или project пользователь может получить только заголовок:
[`bot/formatters/digest.py`](../bot/formatters/digest.py#L47-L80).
Последний коммит изменил политику work/personal task filtering, но тест
[`test_portfolio_utilities.py`](../tests/test_portfolio_utilities.py#L325-L364)
проверяет только заполненный список.

**Рекомендация:** сформулировать одну domain policy выходного дня и покрыть
матрицу: no tasks, personal-only, work-only, mixed, overdue, frog, projects,
trip и birthdays. Empty-state должен вычисляться из реально отображённых
секций.

### REV-20260829-007 — локальная инструкция тестирования не проходит свой gate

**Доказательство:** команда из
[`CONTRIBUTING.md`](../CONTRIBUTING.md#L6-L18) без отдельной PostgreSQL среды
получает 347 passed / 26 skipped и coverage 62,27%, после чего падает против
`fail_under = 70`. Только CI с `RUN_DB_TESTS=1` получает 70,45%.

**Рекомендация:** добавить один канонический `make test` или script, который
поднимает disposable pgvector PostgreSQL, применяет миграции, задаёт
`RUN_DB_TESTS=1`, запускает оба coverage gate и гарантированно удаляет среду.
Если нужны быстрые unit tests — оформить отдельные команды и отдельный честный
threshold.

### REV-20260829-008 — активная документация содержит исторические ссылки и статусы

**Доказательство:** активный
[`REVIEW_2026-08-26_INDEPENDENT.md`](archive/reviews/REVIEW_2026-08-26_INDEPENDENT.md)
ссылается на удалённый `platform/linux/install.sh` и несуществующий
`bot/jobs/task_reminders.py`. Индекс до этого ревью называл старый
post-remediation документ «текущим пакетом». Исторические session context
содержат абсолютные пути локальной машины и могут восприниматься как актуальные
операционные инструкции.

**Рекомендация:** перенести завершённые review/session evidence в `archive/`
либо использовать commit permalinks; добавить markdownlint и local-link checker
в CI; поддерживать один current status/handoff документ.

### REV-20260829-009 — main допускает красные промежуточные коммиты

**Доказательство:** перед финальным исправлением в `main` были последовательно
отправлены `6e8a5be`, `48655a5` и `75db606` с failed CI. Последующие коммиты
вернули ветку в зелёное состояние, но required checks не защищали саму main от
публикации красного состояния.

**Рекомендация:** включить protected branch/ruleset: изменения через PR,
required CI checks до merge, минимум одно review approval, запрет force push,
CODEOWNERS для security/deployment зон и PR template с evidence checklist.

### REV-20260829-010 — test milestone имеет минимальный запас покрытия

**Доказательство:** overall result 70,45% превышает обязательный 70% только на
0,45 процентного пункта. Commit `b147525` добавил более 3000 строк в основном
mock-heavy portfolio tests; метрика закрыта, но уязвима к небольшому production
изменению и сама по себе не доказывает качество проверяемого поведения.

**Рекомендация:** не повышать процент механически. Добавить mutation testing для
application/domain модулей, contract/property tests для recognizers и
state-machine tests для interaction workflows. Порог повышать после выделения
use cases, ориентируясь на mutation score и critical behavior, а не только line
coverage.

## 5. Внешние release gates

Эти пункты нельзя закрыть только изменением репозитория, но их нужно явно
завершить либо документированно вывести из scope демонстрации:

1. юридическая проверка privacy notice, consent и retention для целевого рынка;
2. native STT resource drill на 20 транскрипций на целевом production-class
   Mac;
3. полный live Telegram E2E со state oracle и zero-residue cleanup на текущем
   release SHA;
4. post-deploy preflight, SLO snapshot и recovery drill на том же SHA;
5. tag-triggered release evidence и проверка доступности артефактов.

## 6. Рекомендуемый порядок работ

1. Исправить clean-host bootstrap и убрать обязательный localhost proxy.
2. Разделить центральный message/dispatcher pipeline и снизить complexity
   ratchet.
3. Закрыть task-list и weekend matrices; синхронизировать CHANGELOG.
4. Сделать локальный PostgreSQL quality gate одной воспроизводимой командой.
5. Очистить актуальный documentation set, включить link/style gates.
6. Настроить protected main и PR evidence workflow.
7. Выполнить legal/STT/live/recovery gates на одном SHA.
8. Выпустить versioned tag и GitHub Release с SBOM/checksums/provenance.

## 7. Критерий финальной приёмки

Проект можно назвать образцово-показательным, когда:

- P1 закрыты кодом, тестами и актуальной документацией;
- clean-host install и local quality gate воспроизводятся независимым
  инженером;
- центральная архитектура не требует функций complexity 60 и файлов на
  1000–1700 строк для обычного изменения;
- current SHA имеет зелёные CI, live E2E, production SLO и recovery evidence;
- версия, tag, CHANGELOG, deployed revision и release artifacts указывают на
  один и тот же commit;
- внешние privacy/STT gates закрыты либо явно ограничены согласованным scope.
