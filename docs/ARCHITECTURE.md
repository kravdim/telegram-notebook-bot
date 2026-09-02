# Architecture boundaries

DailyPlanner is one asynchronous process with PostgreSQL as the durable source
of truth. Telegram, LLM, STT and embeddings are external adapters; failure of an
AI adapter must not stop scheduler delivery.

```text
Telegram updates
      │
      ▼
handlers ──► IntentNormalizer / typed intents ◄── LLM adapter
      │                    │
      │                    ▼
      │                command bus ──► services / db/crud ──► PostgreSQL
      │                                                        ▲
      └──────── schedulers ──► delivery service/outbox──────────┘
                                      │
                                      ├──► Telegram delivery
                                      └──► observability/SLO alerts
```

## Dependency rules

1. `handlers/` translates Telegram input/output. Простые scoped reads и
   настройки пока могут открывать сессию напрямую; cross-entity invariants
   обязаны жить в application service (например, task completion workflow).
2. `application/intents.py` is the provider-independent command contract.
   Deterministic rules and `llm/contracts.py` both enter the same typed command
   bus; unknown tools and fields fail closed. Typed `CommandResult` reaches the
   Telegram adapter without sentinel-string parsing. `llm/dispatcher.py` still
   contains compatibility executors for existing use cases; moving them into
   smaller application services remains an explicit modularity follow-up, not
   a completed dependency inversion.
   The inbound boundary additionally returns a typed `MessageOutcome`. Only
   `completed` proves that a voice-confirmed workflow may discard its
   transcript. Mutation requests fail closed unless a mutating `CommandResult`
   was executed; `clarify_request` can request missing input but cannot claim a
   side effect.
3. `db/crud/` — repository boundary для операций одной сущности. Multi-row
   workflows живут в `services/`; task writes используют optimistic `version`
   guard и row locks там, где нужна межканальная идемпотентность.
4. `scheduler/` reads durable state. Multipart digest/memoir payloads are
   immutable `delivery_batches` with per-message progress in `delivery_parts`;
   a DB lease excludes concurrent senders and retries resume pending parts. The
   runtime advisory lock permits only one scheduler/polling process.
5. `observability.py` may read operational state but must never be required for a
   successful domain write or a valid backup archive.
6. `services/user_export.py` derives the complete versioned dataset inventory
   from the same SQLAlchemy metadata and special ownership rules as deletion,
   and verifies row counts before handing JSONL streams to the bounded,
   disk-backed archive writer in `services/export.py`. Telegram handlers never
   hold the complete ZIP payload in process memory.
7. Multi-step interaction workflows have one PostgreSQL slot per user and are
   accessed through `application/interactions.py`. Claims, transitions and
   clears are compare-and-set operations by type and session token. Voice and
   memoir callbacks additionally verify the originating Telegram message ID;
   stale buttons cannot mutate a newer session. Memoir and chronometry finish
   their domain write and state deletion in one transaction. Process-local
   flags are caches only, never a source of truth.
8. `application/normalizer.py` performs only conservative, meaning-preserving
   normalization and retains opaque user markers. Language-provider heuristics
   do not belong in domain services.
9. The inbound message adapter executes explicit phases: deterministic task
   shortcuts, durable interaction workflows, provider-independent recognizers,
   LLM request/mutation guard, metadata logging and Telegram presentation.
   `application/task_query_recognizer.py` and
   `application/task_creation_recognizer.py` have no Telegram, persistence or
   provider objects. An AST boundary test enforces this rule, and strict mypy
   settings cover the complete `bot.application` package.

New functions are capped by Ruff at complexity 15, 20 branches, 12 returns and
80 statements. Nine pre-existing hotspots carry 16 inline complexity exceptions
across product and operational code (15 under `bot/`).
`scripts/check_complexity_ratchet.py` stores a numerical ceiling for every
allowlisted function and Ruff metric (complexity, branches, returns and
statements), rejects any increase, and requires a retired suppression and its
baseline to disappear together. Removing those
exceptions is incremental architecture work; widening the global limits is not
an accepted shortcut.

The first central-path milestone is complete. Background scheduling and STT
warmup are owned by `bot.runtime.background`, while `bot.main` is now a 295-line
composition root with no complexity suppression. Task creation is a typed,
transport-independent use case in `bot.application.task_creation`; its former
43-complexity/104-statement dispatcher function is now a thin adapter without a
suppression. The remaining owned milestones are Telegram intent adaptation and
list/update task execution ([issue #4](https://github.com/kravdim/telegram-notebook-bot/issues/4),
owner: `kravdim`, milestone `v0.4.0`) and command presentation
([issue #5](https://github.com/kravdim/telegram-notebook-bot/issues/5), owner:
`kravdim`, milestone `v0.5.0`).

New business workflows should first become a service function callable without
Telegram objects. Handlers should remain thin adapters around that function.

## Deployment boundaries

The macOS LaunchAgent is the primary production target and may use local Ollama
and Whisper. The Docker target is cloud-only and has no host model dependency.
Both targets run the same migrations and preflight. The macOS installer stages
immutable Git revisions and publishes a release-identified heartbeat only after
singleton acquisition and Telegram polling have remained alive; a bounded
failure restores the previous plist and revision. Docker additionally exposes
an out-of-process readiness contract backed by an atomic event-loop heartbeat;
the probe combines that liveness evidence with config, PostgreSQL and Alembic
head checks. Its hermetic CI E2E exercises image, entrypoint, extensions, schema
and vector storage while deliberately replacing Telegram/provider traffic with
a long-running smoke runtime.

Backup creation uses the normal application role and never requires database
creation rights. Recovery is a separate operational boundary: a CREATEDB-only
role may clone a locked extension template and owns only disposable drill
databases. The application process never receives this credential, and the
recovery process never receives the application's Telegram or AI credentials.
