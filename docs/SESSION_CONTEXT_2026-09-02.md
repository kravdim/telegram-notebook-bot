# Контекст доводки DailyPlanner — 02.09.2026

## Текущее состояние

Независимое ревью `REVIEW_2026-09-02_INDEPENDENT.md` разобрано и закрыто
release-пакетом `81abae0`; обнаруженный первым staged deploy дефект пути venv
исправлен отдельным commit `070c9a7`. Именно полный SHA
`070c9a732407d91d4f3a5ee61af6dae78e37dc0f` развёрнут в production и помечен
annotated tag `v0.3.0`.

Production LaunchAgent использует versioned release directory, имеет PID
`11296`, `runs=1`, `last exit=(never exited)` и публикует свежий heartbeat с
точным release SHA. Deploy report: `status=deployed`, previous `e373b8b`,
rollback `not_required`. Предыдущая версия подготовлена как автоматический
rollback target.

## Что исправлено

- task-list запросы с tomorrow/yesterday/weekday/date/project/trip/category/
  priority/person больше не превращаются молча в `today` или `all`;
- добавлены typed fail-close recognition result и пользовательское уточнение;
- task creation recognizer вынесен из Telegram handler в application layer;
- strict mypy распространяется на весь `bot.application`, AST-test защищает
  transport/persistence boundaries новых recognizers;
- macOS deploy стал staged: immutable revision, mutex, config/DB/migration,
  Telegram `getMe`, STT warmup и plist checks до switch, release-SHA readiness
  после switch, report и automatic rollback;
- quick start разделён на минимальный профиль без Ollama/Whisper и полный
  локальный macOS профиль;
- CI запускает документированный canonical local gate и проверяет ShellCheck,
  version consistency, coverage XML, critical и central-risk ratchets;
- CODEOWNERS исправлен и расширен; tag workflow создаёт полноценный GitHub
  Release с image, SBOM, checksums и Sigstore/in-toto attestation.

## Test, CI и release evidence

- canonical local PostgreSQL gate: `422 passed, 1 skipped`, coverage `70,57%`,
  critical modules `85–100%`, central-risk ratchets PASS;
- Ruff, mypy (109 source files), Bandit, dependency audit, secret scan,
  documentation, ShellCheck и actionlint: PASS;
- canonical GitHub CI run `33601624825`: `quality`, `secrets`,
  `developer-bootstrap`, `container-e2e`, `canonical-local-gate` — PASS;
- pre-deploy backup:
  `notebook_bot_2026-09-02_095613.sql.gz`, 1 232 454 bytes, SHA-256
  `84b06edd2a4ee86e9e6d8c027b91f1362a5a5d399c41c782957cc194625190bc`,
  gzip и sidecar PASS;
- recovery LaunchAgent: 20 tables, migration `a6c9d1e4f7b2`, users 2,
  tasks 121, RTO 0,85 s, exit 0;
- native STT resource drill: 20/20 транскрипций, model/thread cleanup PASS за
  66,80 s на production-class Mac;
- live Telegram run `DP-20260902T073853-f97522`: `85/85 PASS` за 811 s,
  state oracle `12/12`, teardown и cleanup oracle PASS, включая новые
  tomorrow/project/trip negative cases. Report:
  `/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260902_105230.md`;
- release workflow `33609327921`: PASS. GitHub Release:
  `https://github.com/kravdim/telegram-notebook-bot/releases/tag/v0.3.0`;
  assets: `dailyplanner-image.tar`, `dailyplanner.sbom.cdx.json`,
  `SHA256SUMS`, `attestation.json`.

Post-live SLO: reminders `ok`, lag 0 s, pending 0; backup `ok`, artifact
`metadata-ok`, age 1,6 h. Heartbeat release SHA, annotated tag target and
deployed revision совпадают: `070c9a7`.

## Repository governance

`main` защищена GitHub branch protection: PR required, admins included,
strict up-to-date branch, required checks `quality`, `secrets`,
`developer-bootstrap`, `container-e2e`, `canonical-local-gate`, linear history,
conversation resolution, force-push и deletion запрещены.

В репозитории только один collaborator (`kravdim`), поэтому обязательный
approval сейчас установлен в `0`: GitHub не разрешает автору одобрить свой PR,
а значение `1` сделало бы repository немержабельным. После приглашения второго
reviewer нужно включить `required_approving_review_count=1` и
`require_code_owner_reviews=true`, затем подтвердить тестовым PR.

## Оставшийся внешний scope

- юридическое заключение по privacy notice, consent и retention для целевого
  рынка может дать только назначенный legal/product owner;
- дальнейшая декомпозиция `dispatcher.py`, `commands.py`, `main.py` и удаление
  оставшихся legacy complexity exceptions остаются очередным архитектурным
  milestone, не блокирующим зафиксированный production-correctness release.
