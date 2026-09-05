# DailyPlanner: план доработки по комплексному ревью

Дата: 04.09.2026. Исполнитель: Codex.
Основание: [комплексное ревью](REVIEW_2026-09-04_COMPREHENSIVE_INDEPENDENT.md).
Исходный commit: `d871fa870f63e73a339cca47d5db3fdfd3b272f4`, версия `0.5.0`.
Статус: **реализация начата; R01/R05 проверены локально, далее этап 2**.

Цель: закрыть R01–R16, подтвердить исправления тестами и работающим релизом,
подготовить понятную документацию и демонстрацию показательного личного проекта.
Формат работы — последовательные небольшие изменения с проверками и evidence.

## Правила выполнения

1. Сохранять текущие изменения пользователя и документы ревью. Исторический отчёт
   не переписывать под исправленный код: результаты вести в отдельной таблице ниже.
2. Для дефектов сначала закрепить воспроизведение постоянным regression test.
   Проверку R06 начать с реального PostgreSQL: в ревью этот путь проверен статически.
3. На каждом шаге запускать адресные проверки. Полный PostgreSQL gate — на границах
   этапов с runtime-изменениями и перед выпуском; не повторять неизменившиеся дорогие
   проверки без причины. Не снижать coverage/complexity/security gates.
4. Весь затронутый lifecycle проверять через текст, slash-команду и callback.
   Единицей корректности считать task + reminders + recurrence + interaction state.
5. Изменения схемы проводить через совместимые миграции с проверкой старых данных.
   Rollback обязан сохранять записи, появившиеся после обновления.
6. Временные базы, синтетический пользователь и live-namespace должны быть изолированы.
   Реальные пользовательские задачи не использовать как тестовые фикстуры.
7. После каждого этапа фиксировать commit, проверки и следующий шаг в этом плане.
   `Завершён` означает выполненные критерии приёмки; `проверен локально` не означает
   `проверен в production`.
8. Сохранить модульный монолит и личный scope. Юрист, внешние клиенты, обязательный
   внешний reviewer и инфраструктура массового сервиса в план не входят.

## Рабочие решения, которые нужно закрепить кодом и ADR

Это исходные решения для реализации. Изменять их при новых фактах можно с записью
обоснования и обновлением соответствующих тестов.

| Область | Контракт |
| --- | --- |
| Подтверждение | «Готово» только после подтверждённого domain commit; сбой ответа не отменяет запись |
| Задача | Завершение/отмена/возобновление проходят общий lifecycle service |
| Даты | Плановая дата, дедлайн и время напоминания — разные поля; omitted / set / clear различимы |
| Перенос | Изменение только плановой даты сохраняет независимый alarm; привязанный к дедлайну alarm сохраняет offset при изменении дедлайна; ответ показывает итог |
| Неясная привязка | Для старых данных не угадывать связь alarm с датой; сохранять время и явно показывать его |
| Серия | Календарное повторение считается в timezone серии; изменение timezone профиля не переписывает серии молча |
| Простой | Одно уведомление о пропущенном occurrence, затем ближайшее будущее; без лавины накопленных повторов |
| Доставка | Durable claim, ограниченные пачки, backoff; штатная конкуренция без дублей; окно неопределённости Telegram/commit документировано |
| Ошибка доставки | Transient повторяется; exhausted/terminal остаётся видимой ошибкой с восстановлением, не пользовательской отменой |
| Диалог | Чтение не удаляет состояние; cleanup/transition проверяют идентичность сессии |
| Несколько действий | Сохранённый план и результаты по каждому действию; retry продолжает незавершённое |
| Повтор пользователя | Новое сообщение не считается дублем только потому, что совпал текст |
| Cloud choice | Набор внешних получателей входит в идентичность согласия; его изменение требует нового выбора |

До миграции recurrence определить и протестировать конкретные правила DST и короткого
месяца: несуществующее локальное время переносить на ближайшее допустимое, неоднозначное
исполнять один раз; день 29–31 ограничивать концом короткого месяца, сохраняя исходное
правило серии для последующих месяцев. Старые серии получают timezone владельца как
явно задокументированное допущение; исторические timestamps не переписываются.

