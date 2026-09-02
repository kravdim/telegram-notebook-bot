# Финальная перепроверка remediation DailyPlanner

**Дата:** 2 сентября 2026 года  
**Проверенный `HEAD`:** `cb96fdd5e08629f39843b198c21b86d5205adcaa`  
**Проверенная release/deploy revision:**
`070c9a732407d91d4f3a5ee61af6dae78e37dc0f` (`v0.3.0`)  
**Основание:**
[`REVIEW_2026-09-02_INDEPENDENT.md`](REVIEW_2026-09-02_INDEPENDENT.md) и
[`REVIEW_2026-09-02_INDEPENDENT_REMEDIATION.md`](REVIEW_2026-09-02_INDEPENDENT_REMEDIATION.md)  
**Решение:** **remediation существенно улучшила проект, но заявление «всё
исправлено» пока не подтверждается. Для безусловного статуса
образцово-показательного проекта остаются P1.**

## 1. Итог приёмки

Рабочая версия стабильна и пригодна для контролируемой демонстрации. Основной
пользовательский регресс прошлого ревью устранён, production действительно
работает на tagged revision, полный локальный gate и GitHub CI зелёные, а
release evidence опубликован.

Безусловная финальная приёмка пока не выдана по трём причинам:

1. rollback-контур не доказан исполняемыми failure-injection тестами и имеет
   необработанные failure paths;
2. `main` допускает merge без независимого approval и CODEOWNERS review;
3. заявленная архитектурная декомпозиция остановилась на первом шаге: основные
   mega-modules и legacy complexity exceptions сохранены.

Дополнительно найден небольшой воспроизводимый дефект нормализации заголовка
задачи и остаётся внешний legal gate.

## 2. Что независимо подтверждено

### 2.1. Код и локальные проверки

- `scripts/run_local_test_gate.sh`: **422 passed, 1 skipped**;
- total coverage: **70,57%** при floor 70%;
- critical coverage gates: **85–100%**;
- Ruff: PASS;
- mypy: PASS, 109 source files;
- Alembic fresh upgrade и schema drift check: PASS;
- documentation contract, version consistency и tracked-file secret scan:
  PASS;
- shell syntax и все macOS plist: PASS;
- рабочее дерево было чистым до добавления настоящего review-файла.

### 2.2. Исправления прошлого ревью

- `task_query_recognizer` больше не превращает tomorrow/yesterday/weekday/date,
  project/trip/category/priority/person filters в `today` или `all`;
- unsupported qualifier приводит к явному уточнению до dispatch/LLM;
- task-list и task-creation recognizers вынесены в `bot.application`, strict
  mypy и AST boundary test работают;
- появился реально воспроизводимый minimal profile без Ollama/Whisper;
- CI исполняет документированный canonical local gate, ShellCheck, coverage
  artifacts, version check и risk ratchets;
- CODEOWNERS paths исправлены;
- staged installer использует versioned directories, prechecks, readiness SHA,
  deploy mutex и rollback target;
- `v0.3.0` является annotated tag и указывает на deployed SHA `070c9a7`;
- GitHub Release содержит image tar, CycloneDX SBOM, SHA256SUMS и provenance
  attestation;
- CI release revision `070c9a7` и документационного `HEAD` `cb96fdd` зелёный;
- production LaunchAgent находится в состоянии `running`, а свежий heartbeat
  подтверждает PID и точный SHA `070c9a7`.

Проверенные внешние evidence:

- [CI release revision](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33601624825);
- [CI current HEAD](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33613076182);
- [release workflow](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33609327921);
- [GitHub Release v0.3.0](https://github.com/kravdim/telegram-notebook-bot/releases/tag/v0.3.0).

## 3. P1 — требуется до заявления «всё исправлено»

### FINAL-001 — rollback существует в коде, но не доказан отказами

**Риск:** production availability и ложное ощущение recoverability.

Текущие
[`test_macos_staged_deploy_contract.py`](../tests/test_macos_staged_deploy_contract.py)
не запускают installer: они только ищут строки в shell-файле. Поэтому не
проверены требовавшиеся invalid token, DB outage, failed STT warmup, readiness
timeout и failure самого rollback.

В [`platform/macos/install.sh`](../platform/macos/install.sh):

- `alembic upgrade head` выполняется до Telegram/STT checks. Если последующий
  pre-switch check падает, старый процесс остаётся активным уже на изменённой
  схеме БД; совместимость предыдущей версии со схемой не проверяется;
- pre-switch failures завершаются через `set -e` без записи
  `last-deploy-report.txt`;
- внутри `rollback()` вызов `launchctl load "$PLIST_DST"` не обёрнут обработкой
  ошибки. Его отказ может завершить скрипт раньше ветки `status=rollback_failed`;
- успешный deployment подтвердил только happy path: фактический rollback этой
  версии не выполнялся (`rollback=not_required`).

**Требуется:** executable harness со stub/fake `launchctl` и изолированной БД;
failure matrix для всех перечисленных этапов; deploy report на каждом выходе;
явная expand/contract migration policy либо автоматическая проверка backward
compatibility; отдельный recovery drill с намеренно неготовым candidate.

### FINAL-002 — protected main всё ещё допускает неотревьюенный merge

**Риск:** process integrity и слабая демонстрация командной инженерной практики.

GitHub branch protection действительно требует PR, актуальную ветку и пять
зелёных checks, запрещает force-push/deletion и применяется к admin. Однако
фактические настройки показывают:

- `required_approving_review_count = 0`;
- `require_code_owner_reviews = false`;
- PR [#3](https://github.com/kravdim/telegram-notebook-bot/pull/3) был смержен
  без requested reviewers и без единого review.

Это осознанное ограничение репозитория с одним collaborator, но оно прямо не
выполняет критерий «main не допускает неотревьюенные изменения».

**Требуется:** пригласить минимум одного независимого reviewer, включить один
обязательный approval, CODEOWNERS review, dismissal stale reviews и approval
последнего push; подтвердить правила тестовым PR. До этого не называть
governance полностью закрытым.

### FINAL-003 — архитектурный P1 отложен, а не закрыт

**Риск:** reviewability заказчиком, регрессионный радиус и стоимость развития.

Текущее состояние:

| Поверхность | Факт |
|---|---:|
| `bot/handlers/messages.py` | 1628 строк |
| `bot/handlers/commands.py` | 1223 строки |
| `bot/llm/dispatcher.py` | 1125 строк |
| Ruff с `--ignore-noqa` | 20 нарушений |
| `main()` | complexity 45, 178 statements |
| `_handle_create_task()` | complexity 43, 43 branches, 104 statements |
| `_extract_common_intent()` | complexity 35, 34 branches, 21 returns |

Сохраняются 10 помеченных `REVIEW-20260829 legacy ratchet` функций. Из
Telegram handler вынесен task creation recognizer, но project/note/reminder use
cases, dispatcher services и startup lifecycle по-прежнему не получили
требуемых физических application boundaries.

**Требуется:** оформить issues с owner/milestone для каждого exception;
последовательно вынести typed use cases; разделить dispatcher и startup
lifecycle; запретить новые исключения; удалять существующие по измеримому
ratchet. Для портфолио перед техническим заказчиком хотя бы центральные task и
startup paths должны пройти этот этап.

## 4. P2 — качество и полировка

### FINAL-004 — быстрый task parser загрязняет title извлечёнными qualifiers

Воспроизводимые результаты:

| Ввод | Фактический результат |
|---|---|
| `Надо купить молоко завтра` | title `Купить молоко завтра`, date также заполнена |
| `Надо отправить отчёт, приоритет высокий` | title содержит `приоритет высокий`, priority=`high` |

Дата и приоритет не теряются, но одновременно сохраняются как часть названия.
Нужно типизированно выделять и удалять только распознанный qualifier из title,
добавив parameterized tests для leading/trailing date, urgency и explicit
priority. Неизвестные формулировки должны fail-close, а не обрезаться.

### FINAL-005 — quality gates зелёные, но запас остаётся минимальным

Overall coverage превышает порог только на 0,57 п.п. Risk floors заметно ниже
обычного critical уровня: `main.py` 25% (факт 26%), `llm/client.py` 40% (43%),
`commands.py` 45% (49%), `dispatcher.py` 50% (54%). Ratchet полезен, но пока
защищает низкую стартовую базу.

Нужно повышать пороги вместе с behavioral/failure tests, начиная со startup,
provider failover, interaction CAS, voice state transitions и installer. Для
чистой application/domain логики стоит откалибровать mutation baseline.

### FINAL-006 — release evidence долговечен, но GitHub Release изменяемый

Release API сообщает `immutable: false`. Actions attestation и checksums
существенно улучшают supply-chain evidence, но владелец всё ещё может заменить
release assets. Для эталонного публичного процесса рекомендуется включить
immutable releases либо публиковать image по digest в registry и подписывать
tag/image; release workflow должен проверять digest после публикации.

### FINAL-007 — legal gate остаётся внешним и незакрытым

Privacy implementation и документация выглядят зрелыми, но юридическая
проверка notice, consent и retention для целевого рынка не выполнена. До
обработки реальных клиентских данных требуется письменное решение legal/product
owner либо явное ограничение демонстрации синтетическими данными.

## 5. Критерий следующей приёмки

Повторный прогон можно считать финальным после одновременного выполнения:

1. executable failure-injection suite и успешный intentional rollback drill;
2. независимый обязательный approval и CODEOWNERS enforcement;
3. закрытый первый архитектурный milestone без центральных legacy exceptions;
4. исправленная нормализация task title;
5. formal legal sign-off или документированное исключение scope;
6. новый release SHA проходит CI → backup/restore → failed-candidate rollback
   drill → deploy → live E2E → tag/release → post-deploy SLO.

До этого корректная формулировка статуса: **сильный работающий production
release и хороший демонстрационный проект, но ещё не безусловный эталон
инженерного процесса**.
