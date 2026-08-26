# Контекст релиза DailyPlanner — 26.08.2026

## Итог

Повторный аудит `REVIEW_2026-08-25.md` применён, reliability-пакет и следующий
application architecture этап завершены. Product commit `6b1acad` отправлен в
`origin/main`, GitHub Actions run `32876119870` завершён успешно, production
развёрнут штатным macOS LaunchAgent.

## Что реализовано

- Закрыты подтверждённые пункты повторного аудита: ORM/CI schema drift,
  recurrence reminder policy, interaction-state CAS, Groq runtime validation,
  privacy rollback, delivery lease fencing, backup SLO/catch-up, truthful
  callbacks и p95 nearest-rank.
- Добавлен provider-independent application layer: строгие typed intents,
  `IntentNormalizer`, `InteractionService`, `CommandBus` и typed
  `CommandResult`. Детерминированные и LLM-команды проходят один контракт и
  реестр исполнителей.
- Voice, memoir, chronometry, project-completion и memoir callback переведены
  на единый PostgreSQL interaction service. Consume workflow выполняется
  атомарно под row lock.
- Архитектура, operations/privacy и remediation evidence обновлены; review
  сохранён в `docs/archive/reviews/`.

## Quality evidence

- Полный локальный suite с PostgreSQL: `202 passed`;
- Ruff и mypy (`98 source files`): успешно;
- Bandit medium/high, secret scan и `git diff --check`: успешно;
- LLM contracts: 6/6 parser cases, 16/16 saved utterance cases;
- Alembic upgrade/check: head `f4b8c2d6e1a0`, новых операций нет;
- GitHub Actions: run `32876119870`, commit `6b1acad`, conclusion `success`.

## Backup, recovery и production

Перед deploy создан backup
`/Users/moltbot/backups/notebook-bot/notebook_bot_2026-08-25_200832.sql.gz`
(1 166 083 bytes, SHA-256
`ed885fcf36d5b53e8cde6ead4675ceff1e8a7d67cd1395348d0f9e5bd9340a3d`).
Gzip integrity и sidecar checksum подтверждены.

Recovery drill запущен через установленный LaunchAgent, потому что прямой
Codex tool context не получает пользовательский Keychain access. Drill завершён
exit code 0: 20 public tables, migration `f4b8c2d6e1a0`, 1 user, 115 tasks,
3 delivery batches, RTO 0,29 секунды; disposable database удалена.

Production установлен `platform/macos/install.sh`. После финальной очистки
native STT workers LaunchAgent `com.notebook-bot` работает одним экземпляром с
PID `1826`, singleton lease получен, Telegram polling активен, preflight и
Whisper warm-up прошли.

## Live E2E и найденная host-проблема

Dedicated E2E user `8514454144` оказался удалён до релиза. Его allowlisted
prerequisite восстановлена только для тестового аккаунта; проверка onboarding
дала 3/3 PASS. После этого полный live run прошёл все 78 non-voice сценариев,
но voice-сценарии попали под тяжёлую внешнюю CPU-нагрузку. Отчёт:
`/Users/moltbot/Projects/userbot/tests_dailyplanner/results/`
`report_20260825_221438.md`.

Диагностика Mac mini обнаружила пять orphan процессов
`payload ... generate:types` из `/Users/moltbot/Projects/studio`, каждый
потреблял около одного CPU core. Load достигал 32,8 при 0% idle; memory pressure
оставался нормальным и swap не использовался. Две точные группы были завершены
через TERM, CPU временно восстановился до 72–80% idle. На здоровом CPU
изолированный voice gate прошёл 4/4 за 51 секунду:
`report_20260826_084357.md`. Таким образом, все 82 product-контракта получили
PASS evidence в последовательных 78/78 + 4/4 прогонах, teardown обоих запусков
успешен.

Источник Studio generators остаётся внешним инфраструктурным follow-up: после
двух остановок процессы появились снова с PPID 1. Kill-loop прекращён согласно
operations policy; требуется найти automation/session, которая повторно
запускает `payload generate:types`. Это не дефект DailyPlanner, но при высокой
нагрузке локальный Whisper может превысить 90-секундный timeout.
На финальной контрольной сверке `pgrep` уже не находил generators без третьего
TERM, однако источник повторного запуска не установлен, поэтому риск рецидива
остаётся открытым.

## Связанный userbot runner

Voice Q1 теперь ждёт confirm-кнопки, а не считает промежуточное
«Распознаю…» завершением кейса; это исключает параллельный запуск Q1/Q3.
Изменение зафиксировано локальным commit `0521822` в
`/Users/moltbot/Projects/userbot`. У checkout нет remote; посторонние untracked
`REVIEW_2026-08-19.md` и `tests_kuzya/*` не изменялись.

## Состояние для следующей сессии

Product release завершён и работает. Следующую сессию начать с read-only
проверки источника recurring Studio `generate:types`, затем повторить единый
82/82 live gate при устойчивом CPU idle. Не продолжать безадресный kill-loop и
не перезапускать DailyPlanner: текущий PID здоров, а причина STT degradation
находится вне приложения.
