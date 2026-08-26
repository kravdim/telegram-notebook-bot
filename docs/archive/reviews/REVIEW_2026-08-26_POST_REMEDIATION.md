# Повторный аудит DailyPlanner после remediation

Дата: 26 августа 2026 года
Проверенный HEAD: `ff610e0` (`43f9de6` — продуктовый commit, `ff610e0` — release handoff)
Предыдущий аудит: `docs/archive/reviews/REVIEW_2026-08-26.md`
Версия проекта: `0.2.0`

## Итоговый вердикт

Проект стал существенно сильнее. Архитектурная и эксплуатационная доработка не
является косметической: общий deadline LLM, CAS для interaction state, typed
`CommandResult`, delivery fencing, атомарные memoir/chronometry writes,
crash-resumable deletion journal, backup metadata semantics, CI и документация
реализованы содержательно и в основном соответствуют заявленным контрактам.

Однако безусловно образцово-показательным и готовым к новому release tag проект
пока считать нельзя. Повторный аудит обнаружил три P1-дефекта, два из которых
являются продолжением уже закрывавшихся reliability/privacy требований. Один из
них воспроизвёлся в текущем боевом userbot-прогоне: бот пообещал поставить
напоминание, не создал его и не прислал push.

Решение на 26.08.2026: **условный NO-GO для демонстрации как эталонного
reliability-продукта и для тега `0.2.0` до устранения P1 и повторного единого
82/82 gate**. Для обычной внутренней эксплуатации состояние значительно лучше
предыдущего, production process и текущие SLO здоровы.

## Что именно проверено

- diff от предыдущего аудита: `c3db802..43f9de6`, 41 изменённый файл;
- реализация LLM deadline/history, interaction lifecycle, voice/memoir/project
  flows, typed application results, privacy deletion, delivery outbox,
  backup/recovery observability и chronometry transaction boundary;
- README, CHANGELOG, architecture, operations, remediation и release handoff;
- локальный полный quality-контур;
- официальный GitHub Actions run `32962136475`;
- production preflight, migration head, LaunchAgent и SLO snapshot;
- новый полный live userbot gate из 82 messy-human сценариев.

## Проверочные результаты

| Проверка | Результат |
|---|---|
| Ruff | PASS |
| mypy | PASS, 98 файлов |
| Bandit medium/high | PASS |
| compileall | PASS |
| secret scan | PASS, 203 tracked files |
| STT import | PASS, `faster-whisper 1.2.1` |
| unit/scenario pytest | `195 passed, 15 skipped` |
| coverage | 45,57% при floor 45% |
| GitHub Actions `32962136475` | PASS: quality, secrets, container-e2e |
| CI PostgreSQL/migrations/recovery | PASS на product SHA `43f9de6` |
| Production preflight | PASS, `f4b8c2d6e1a0` |
| Alembic | один head `f4b8c2d6e1a0` |
| LaunchAgent | running, один process PID 7044 |
| Current reminder SLO | ok, lag 0 s, pending 0 |
| Current backup SLO | ok, age 6,5 h, `metadata-ok` |
| Предыдущий release live evidence | 82/82, `report_20260826_164524.md` |
| Новый независимый live gate | **79/82**, FAIL, `report_20260826_200026.md` |
| Новый live teardown | PASS, тестовые данные удалены |

Локальные 15 integration-тестов были пропущены без disposable PostgreSQL; их
зелёный результат подтверждён официальным CI на том же продуктовом SHA.

## Критические замечания

### P1. Бот может подтвердить мутацию свободным текстом, не выполнив её

В новом live run запрос:

`слушай напомни через 2 минуты DP-20260826T164140-62c608-чай попить, а то забуду`

получил ответ:

`Готово! Через 2 минуты напомню. ☕`

Фактически reminder не был создан, push за 180 секунд не пришёл, кнопка snooze
отсутствовала. R1 и R2 завершились FAIL, зависимый R3 — ERROR.

Причина:

- `bot/handlers/messages.py:630-649` правильно требует mutating tool call;
- если forced retry снова не дал мутацию, `bot/handlers/messages.py:1397-1407`
  допускает свободный content как «уточнение»;