## Этап 0. Подготовка и воспроизведения

- [ ] Проверить HEAD/worktree и сохранить ревью, план и индекс в истории разработки.
- [ ] Завести отдельную рабочую ветку с учётом действующих правил репозитория.
- [ ] Перенести воспроизведения R01–R05 в постоянные tests; добавить DB-сценарий R06.
- [ ] Составить матрицу статусов и переходов Task/Reminder/Interaction.
- [ ] Зафиксировать baseline проверок и версии окружения; старый результат 482/1,
      73,14% — ориентир из ревью, не новый прогон.

Готовность: дефекты воспроизводятся адресно; будущие tests не обращаются к production.
Намеренно падающие тесты не сливать в основную ветку отдельно от исправлений.

## Этап 1. Правдивые ответы и сохранность диалогов — R01, R05

Область: `bot/handlers/callbacks.py`, `bot/db/crud/interaction_states.py`,
`bot/application/interactions.py`, tests callback/interaction.

- [ ] Разделить completed / already completed / not found / failed в кнопке «Сделано».
- [ ] Сохранять безопасный retry при ошибке; acknowledgement кнопки сделать нейтральным.
- [ ] Убрать побочное удаление из чтения состояния; cleanup сделать conditional.
- [ ] Проверить все callers, рассчитывавшие на старое поведение истёкшего состояния.
- [ ] Проверить restart, старый Reply и старую кнопку при наличии новой сессии.

Готовность: DB failure не выдаёт успех; два конкурентных соединения не теряют новый
voice/memoir state при очистке старого. После проверки — отдельный исправляющий commit.

## Этап 2. Контракты команд и безопасный вывод — R09, часть R10/R11

Область: `bot/application/intents.py`, `command_bus.py`, `bot/llm/contracts.py`,
dispatcher adapters, `bot/formatters/`, handlers.

- [ ] Перенести provider-independent команды в application boundary без импорта `bot.llm`.
- [ ] Заменить legacy sentinel strings на typed payload и outcome; обновить всех callers.
- [ ] Ввести omitted / set / clear для updates, не ломая значения False и 0.
- [ ] Ввести явный plain/HTML renderer; проверить пользовательские строки во всех ответах.
- [ ] Исправить chunker: гарантированный прогресс, проверка лимита, fallback без потери текста.
- [ ] Протестировать двоеточия, `<>&`, Unicode, oversized tags/entities и границы частей.

Готовность: title не меняет структуру ответа, splitter не зависает, explicit null
доходит до application service. Изменения структурных контрактов обновлены атомарно.

## Этап 3. Единый lifecycle задач — R06, завершение R10

Область: `bot/services/tasks.py`, task CRUD, dispatcher, slash/callback handlers,
application use cases и, при необходимости, миграция привязки alarm.

- [ ] Общие операции complete / cancel / reopen / reschedule; одна транзакция на инвариант.
- [ ] Запретить обход lifecycle через generic status update.
- [ ] Завершать/отменять связанные reminders и продолжать recurring task ровно по контракту.
- [ ] Синхронизировать привязанные alarms при изменении/очистке дедлайна и времени.
- [ ] Возобновление recurring task не создаёт вторую ветку; конфликт с уже созданным
      следующим occurrence требует явного безопасного решения.
- [ ] Пользовательское подтверждение показывает итоговые дату/время и судьбу напоминания.

Готовность: одинаковые DB-инварианты во всех каналах, проверки конкурентного completion,
отмены, переноса, очистки даты и повторного открытия. Нет осиротевших task-reminders.

## Этап 4. Доставка и календарные повторы — R02, R03, R04, R08

Область: reminder CRUD/models/migrations, `bot/services/delivery.py` и reminder service,
scheduler loops, weekly review, observability/status.

- [ ] Проверить переиспользование delivery batches для reminder lifecycle; выбрать одну
      реализацию владения доставкой и закрепить ADR, не поддерживать две конкурирующие схемы.
