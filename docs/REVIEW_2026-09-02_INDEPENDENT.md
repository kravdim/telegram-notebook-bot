# Повторное независимое приёмочное ревью DailyPlanner

**Дата:** 2 сентября 2026 года
**Проверенная ревизия:** `e590d32` (`main`, `origin/main`)
**Базовое ревью:**
[`REVIEW_2026-08-29_INDEPENDENT.md`](REVIEW_2026-08-29_INDEPENDENT.md)
**Проверенный remediation-пакет:** `5c92b2d`, `1444b2a`, `e373b8b`, `e590d32`
**Статус:** **сильный production release candidate, но пока не принят как
безусловно образцово-показательный проект**

## 1. Решение по повторной приёмке

Remediation от 29 августа дала проекту измеримый инженерный прирост:

- создан воспроизводимый Docker-backed developer bootstrap;
- появился единый локальный PostgreSQL test/coverage gate;
- обязательный localhost proxy удалён из macOS LaunchAgent;
- task-list recognizer вынесен в provider-independent application module;
- weekend digest получил отдельную behavioral matrix;
- message pipeline разделён на явные фазы;
- глобальные complexity limits снижены;
- историческая документация архивирована, а активные ссылки проверяются в CI;
- добавлены CODEOWNERS и PR evidence template.

Текущий `HEAD` и production evidence зелёные. P0-дефектов, активной
компрометации, tracked secrets или schema drift не обнаружено.

Повторная приёмка тем не менее выявила новый пользовательский регресс в
task-list recognizer и подтвердила, что release governance, staged deployment и
структурная декомпозиция закрыты лишь частично. Поэтому проект уже можно
демонстрировать как зрелую работающую систему, но пока нельзя честно
позиционировать как завершённый эталон инженерного процесса.

**Условие приёмки:** закрыть P1 перед демонстрацией техническим специалистам
заказчика. P2 закрыть до финального versioned release либо явно оформить как
согласованный ограниченный scope.

## 2. Проверки и evidence

### 2.1. Canonical GitHub Actions

Текущий `HEAD` прошёл run
[`33277427058`](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33277427058).
Последний исполняемый commit `e373b8b` прошёл run
[`33276340956`](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33276340956).

| Проверка | Результат |
|---|---:|
| CI `quality`, `secrets`, `developer-bootstrap`, `container-e2e` | PASS |
| PostgreSQL test gate | 401 passed, 1 skipped |
| Overall coverage | 71,07% при gate 70% |
| Critical coverage | 85–100% при gate 85% |
| Быстрый suite без external DB | 376 passed, 26 skipped |
| Ruff | PASS |
| mypy | PASS, 105 source files |
| Bandit medium/high | PASS в canonical CI |
| Frozen dependency audit | известных уязвимостей нет в canonical CI |
| Tracked-file secret scan | PASS, 240 файлов |
| Documentation contract | PASS, 10 active documents |
| Alembic upgrade/check/single head | PASS, `a6c9d1e4f7b2` |
| Container readiness, SBOM, Trivy | PASS |
| Backup/restore и production live gate | PASS по handoff 30.08 |

### 2.2. Независимый локальный прогон

В ходе повторного аудита выполнены:

- `scripts/run_local_test_gate.sh` с одноразовым pgvector PostgreSQL;
- Ruff и mypy по production/ops-коду;
- documentation contract и secret scan;
- shell syntax и plist validation;
- Alembic fresh upgrade и ORM/schema drift check;
- adversarial task-query matrix вне существующих regression-тестов;
- проверка GitHub tags, releases, rulesets и branch protection;
- complexity audit с учётом строк, скрытых inline `noqa`.

Одноразовые контейнеры и volumes после проверки удалены. Рабочее дерево
осталось чистым.

## 3. Состояние замечаний прошлого ревью

| Замечание от 29.08 | Состояние повторной проверки |
|---|---|
| REV-001, localhost proxy | Закрыто на уровне шаблона и renderer tests |
| REV-002, developer DB/bootstrap | Основная DB-проблема закрыта; полный local profile требует P2-доводки |
| REV-003, version/tag/release | Не закрыто |
| REV-004, центральная сложность | Частично закрыто фазами и ratchet; mega-modules и hotspots сохранены |
| REV-005, task-list recognizer | Старые scopes закрыты, но найден новый qualifier regression |
| REV-006, weekend digest | Закрыто behavioral matrix |
| REV-007, локальный test gate | Закрыто локально; CI не исполняет сам canonical script |
| REV-008, активная документация | Основная проблема закрыта |
| REV-009, protected main | Репозиторные файлы добавлены, внешняя защита отсутствует |
| REV-010, запас/качество покрытия | Line coverage улучшен; risk-based и mutation gates не завершены |

