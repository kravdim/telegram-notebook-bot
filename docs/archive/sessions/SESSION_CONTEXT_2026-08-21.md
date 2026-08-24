# Контекст сессии — завершение beta remediation 21.08.2026

## Исходная задача

Разобрать результаты живого бета-теста из
[`BETA_TEST_2026-08-20.md`](../reviews/BETA_TEST_2026-08-20.md), составить план исправлений,
реализовать его, проверить и развернуть исправления в production.

## Что сделано

- Защита от ложных подтверждений LLM расширена на задачи, заметки, дни
  рождения, напоминания и проекты.
- Для явного изменения данных без tool call выполняется один повтор с
  `tool_choice=required`; повторная неудача завершается без изменения данных.
- Мемуарник больше не поглощает командоподобные сообщения, его TTL сокращён
  до 60 минут, добавлена кнопка «Пропустить».
- Локальный Whisper предварительно загружается, работает offline, прогревается
  при старте и использует отдельный доступный LaunchAgent кэш. Голосовой
  обработчик сразу показывает статус и ограничен таймаутом.
- Повторяемые действия маршрутизируются в recurring tasks, а
  `create_reminder` используется для явных просьб напомнить.
- Убран дубль итогового сообщения при декомпозиции проекта.
- Добавлены метрика и throttled admin alert для `TelegramConflictError`.
- Улучшены валидация и resume онбординга, вывод `/notes`, value-эвристика и
  подтверждение snooze в таймзоне пользователя.
- Добавлена миграция активного intent-промпта
  `e8c1f4a7b2d9_beta_intent_hardening`.
- Добавлены regression-тесты и новые golden intent cases.

Подробный чек-лист:
[`BETA_REMEDIATION_PLAN_2026-08-21.md`](../reviews/BETA_REMEDIATION_PLAN_2026-08-21.md).

## Проверка

- Unit/regression: `98 passed, 4 skipped`.
- Golden LLM contracts: `6/6`, intent accuracy `1.000`, invalid tool rate
  `0.000`.
- CI lint gates и operational mypy прошли.
- Alembic: один head `e8c1f4a7b2d9`.
- PostgreSQL integration drill и backup/restore drill прошли.
- Целевой live Telegram E2E: 8/10 в первом прогоне. B3 выявил оставшуюся
  форму «Сделай заметку» и после исправления повторно прошёл при активном
  persistent-состоянии мемуарника. G3 фактически выполнил snooze, а найденное
  отображение UTC и неясный текст подтверждения исправлены и покрыты тестом.

Live-отчёты юзербота находятся вне этого репозитория:

- `/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260821_221342.md`
- `/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260821_221508.md`

## Production на момент завершения

- Бот развёрнут через macOS LaunchAgent `com.notebook-bot`.
- Production migration: `e8c1f4a7b2d9`.
- Единственный активный poller работает через LaunchAgent; tmux-сессия
  `dailyplanner-bot` остановлена и отсутствует.
- STT успешно загружен и прогрет из
  `/Users/moltbot/Library/Caches/notebook-bot/huggingface`.
- Production backup:
  `/Users/moltbot/backups/notebook-bot/notebook_bot_2026-08-21_2143.sql.gz`.

## Эксплуатационная оговорка

Исходный `/Users/moltbot/DevOps/scripts/macmini_boot_recovery.sh` уже не
запускает DailyPlanner через tmux, но установленная root-копия
`/usr/local/sbin/macmini_boot_recovery.sh` осталась старой: для её замены нужен
пароль администратора. В `bot/main.py` добавлен fail-safe: процесс внутри tmux
не запускает polling без явного `DAILYPLANNER_ALLOW_TMUX=1`, поэтому старая
root-копия не может создать второго poller.

## Что осталось вне scope релиза

- Полный исторический E2E-прогон всех 52 кейсов не повторялся; выполнен
  целевой прогон всех затронутых сценариев.
- Подключение 35-минутного E2E-runner к CI по ручному trigger остаётся
  отдельной инфраструктурной задачей.
- Репозиторий содержит исторический lint/typecheck debt вне строгих CI-gates;
  он не расширялся в рамках beta remediation.