- [ ] Основной loop и sweep используют один сервис claims; DB-lock не держится на сетевом send.
- [ ] Сохранять ID до rollback; failures записывать новой транзакцией.
- [ ] Добавить bounded retries, next_attempt_at/backoff, 429 retry_after и видимые failed states.
- [ ] Добавить безопасное восстановление ошибочной доставки с проверкой текущего состояния задачи.
- [ ] Обработать гонки send / snooze / complete / cancel и потерю/истечение lease.
- [ ] Сохранить timezone серии; унифицировать recurrence и catch-up; выполнить backfill.
- [ ] Перевести weekly review на возобновляемую multipart delivery.
- [ ] SLO/status учитывает failed и overdue; ошибка одного элемента не останавливает пачку.

Готовность: PostgreSQL interleaving tests минимум с тремя reminders; timeout, 429, 5xx,
403, failure commit, restart и expiry lease; локальные календарные edge cases; длинный
weekly review с отказом промежуточной части. R03 закрывается вместе с retry policy.

Миграции этого этапа: заранее проверить ограничения status, индексы claims и уникальность
occurrences, nullable/default/backfill, работу на копии старой схемы. Старый sender может
не понимать новые leases/statuses даже на совместимой схеме: это отдельный rollback gate.
Если предыдущий release несовместим, сначала подготовить совместимый промежуточный release;
не подменять проверку совместимости разрешением произвольного Alembic head.

## Этап 5. Безопасные повторы составных запросов — R07

Область: message pipeline, command bus/use cases, models/migrations, voice confirmation.

- [ ] Сохранять распознанный план до исполнения; стабильные request/action IDs и порядок.
- [ ] Хранить domain effect и completion action journal в одной транзакции.
- [ ] Retry не обращается к модели для нового плана и не повторяет завершённые действия.
- [ ] Разделить durable mutation result и доставку ответа пользователю.
- [ ] Убрать необязательные запросы/декоративные вычисления из критерия успеха записи.
- [ ] Отчёт о partial success с безопасным продолжением; transcript сохраняется до
      завершения или явного отказа пользователя от оставшихся действий.
- [ ] Определить retention action journal так, чтобы ещё доступный retry не потерял защиту от дублей.

Готовность: три действия с отказом в середине и при отправке ответа; crash до/после
commit; повторная кнопка; неизвестный результат соединения с БД; отдельное новое сообщение
с тем же текстом. Предыдущие успешные записи не дублируются и не теряются.

## Этап 6. Завершение архитектуры, тестовой и security-базы — R11, R13, R14

- [ ] У lifecycle services узкие Protocol/DTO; transaction ownership задаёт use case.
- [ ] Убрать оставшееся дублирование затронутых бизнес-правил в больших handlers/dispatcher.
- [ ] Architecture tests проверяют направление импортов и независимость use cases.
- [ ] Разделить parser fixtures, настоящие deterministic utterance tests и provider eval.
- [ ] Версионировать live runner/корпус: сначала проверить внешнюю зависимость, затем
      перенести принадлежащие проекту сценарии либо закрепить commit + checksum manifest.
- [ ] Full acceptance проверяет обязательные case IDs; subset не выдаёт себя за full run.
- [ ] Evidence включает app/runner SHA, corpus hash, prompt/model/config identity,
      начало/конец прогона и state/cleanup oracles без пользовательских payload/secrets.
- [ ] Аудировать locked cloud/macOS-STT профили, выпускать соответствующие SBOM.
- [ ] Проверить consent при смене получателей; negative authorization/egress tests.
- [ ] Описать threat model и процедуры ротации/восстановления credentials.

Готовность: намеренная поломка recognizer роняет utterance gate; пропуск обязательного
live-case роняет full gate; оба dependency profiles проверяются в CI. Coverage повышается
тестами найденных рисков, а не механическим достижением произвольного процента.

## Этап 7. Воспроизводимый release workflow — R12

- [ ] Tag release требует полного успешного quality gate на том же SHA; использовать
      reusable workflow или явно проверенный CI result с нужным составом jobs.