## 4. P1 — блокирует статус «образцово-показательный»

### REV-20260902-001 — task-list recognizer теряет дату и контекст запроса

**Риск:** высокий, product correctness и доверие пользователя.

**Доказательство:**
[`extract_task_list_scope`](../bot/application/task_query_recognizer.py#L23-L44)
распознаёт сигнал списка и затем по умолчанию возвращает `today`. Он не
отвергает temporal/context qualifiers, которые текущий `TaskListScope` не может
представить.

Фактическая независимая матрица:

| Пользовательский запрос | Фактический scope |
|---|---|
| `Покажи задачи на завтра` | `today` |
| `Какие дела на понедельник?` | `today` |
| `Покажи задачи по проекту Альфа` | `today` |
| `Какие задачи в командировке?` | `today` |
| `Покажи все задачи на завтра` | `all` |
| `Какие задачи выполнены вчера?` | `today` |

Поскольку deterministic recognizer выполняется до LLM, пользователь получает
уверенный, но семантически неверный список. Существующая
[`test_task_query_recognizer.py`](../tests/test_task_query_recognizer.py)
проверяет поддерживаемые happy paths и mutation negatives, но не unsupported
read-only qualifiers.

**Требуемое исправление:** recognizer обязан либо представить весь фильтр
типизированным объектом, либо fail-close и вернуть `None`, если запрос содержит
дату/период/context, не поддерживаемые `TaskListScope`.

**Критерии приёмки:**

- tomorrow/yesterday/weekday/explicit-date не превращаются в `today` или `all`;
- project/trip/category/priority/person qualifiers не теряются;
- комбинации `all + date`, `done + date`, `overdue + context` покрыты отдельно;
- unsupported запрос передаётся в способный обработать его use case либо
  получает честное уточнение;
- property/parameterized tests доказывают, что recognizer никогда не удаляет
  значимое ограничение запроса;
- live Telegram gate содержит хотя бы tomorrow, project и trip negative oracle.

### REV-20260902-002 — macOS deploy не выполняет staged preflight и rollback

**Риск:** высокий, production availability.

**Доказательство:**
[`platform/macos/install.sh`](../platform/macos/install.sh#L45-L86)
синхронизирует dependencies, проверяет заданный proxy и STT, рендерит plist,
после чего выгружает действующий LaunchAgent и загружает новый. До остановки
работающей версии не выполняются:

- `settings.runtime_config_errors()` и DB/migration preflight нового кода;
- Telegram `getMe` с фактическим bot token;
- ограниченный readiness wait нового процесса;
- проверка singleton/polling после switch;
- автоматический возврат предыдущего plist/revision при ошибке.

Proxy probe вызывает только корень `https://api.telegram.org`, не использует
bot token и запускает `curl` без `--fail`. Он доказывает транспортную
доступность endpoint, но не готовность Telegram-конфигурации. Полный preflight
сейчас происходит внутри `run.sh` уже после restart; ошибочная версия может
попасть в KeepAlive crash loop.

**Требуемое исправление:** сделать deployment staged и recoverable.

**Критерии приёмки:**

- до остановки текущего процесса проверяются config, DB, migration и Telegram
  credentials новой ревизии;
- новый plist создаётся отдельно и проходит `plutil`;
- installer сохраняет предыдущий рабочий plist/revision;
- после switch bounded readiness проверяет PID, heartbeat, singleton lease,
  polling и migration;
- при timeout/error автоматически возвращается предыдущая конфигурация;
- failure-injection tests покрывают invalid token, DB outage, invalid config,
  failed model warmup и readiness timeout;
- install report явно фиксирует deployed SHA и rollback result.

### REV-20260902-003 — versioned release и protected main отсутствуют

**Риск:** высокий для демонстрации инженерного процесса, supply-chain evidence
и однозначной идентификации принятой версии.

**Доказательство текущего состояния GitHub:**

- repository tags: `[]`;
- GitHub Releases: `[]`;
- repository rulesets: `[]`;
- branch protection для `main`: отсутствует;
- `pyproject.toml` всё ещё содержит `0.2.0`, а значительный объём production
  изменений находится в `Unreleased`;
- после добавления CODEOWNERS напрямую в `main` были отправлены красные
  `5c92b2d` и `1444b2a`; зелёным стал только последующий `e373b8b`.

Текущий [`release.yml`](../.github/workflows/release.yml#L1-L50) запускается по
tag, но tag ещё не создавался. Workflow не создаёт GitHub Release: он загружает
Actions artifact с retention 90 дней. Название шага `Upload immutable release
evidence` описывает неизменяемость artifact object, но не долговременную
публикацию релиза.

**Требуемое исправление:** закрыть release identity и repository governance как
один acceptance gate.

**Критерии приёмки:**

- `main` защищена ruleset/branch protection;
- merge разрешён только через PR после required `quality`, `secrets`,
  `developer-bootstrap` и `container-e2e`;
- required approval и CODEOWNERS review реально enforced;
- force push и deletion `main` запрещены;
- версия согласована между `pyproject.toml`, CHANGELOG, annotated tag и
  deployed revision;
- tag создаёт GitHub Release с SBOM, SHA256SUMS, provenance и release notes;
- release evidence хранится как release asset/registry artifact, а не только
  90-дневный workflow artifact;
- tag создаётся только для SHA с CI, live E2E, recovery и post-deploy evidence.

### REV-20260902-004 — архитектурная декомпозиция остаётся поверхностной

**Риск:** высокий именно для технической оценки заказчиком: reviewability,
регрессионный радиус и стоимость дальнейшей разработки.

**Положительный результат remediation:** `_process_text_message_unlocked`
разделён на routing/request/guard/persistence/presentation phases, а глобальные
пороги снижены до complexity 15, branches 20, returns 12, statements 80.

**Оставшееся доказательство долга:**

- `bot/handlers/messages.py` — 1718 строк;
- `bot/handlers/commands.py` — 1223 строки;
- `bot/llm/dispatcher.py` — 1125 строк;
- 11 функций имеют inline `REVIEW-20260829 legacy ratchet`;
- при запуске Ruff с `--ignore-noqa` остаётся 21 нарушение даже настроенных
  проектных порогов;
- при conventional threshold 10 обнаруживается 27 сложных функций;
- `main()` имеет complexity 44 и 174 statements;
- `_handle_create_task()` — complexity 43, 43 branches, 104 statements;
- `_extract_common_intent()` — complexity 35, 34 branches, 21 return;
- strict mypy override включён только для
  `bot.application.task_query_recognizer`; глобально сохраняются
  `ignore_missing_imports = true` и `no_implicit_optional = false`:
  [`pyproject.toml`](../pyproject.toml#L74-L87).

Текущий refactor в основном разбил одну функцию на соседние функции того же
mega-file. Это полезный первый шаг, но ещё не application-level modularity.

**Требуемое исправление:** физически выделить use cases и удалить legacy
exceptions по плану, а не бессрочно фиксировать их как допустимое состояние.

**Критерии приёмки:**

- task/project/note/reminder recognizers вынесены из Telegram handler;
- `dispatcher.py` заменяется набором typed application services;
- Telegram presentation не импортирует persistence/LLM decomposition внутри
  функций;
- startup orchestration разделён на ресурсы с явным acquire/close lifecycle;
- для каждого legacy exception есть issue/owner/target milestone;
- ни один production use case не превышает согласованный complexity threshold;
- mypy strictness включается минимум для всего `application/` и новых service
  modules;
- import/layer tests запрещают обратные зависимости.

## 5. P2 — обязательно до финального публичного релиза

### REV-20260902-005 — quick start воспроизводит БД, но не полный runtime profile

**Доказательство:** README перечисляет Python, PostgreSQL и Telegram/MiniMax
keys как все требования:
[`README.md`](../README.md#L60-L93). Однако стандартный
[`config.yaml.example`](../config.yaml.example) выбирает локальные Ollama
`nomic-embed-text` и Whisper `medium` с `local_files_only: true`.

`scripts/bootstrap_dev.sh` поднимает БД, применяет миграции и запускает
`scripts/preflight.py`, но этот preflight проверяет только config structure,
DB и migration revision. Он не подтверждает Ollama model, Whisper model,
Telegram credentials или реальный provider call. `seed_knowledge.py` допускает
загрузку без embeddings, поэтому успешная команда также не доказывает
полнофункциональный профиль.

**Рекомендация:** оформить два явных и проверяемых пути:

1. `minimal cloud/dev` — запускается после quick start без Ollama/локального
   Whisper;
2. `full local macOS` — устанавливает/проверяет Ollama model и prefetch STT.

README должен отдельно указывать degraded-режим и acceptance каждого профиля.

### REV-20260902-006 — risk-based coverage неполон

Overall coverage вырос до 71,07%, critical privacy/export/delivery/reminder
modules имеют 85–100%. Это существенное улучшение. Но несколько центральных
runtime поверхностей остаются заметно ниже:

| Модуль | Coverage |
|---|---:|
| `bot/main.py` | 27% |
| `bot/handlers/commands.py` | 49% |
| `bot/llm/client.py` | 43% |
| `bot/llm/dispatcher.py` | 54% |
| `bot/db/crud/interaction_states.py` | 66% |
| `bot/handlers/voice.py` | 68% |

Current critical gate не включает startup lifecycle, provider failover,
основные command handlers, durable interaction CAS и voice state transitions.
Mutation testing отсутствует, поэтому line coverage не доказывает, что asserts
чувствительны к ошибочной логике.

**Рекомендация:** добавить risk tier для startup/commands/LLM/interactions/voice,
mutation testing для application/domain logic и state-machine/property tests
для recognizers и durable workflows. Не повышать общий процент за счёт
mock-only execution.

### REV-20260902-007 — canonical local gate не является canonical CI entrypoint

[`scripts/run_local_test_gate.sh`](../scripts/run_local_test_gate.sh) успешно
управляет disposable DB, migrations, integration tests, overall/critical
coverage и cleanup. Однако GitHub workflow повторяет эту логику самостоятельно
и не запускает документированный script. Следовательно, shell lifecycle,
выбор порта или cleanup могут регрессировать при зелёном CI.

**Рекомендация:** либо вызывать script из CI quality job, либо выделить общий
library/Make target, используемый и локально, и CI. Добавить failure tests для
port collision, interrupted pytest, failed migration и гарантированного
удаления volume.

### REV-20260902-008 — CODEOWNERS содержит фиктивную sensitive path

[`CODEOWNERS`](../.github/CODEOWNERS#L1-L7) указывает `/bot/middleware/`, тогда
как реальная security boundary — файл `bot/middleware.py`. Общий `* @kravdim`
пока сохраняет владельца, но специальная строка не защищает ожидаемый объект.
Без branch protection весь CODEOWNERS вообще носит информационный характер.

**Рекомендация:** исправить path, перечислить реальные sensitive surfaces
(`middleware.py`, privacy handlers/services, migrations, workflows, deployment,
backup/recovery) и проверить required CODEOWNERS approval отдельным тестовым PR.

### REV-20260902-009 — CI эксплуатационная полировка

Для образцового публичного репозитория рекомендуется дополнительно:

- задать `timeout-minutes` всем jobs;
- включить workflow `concurrency` с отменой устаревших PR runs;
- не устанавливать тяжёлый STT extra в developer-bootstrap job, если он не
  участвует в проверке;
- добавить ShellCheck, а не только `sh -n`;
- проверять Markdown anchors и style, а не только существование local targets;
- публиковать test/coverage summary и machine-readable coverage artifact;
- добавить changelog/version consistency check.

## 6. Внешние gates

Следующие пункты остаются открытыми и должны быть завершены либо явно исключены
с письменным product-owner решением:

1. legal review privacy notice, consent и retention для целевого рынка;
2. native STT resource drill на 20 транскрипций на production-class Mac;
3. protected-main GitHub ruleset и проверочный PR;
4. version/tag/GitHub Release с provenance artifacts;
5. финальный live Telegram E2E, recovery drill и post-deploy SLO на release SHA.

Voice acceptance `4/4` в текущем live gate не заменяет отдельный многократный
native STT resource/leak drill.

## 7. Рекомендуемый порядок доработки

1. Немедленно закрыть qualifier regression в task-list recognizer и добавить
   adversarial/live cases.
2. Настроить protected main; дальнейшие исправления вести только через PR.
3. Реализовать staged macOS deployment с preflight/readiness/rollback.
4. Продолжить физическую декомпозицию `messages.py`, `dispatcher.py`, `main.py`;
   удалить legacy `noqa` по milestones.
5. Довести quick start до полного minimal profile и отдельно описать full local
   profile.
6. Объединить local и CI quality entrypoint, усилить risk/mutation testing.
7. Закрыть legal и native STT gates.
8. Выпустить новую согласованную версию одним SHA: CI → live E2E → recovery →
   deploy → tag → GitHub Release → provenance verification.

## 8. Критерий финальной приёмки

Проект получает статус образцово-показательного только если одновременно:

- любой deterministic recognizer fail-close сохраняет все значимые qualifiers;
- supported clean-host profile воспроизводится независимым инженером;
- production deploy автоматически сохраняет доступную предыдущую версию при
  любой ошибке новой;
- центральные use cases имеют физические application boundaries и не зависят
  от mega-functions с legacy exceptions;
- protected main не допускает красные или неотревьюенные изменения;
- version, tag, GitHub Release, deployed SHA и evidence bundle совпадают;
- release SHA имеет зелёные CI, live E2E, recovery и post-deploy SLO;
- legal privacy и native STT gates закрыты либо формально выведены из scope.