- `_FAKE_MUTATION_RE` в `bot/handlers/messages.py:73-83` распознаёт
  «напоминание установлено», но не обещания вида «напомню», «поставил
  напоминание», «готово, через N минут...»;
- в результате пользователь получает ложное подтверждение, хотя side effect не
  состоялся.

Это нарушение основного продуктового контракта. Проверять список фраз
регулярным выражением недостаточно: при mutation request любой ответ без
успешного typed mutating result должен fail closed.

Требуется:

1. После `mutation_expected=True` запрещать любой success-like/free-form ответ
   без выполненного mutating `CommandResult`.
2. Разрешать только явно типизированное уточнение/отказ; content провайдера не
   считать доказательством успеха.
3. Добавить regression для точной live-фразы и вариантов «я напомню», «готово,
   поставил», включая forced-tool provider non-compliance и timeout.
4. Проверять в тесте не только текст, но и наличие строки reminder в БД и
   фактическую доставку.

### P1. Повторный privacy deletion для заново зарегистрированного Telegram ID не работает

`scripts/delete_user_data.py:66-77` использует вечный ключ
`privacy.deletion.<telegram_id>`. Если журнал уже имеет phase `completed`,
оператор немедленно получает `already-completed`; новые row counts не читаются,
whitelist не проверяется и удаление не запускается.

Сценарий:

1. пользователь удалён корректно;
2. позднее тот же Telegram ID снова разрешён и проходит onboarding;
3. появляются новые user/domain rows;
4. повторный privacy request возвращает старое «all-user-data-zero», оставляя
   новые данные и доступ нетронутыми.

Это особенно опасно тем, что оператор видит ложную успешную verification.
Crash-resume внутри одной операции исправлен хорошо, но idempotency ошибочно
распространена на все будущие операции этого пользователя.

Требуется:

1. В completed-ветке заново проверять `user_data_counts()` и актуальный access
   list; возвращать `already-completed` только при реальном нуле и отсутствии
   доступа.
2. Использовать generation/operation UUID либо reopen completed journal, если
   данные или доступ появились снова.
3. Добавить integration-тест: delete → re-onboard/recreate rows → delete again.
4. В verification возвращать результаты свежей проверки, а не сохранённое
   утверждение предыдущей операции.

### P1. Voice confirm всё ещё теряет retry при штатно обработанной ошибке LLM

`cb_voice_confirm()` восстанавливает `voice_confirm` только если
`process_text_message()` выбросил исключение (`bot/handlers/voice.py:286-305`).
Но основной pipeline перехватывает `LLMUnavailableError`, общий provider error и
ошибку forced retry, отправляет пользователю error text и нормально возвращает
`None` (`bot/handlers/messages.py:607-626`, `635-649`). Затем voice handler идёт
по success-ветке и очищает `voice_processing` (`bot/handlers/voice.py:306-307`).

Итог: при типичном отказе провайдера пользователь видит ошибку, но кнопка
повтора и transcript/session уже потеряны. Новый live happy path Q1–Q4 прошёл,
однако он не инъецирует этот отказ. Coverage подтверждает пробел: соответствующие
error/retry ветки voice не исполнены.

Требуется вернуть из message pipeline типизированный outcome, например
`completed | retryable_error | rejected | duplicate`, и очищать voice state
только при `completed`. Нужны fault-injection тесты как для выброшенного, так и
для штатно преобразованного в user-facing response исключения.

## Существенные замечания

### P2. Memoir skip сообщает об успехе после ошибки очистки state

В `bot/handlers/callbacks.py:49-60` исключение `interaction_service.clear()`
только логируется, после чего сообщение всё равно меняется на «Сегодня без
записи». PostgreSQL state остаётся активным, и последующий reply ещё может
создать memoir entry до TTL.

На ошибке очистки нельзя подтверждать skip. Нужно оставить/вернуть кнопку,
сообщить о временной ошибке и проверить fault-injection тестом. Сейчас покрыта
только stale-button ветка, а exception branch отсутствует в coverage.

### P2. Release provenance и coverage ещё не уровня эталонного релиза