- [ ] Release manifest содержит версии, SHA, profile, schema head и ожидаемые assets.
- [ ] SHA256SUMS содержит только стабильные относительные имена файлов.
- [ ] Проверка скачанных assets выполняется из новой пустой директории; проверяется attestation.
- [ ] Негативные сценарии: tag без успешного gate, неполный manifest, несовпадающий SHA,
      отсутствующий/изменённый asset, недопустимый путь checksum.

Готовность: полный путь сборки и проверки нового candidate подтверждён.
Immutable v0.5.0 не изменяется. Новая версия выбирается после оценки совместимости и
состава изменений; номер и дата релиза не объявляются заранее доказанными.

## Этап 8. UX, документация и демонстрация — R15, R16

- [ ] Обновить README, architecture, operations, privacy, contributing и help.
- [ ] Сократить CLAUDE.md до актуальных инструкций/ссылок; удалить противоречивые описания.
- [ ] Добавить ERD, glossary дат/статусов, ADR транзакций, recurrence, retry и delivery.
- [ ] Разделить актуальные документы, backlog и исторические evidence; добавить план
      и release manifest в проверяемый documentation inventory.
- [ ] Пройти 10 UX-сценариев из R16 на синтетическом наборе; внести необходимые изменения
      подтверждений, уточнений, пагинации, ошибок и локального режима.
- [ ] Реализовать безопасный undo последнего поддерживаемого изменения с проверкой
      версии/связанных occurrences; при конфликте объяснять отказ, не затирать новые данные.
- [ ] Выполнить documented bootstrap/smoke/gate из чистого checkout.
- [ ] Подготовить 3–5 настоящих снимков синтетических диалогов и demo 60–90 секунд;
      показать также восстановление после ошибки. Не публиковать личную переписку.
- [ ] Измерить текст/голос latency, reminder lag, память и AI cost на версионированной
      синтетической нагрузке; сохранить методику, объём данных, окружение и ограничения.

Готовность: примеры документации соответствуют тестируемому поведению, новый специалист
может запустить проект и понять гарантии без истории сессий; demo-артефакты существуют,
а не только перечислены как будущая работа.

## Этап 9. Финальная приёмка, выпуск и контекст

- [ ] Проверить закрытие каждого R01–R16 по evidence-таблице; провести повторное ревью diff.
- [ ] Полный PostgreSQL gate, Ruff, mypy, security/dependency/history secret scans,
      complexity/coverage, documentation/version checks.
- [ ] Проверить container build/readiness/scan; для macOS выполнить ресурсный STT drill.
- [ ] Проверить реальное восстановление backup в изолированной БД.
- [ ] Для новой схемы — старый runtime на новой схеме и failure injection с реальными
      миграциями/DB-инвариантами, включая записи, созданные после обновления.
- [ ] Закоммитить и запушить завершённые изменения, пройти действующие PR/CI gates.
- [ ] Выпустить новый immutable release и проверить скачанные assets.
- [ ] Выполнить staged deploy, проверить release SHA, readiness и единственный poller.
- [ ] Прогнать полный versioned live E2E на точном deployed SHA, проверить state/cleanup.
- [ ] Подтвердить реальный scheduler cycle, отсутствие новых ошибок, backup/failed-delivery status.
- [ ] Записать acceptance report и session context с release/app/docs SHA, результатами,
      путями evidence и remaining work; закоммитить и запушить итоговые документы.

Релиз, деплой и внешние проверки относятся к последующей реализации, а не выполнены
подготовкой этого плана. Если интеграционная среда недоступна, точно указать непроверенный
gate; не заменять его локальным mock-test и не объявлять весь план завершённым.
Docs-only handoff commit после live-проверки отдельно отличать от проверенного runtime SHA.

## Контроль исполнения

