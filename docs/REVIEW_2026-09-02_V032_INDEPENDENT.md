# Независимая перепроверка DailyPlanner v0.3.2

**Дата:** 2 сентября 2026 года
**Проверенный `HEAD`:** `21deb84b67b04eba4b285124a7cd8392ae97a142`
**Release/deploy revision:**
`64d769ddc21b93d1f6767b4529ecaebab4b47f5b` (`v0.3.2`)
**База сравнения:** `cb96fdd`
**Статус:** **remediation выполнена в большом объёме и существенно усилила
проект, но `v0.3.2` пока не принят как безусловно образцово-показательный
релиз из-за пользовательских P1-регрессий и неполного release evidence.**

## 1. Решение

Разработчик действительно сделал много полезной работы. Это не косметическая
доработка:

- статические проверки installer заменены исполняемой failure-injection
  матрицей;
- каждый deploy phase получил атомарный отчёт;
- task creation вынесен в типизированный application use case;
- background lifecycle вынесен из composition root;
- `main.py` и `dispatcher.py` заметно уменьшены;
- coverage и complexity ratchets усилены;
- `v0.3.1` и `v0.3.2` опубликованы как immutable GitHub Releases;
- production действительно работает на точном SHA `64d769d`.

Тем не менее новый memoir fix вернул старый опасный сценарий потери команды,
fast-path создания задач всё ещё не является lossless для широкого класса дат,
а полный live E2E в handoff выполнен до появления `v0.3.2`. Поэтому текущую
версию можно считать сильным работающим production-релизом для владельца, но
нельзя демонстрировать техническому заказчику со словами «все замечания
закрыты».

## 2. Независимо подтверждённые результаты

### 2.1. Локальная приёмка

- canonical PostgreSQL gate: **452 passed, 1 skipped**;
- measured coverage: **72,26%** при floor 71%;
- critical coverage: **85–100%**;
- task creation use case: **96%**;
- background lifecycle: **79%**;
- intentional deploy failure matrix: **11/11 PASS**;
- Ruff: PASS;
- complexity allowlist gate: PASS, 16/16 suppressions в 9 функциях;
- mypy: PASS, 112 source files;
- Alembic fresh upgrade и schema drift check: PASS;
- documentation/version/secret contracts: PASS;
- shell syntax и plist validation: PASS;
- рабочее дерево до добавления настоящего отчёта было чистым.

### 2.2. GitHub и production

- текущий `HEAD` прошёл
  [CI run 33677070815](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33677070815);
- release SHA прошёл
  [CI run 33675039515](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33675039515);
- [release workflow 33675498986](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33675498986)
  завершён успешно;