- `pyproject.toml` и CHANGELOG содержат `0.2.0`, но Git tag отсутствует, хотя
  remediation прямо связывает его с зелёными CI/deploy/live gate, а handoff уже
  объявляет релиз завершённым.
- Новый обязательный live gate красный, поэтому создавать tag сейчас нельзя.
- Общий coverage 45,57% проходит floor 45% с запасом лишь 0,57 п.п.; критичные
  `interaction_states` — 14%, `delivery` — 29%, `user_deletion` — 27%, voice —
  56%. Общая цифра не защищает самые рискованные failure branches.

После исправления P1 рекомендуется ввести branch coverage и отдельные
risk-weighted thresholds/обязательный список fault-injection тестов для
interaction, deletion, reminders и delivery. Затем повторить единый live gate,
зафиксировать SHA отчёта и создать annotated `v0.2.0` tag/release.

## Статус десяти пунктов прошлого аудита

| № | Статус | Вывод |
|---|---|---|
| 1 | Закрыт | LLM chain имеет общий deadline, SDK retries отключены, provider compression убрана |
| 2 | Закрыт | Voice/memoir tokens и Telegram message IDs защищают stale callbacks |
| 3 | Частично | Project/memoir/chrono lifecycle исправлены; voice retry ломается на handled error |
| 4 | Закрыт | Typed `CommandResult` доходит до Telegram adapter |
| 5 | Формально закрыт | floor 45% достигнут, но risk branches недостаточно покрыты |
| 6 | Частично | crash-resumable journal есть; новая privacy operation после re-onboarding блокируется старой |
| 7 | Закрыт | быстрый check честно metadata-only, полный SHA закреплён за recovery drill |
| 8 | Закрыт | delivery error/progress/final updates fenced lease token |
| 9 | Закрыт | chronometry write, marker и state completion объединены транзакцией |
| 10 | Частично | version/docs готовы, но release tag отсутствует и новый live gate красный |

## Вопросы разработчику

1. Почему free-form content после обязательного tool retry считается безопасным
   уточнением без отдельного typed result `clarification`?
2. Как гарантируется повторное выполнение privacy request после легального
   возвращения пользователя в систему?
3. Почему `process_text_message()` не сообщает вызывающему workflow результат
   выполнения, а success и handled failure имеют одинаковый `None`?
4. Какой failure policy ожидается от memoir skip при недоступной БД: retry,
   сохранение кнопки или явный отказ?
5. Является ли 82/82 обязательным release gate? Если да, почему handoff называет
   релиз завершённым при отсутствии tag, и где хранится машинно связанная цепочка
   commit → CI → live report → recovery evidence → tag?

## Порядок исправления и повторной приёмки

1. Закрыть false-success mutation path и подтвердить live reminder end-to-end.
2. Исправить generation semantics privacy deletion и добавить PostgreSQL
   integration regression.
3. Ввести typed outcome message pipeline и сохранить voice retry на всех
   retryable errors.
4. Исправить memoir skip failure UX.
5. Добавить targeted branch/fault-injection tests и поднять запас coverage.
6. Повторить Ruff, mypy, Bandit, dependency/secret audit, полный pytest с
   disposable PostgreSQL, backup+restore drill.
7. Выполнить один чистый live gate 82/82 с успешным teardown и отдельно forced
   provider-error сценарий для voice.
8. Только после этого создать annotated `v0.2.0` tag/release и обновить handoff.

## Сильные стороны, которые уже можно показывать

- честно описанные architecture boundaries и ограничения at-least-once
  Telegram delivery;
- полноценный CI с PostgreSQL, Alembic drift check, dependency/security gates,
  backup+restore и container readiness;
- singleton runtime, production preflight и измеримые SLO;
- durable multipart delivery с lease fencing;
- CAS interaction state и защита stale callbacks;
- транзакционные memoir/chronometry workflows;
- изолированный messy-human userbot с pre-cleanup и mandatory teardown;
- хорошая документация remediation/operations/recovery без сокрытия внешних
  ограничений.

Проект уже производит впечатление серьёзной инженерной работы. Следующий шаг —
довести fail-closed semantics до того же уровня, на котором уже выполнены
инфраструктура, транзакции и документация.