| Пункт ревью | Основной этап | Статус | Commit / test / evidence |
| --- | --- | --- | --- |
| R01 | 1 | проверен локально | callback failure/missing tests; PostgreSQL gate 487 passed, 1 skipped |
| R02 | 4 | реализован, приёмка частичная | общие claims main/sweep; DB concurrency test |
| R03 | 4 | реализован, приёмка частичная | fresh-session failure record, failed/retry, backoff |
| R04 | 4 | частично | timezone series/backfill, future catch-up; календарные edge cases впереди |
| R05 | 1 | проверен локально | concurrent expired read/new claim и expired replacement; coverage 73,22% |
| R06 | 3 | частично | legacy completion helpers удалены; clear/reschedule/reopen tests; полный межканальный UX впереди |
| R07 | 5 | частично | concurrent retry, injection после реального COMMIT, child-task guard; полный live/voice впереди |
| R08 | 4 | реализован, приёмка частичная | weekly DeliveryBatch; full live впереди |
| R09 | 2 | проверен локально | typed payload, splitter boundaries; полный UX audit впереди |
| R10 | 2–3 | частично | explicit null/False, bound alarm sync; UX и reopen впереди |
| R11 | 2–6 | частично | application imports закрыты, InteractionPort; task-creation Any/UoW остаются |
| R12 | 7, 9 | частично | reusable CI release dependency, portable checksums/manifest; реальный release не выполнен |
| R13 | 6, 9 | частично | nested schema checks, actual recognizer corpus, runner lock 85 cases; provider/live впереди |
| R14 | 6, 9 | частично | consent fingerprint, stale-button и egress tests, threat model; STT SBOM/CI приёмка остаются |
| R15 | 8 | запланирован | — |
| R16 | 8–9 | запланирован | — |

Этапы выполняются по порядку; короткие ADR и обновления tests/docs делаются вместе
с соответствующим кодом, даже если итоговая редактура документации относится к этапу 8.
Промежуточный исправляющий релиз допустим после закрытия P1 и всех применимых release
gates; он не считается завершением всего плана и не откладывает оставшиеся R07–R16.

## Промежуточная реализация 04.09.2026

Этапы 2–5 реализованы частично, без объявления полного закрытия findings:

- Typed CommandResult вместо sentinel-протокола; application contracts вынесены
  из LLM. Explicit null/False сохраняются. HTML splitter имеет bounded fallback.
- Lifecycle update/complete/cancel синхронизирует связанные reminders;
  generic status update запрещён. Reopen recurring отклоняется до безопасного
  разрешения конфликта серии. Полная UX-приёмка и набор календарных границ впереди.
- Main/sweep разделяют durable claims; failed delivery видима через
  `/reminder_errors`; retry/backoff и timezone серии сохранены в БД.
  Weekly review переведён на DeliveryBatch.
- Durable action plan/result и атомарные effects; `/retry` продолжает сохранённый
  план; domain result отделён от Telegram response, декомпозиция имеет journal phase.
  Failed plans не удаляются обычной retention.

Полный локальный gate: **513 passed, 1 skipped; coverage 73.47%**. Миграции
до `c8e1f3a5b702`, schema drift и complexity ratchet прошли. Это evidence только
локального состояния, не релиза. Контракты и ограничения:
[ADR retry/delivery](ADR_2026-09-04_RETRY_AND_DELIVERY.md).

**Следующее действие исполнителя:** добрать failure/concurrency/UX приёмку этапов
3–5, затем завершить архитектурные ports, eval/security и release evidence этапов
6–9. Новая схема ещё не разрешена к production deploy: совместимость rollback со
старым sender требует отдельного доказательства. Ни одно оставшееся finding не
считать закрытым по одному общему зелёному coverage gate.

Обновление 05.09.2026: после дополнительных architecture/eval/release checks полный
PostgreSQL gate — **532 passed, 1 skipped; coverage 73.52%**. Ruff, mypy (120 файлов),
Bandit, version/docs checks и runner lock verification проходят локально.
Live-проверки и GitHub Actions в этом checkpoint не запускались. ShellCheck локально
не установлен; `bash -n` изменённого live-wrapper прошёл, полноценный ShellCheck
остаётся CI gate. Это не завершение плана и не release acceptance.