- [GitHub Release v0.3.2](https://github.com/kravdim/telegram-notebook-bot/releases/tag/v0.3.2)
  имеет `immutable=true` и содержит image, SBOM, SHA256SUMS и attestation;
- annotated tag `v0.3.2` указывает на `64d769d`;
- production LaunchAgent: `running`, PID 3468, `runs=1`, процесс не завершался;
- deploy report: `status=deployed`, `phase=complete`, active SHA `64d769d`;
- свежий heartbeat подтверждает PID и точный release SHA.

### 2.3. Состояние прошлого review

| Пункт | Результат перепроверки |
|---|---|
| FINAL-001, executable rollback tests | Основная часть закрыта; найден оставшийся migration/previous-release gap ниже |
| FINAL-002, independent review | Не закрыт, оформлен issue #6 |
| FINAL-003, architecture milestone | Первый milestone действительно закрыт; дальнейшая декомпозиция оформлена issues #4/#5 |
| FINAL-004, title qualifiers | Узкие today/tomorrow/priority cases закрыты; общий lossless contract ещё нарушается |
| FINAL-005, coverage | Улучшен и подтверждён |
| FINAL-006, immutable releases | Закрыт для v0.3.1/v0.3.2 |
| FINAL-007, legal scope | Допустимо только для owner-only production и демонстраций на синтетических данных |

## 3. P1 — блокирует безусловную приёмку v0.3.2

### V032-001 — pending memoir снова поглощает обычные команды

**Риск:** высокий, silent wrong mutation и потеря намерения пользователя.

[`_is_memoir_answer`](../bot/handlers/messages.py) не анализирует текст. Если
есть active memoir state и сообщение не является Reply на другую ветку, любое
следующее обычное сообщение признаётся ответом мемуарника. Этот маршрут
выполняется раньше task shortcuts, deterministic intents и LLM.

Состояние мемуарника живёт 60 минут. Фактическая матрица для сообщения без
Telegram Reply:

| Текст | Фактический маршрут |
|---|---|
| `Напомни через 15 минут выпить воды` | memoir |
| `Надо купить молоко` | memoir |
| `Позвонить маме — сделал` | memoir |
| `Сегодня было яркое событие` | memoir |

Первые три команды будут записаны как memoir + diary entry, а требуемая
задача/напоминание/закрытие не произойдёт. Пользователь получит уверенное
`Записано в мемуарник`.

Это не теоретический риск: ровно такой дефект уже был подтверждён production
ревью 24 августа — команда `напомни` попала в дневник. Тогда явным критерием
исправления было считать ответом memoir только Reply/явное действие. Новый тест
`test_pending_memoir_accepts_next_text_without_explicit_reply` теперь закрепляет
возвращённое опасное поведение как норму.

**Требуемое исправление:**

- явный Reply/ForceReply на memoir prompt должен оставаться однозначным путём;
- обычные высокоуверенные mutation intents без Reply нельзя поглощать;
- если продукт всё же принимает plain-text memoir answer, неоднозначный текст
  должен получить confirmation (`записать в мемуарник` / `выполнить команду`),
  а не молчаливую маршрутизацию;
- кнопка `Пропустить` не является достаточной защитой: пользователь может не
  нажать её и продолжить обычную работу;
- добавить PostgreSQL integration tests для TTL, restart, concurrent messages,
  plain reminder/task/done commands и точного DB side-effect oracle;
- добавить эти сценарии в live Telegram gate.

### V032-002 — task creation fast-path по-прежнему теряет даты и конфликты

**Риск:** высокий для planner correctness.

[`task_creation_recognizer.py`](../bot/application/task_creation_recognizer.py)
fail-close проверяет только ограниченный словарь дат. Более естественные
временные формулировки проходят deterministic fast-path, становятся частью
title и не заполняют `scheduled_date`:

| Ввод | Фактический результат |
|---|---|
| `Надо купить молоко через два дня` | title содержит дату, `scheduled_date` отсутствует |
| `Надо сделать отчёт через 2 дня` | дата потеряна как поле |
| `Надо сделать отчёт на следующей неделе` | дата/период потеряны как поле |
| `Надо сделать отчёт 10 сентября` | дата потеряна как поле |

Противоречия также разрешаются без уточнения:

- `Завтра надо купить молоко сегодня` выбирает tomorrow;
- `Надо срочно купить молоко, приоритет средний` выбирает high.

Поскольку fast-path выполняется до LLM, более способный parser уже не получает
эти запросы.

**Требуемое исправление:** recognizer должен либо полностью представить
temporal/priority semantics, либо возвращать `None` при любом похожем на
qualifier фрагменте. Нужны conflict detection и широкая parameterized/property
matrix: number/word offsets, month names, next week/month, multiple dates,
urgency + explicit priority, punctuation и morphology. Live gate должен
проверять не только ответ, но `scheduled_date` в БД.

### V032-003 — live E2E evidence не относится к release SHA v0.3.2

**Риск:** release gate допускает production fix без проверки реального
Telegram route на том же SHA.

Handoff приводит успешный run `DP-20260902T181030-4a57c7`, отчёт которого
создан в 21:23 по Москве. Commit `v0.3.2` создан в 22:40 и влит в 22:43. Значит
`85/85 PASS` выполнен на предыдущей версии, а не на `64d769d`.

Для `v0.3.2` подтверждены unit/integration tests, CI, release workflow,
deployment и heartbeat, но не найден post-deploy live Telegram run. Особенно
важно, что текущий общий live suite не содержит полного scheduler memoir
сценария с DB oracle.

**Требуемое исправление:** на точном SHA `64d769d` выполнить targeted live
memoir matrix и затем полный live gate. Evidence должен записывать tested SHA,
время deploy и run, ответы, memoir/diary rows, отсутствие task/reminder side
effects и cleanup. До этого handoff не должен объединять v0.3.1 live evidence с
v0.3.2 release evidence.

### V032-004 — rollback через новую migration пока не гарантирован

**Риск:** заявленная recoverability сломается при первом релизе с изменением
схемы.

Installer декларирует expand/contract compatibility, но восстановленный
previous release запускает собственный `run.sh` и собственный strict preflight.
После применения новой migration старый preflight ожидает старый Alembic head и
завершит процесс с mismatch даже при обратно совместимом добавлении столбца.

Кроме того, `wait_for_release()` всегда выполняет Python и preflight из
`CANDIDATE_DIR`, даже когда проверяется heartbeat предыдущего SHA. Текущий fake
harness создаёт heartbeat напрямую и не моделирует реальный startup старого
release, поэтому этот разрыв не обнаруживает. Для `v0.3.1` → `v0.3.2` миграций
нет, но общая гарантия installer шире фактически доказанной.

**Требуемое исправление:** ввести явный совместимый schema range или режим
preflight `database head is descendant of this release head`; проверять
restored release его собственным кодом; добавить drill с двумя настоящими
release fixtures и дополнительной backward-compatible migration. Отдельно
проверить запрет destructive migration без maintenance/restore plan.

## 4. P2 — инженерная полировка

### V032-005 — complexity ratchet не ограничивает рост complexity

Скрипт считает наборы `noqa` codes, но не фактические значения complexity,
branches, returns и statements. Уже allowlisted функция может вырасти с 35 до
70 complexity и gate останется зелёным, пока не добавлен новый тип suppression.
Удалённое исключение также можно вернуть позже, потому что оно остаётся в
статическом `ALLOWED`.

Нужно хранить числовой baseline на функцию и запрещать увеличение каждой
метрики; удалённые allowlist entries должны удаляться тем же PR. Сейчас gate
полезен как защита от новых `noqa`, но документация преувеличивает его как
ratchet, допускающий только reductions.

### V032-006 — repository governance остаётся owner-only

Branch protection требует PR и пять checks, запрещает force-push/deletion и
enforced для admin. Но approval count равен нулю, CODEOWNERS review и last-push
approval выключены; PR #11 смержен без reviews. Issue #6 корректно фиксирует
ограничение, однако для демонстрации командного процесса оно остаётся открытым.

### V032-007 — активная документация частично отстаёт от релиза

- `docs/README.md` называет текущим handoff `v0.3.1`, хотя production —
  `v0.3.2`;
- `SESSION_CONTEXT_2026-09-02.md` в remaining scope также называет production
  release `v0.3.1`;
- README описывает Reply-context только как chronometry/LLM и не раскрывает,
  что memoir перехватывает следующее plain-text сообщение;
- текущий documentation contract проверяет существование ссылок, но не такие
  семантические расхождения и не Markdown style; в ранее добавленном review
  остаётся trailing whitespace.

Нужно обновить active index/UX contract и добавить version-aware проверку
текущего handoff. Поведение plain memoir answer должно быть описано до релиза,
а не только в session evidence.

## 5. Рекомендуемый порядок исправления

1. Немедленно закрыть V032-001 и выполнить targeted live memoir regression.
2. Расширить fail-close task creation и закрыть qualifier conflicts.
3. Выпустить новый SHA и прогнать полный live E2E именно после deploy.
4. Добавить real two-version migration rollback drill.
5. Усилить числовой complexity ratchet и синхронизировать активные документы.
6. Продолжить issues #4/#5 и пригласить независимого reviewer по issue #6.

## 6. Критерий следующей приёмки

Релиз можно принять как образцовый только когда:

- pending memoir не может молча украсть задачу, reminder или completion;
- deterministic recognizers не теряют temporal/priority semantics;
- live E2E и DB oracle выполнены после deploy на exact release SHA;
- rollback доказан между двумя версиями при новой совместимой migration;
- active docs однозначно описывают текущую версию и interaction ownership;
- оставшиеся governance/architecture ограничения честно показаны как scope, а
  не как полностью закрытые требования.

Текущая оценка: **инженерно сильный проект с заметным прогрессом, но `v0.3.2`
содержит release-blocking regression и требует ещё одного исправительного
цикла.**