Продолжение 05.09.2026: снят публичный CRUD bypass завершения задач; перенос
напоминания отзывает старый lease и сбрасывает retry state. Добавлены DB проверки
clear/reschedule/protected recurring reopen, конкурентного retry и потерянного
подтверждения после реального COMMIT (инъекция исключения в клиентском адаптере,
не сетевой proxy drill). Запрещено наследовать command session в дочернюю coroutine.

Consent теперь связан с fingerprint получателей/endpoint; старые consent не
backfill-ятся как разрешённые. Проверяются устаревшие обычные/onboarding кнопки,
текст/голос и отзыв согласия между записями reindex batch. Новый schema head:
`d9f2a4b6c803`. Downgrade этой миграции сбрасывает cloud consent перед удалением
fingerprint, чтобы старый runtime не расширил доступ молча. Это **не** разрешение
на rollback reminder/action-journal migrations; их compatibility gate остаётся.

Итог продолжения: **557 passed, 1 skipped; coverage 74.01%**, миграции/schema drift,
complexity и coverage gates зелёные. Ruff/mypy/Bandit/docs/version checks проходят.
GitHub CI, live-приёмка и deploy в этом продолжении не выполнялись.

Rollback step 05.09.2026: выполнен новый реальный Docker/PostgreSQL drill со старым
runtime `27ce9e0` в отдельной frozen-среде. Старый preflight отвергает новую схему;
принудительное разрешение head не делает sender совместимым (он выбирает failed
и leased occurrences). Downgrade с pending plan блокируется атомарно, включая
сохранение consent state. Восстановление предмиграционного снимка в другую БД
прошло старые preflight, singleton/schema/pgvector smoke и domain read/write.
База кандидата сохранялась до завершения проверки.

[Evidence и границы](MIGRATION_ROLLBACK.md). Drill добавлен в reusable CI,
но remote CI ещё не подтверждён. Обычный installer по-прежнему правильно блокирует
новую схему; allowlist не расширялся. **Следующий шаг:** guarded maintenance-deploy
с остановкой writers, snapshot identity и запретом молчаливой потери post-snapshot
updates. Эта проверка не разрешает code-only/zero-downtime rollback.

Продолжение maintenance (05.09): добавлены durable orchestration и PostgreSQL
snapshot/data-guard/separate-restore компоненты. Реальные DB-тесты проверяют
экспортированный snapshot, post-snapshot writes, новые recovery-поля, доступ
application role к восстановленной БД и сохранение failed target. Это ещё не
закрытие deployment gate: впереди freeze/lease/launchd adapter, проверка точных
runtime/config identities и интеграция с orchestration без раннего polling.

Следующий checkpoint: реализованы `maintenance_launchd.py` и `maintenance_lease.py`.
Проверяются persistent disable/bootout, живое владение runtime lock, отсутствие
других DB connections/prepared transactions и journal-bound activation. Launch
обходит `run.sh`, чтобы не повторять migrations до singleton; uncertainty после
admission приводит к halt без snapshot rollback. launchd пока только simulated,
lock проверяется на реальном PostgreSQL. На этом checkpoint composition и CLI ещё
не были реализованы; их следующий checkpoint описан ниже.

Composition/CLI checkpoint: `MacMaintenance` связывает PostgreSQL, source/target
leases, journal и launchd. Проверяются exact Git blobs/modes, config/lock/interpreter
fingerprints и frozen offline environment consistency; конфигурация перепроверяется
перед фазами. CLI по умолчанию только строит план, execution требует macOS и точного
confirmation identifier для текущей операции/identity. Сквозные DB-сценарии покрывают
успех, безопасное восстановление до admission, сохранение новых данных и uncertain
activation. Это implementation/local evidence, не production acceptance: впереди
exact-release/native macOS rehearsal, remote CI/live/profile gates и downtime window.

Quality gate этого шага: 564 passed, 1 skipped; coverage 72.89% с новыми
operational scripts. Реальный migration drill выполнялся отдельно от pytest
coverage. Пороговые проверки, линтер, типизация, Bandit и docs gate прошли.
