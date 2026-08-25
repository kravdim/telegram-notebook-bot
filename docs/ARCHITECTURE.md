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
   bus; unknown tools and fields fail closed. `llm/dispatcher.py` is now an
   adapter from provider calls to stable business handlers, not an intent
   router.
3. `db/crud/` — repository boundary для операций одной сущности. Multi-row
   workflows живут в `services/`; task writes используют optimistic `version`
   guard и row locks там, где нужна межканальная идемпотентность.
4. `scheduler/` reads durable state. Multipart digest/memoir payloads are
   immutable `delivery_batches` with per-message progress in `delivery_parts`;
   a DB lease excludes concurrent senders and retries resume pending parts. The
   runtime advisory lock permits only one scheduler/polling process.
5. `observability.py` may read operational state but must never be required for a
   successful domain write or a valid backup archive.
6. User export is disk-backed and bounded in `services/export.py`; Telegram
   handlers never hold the complete ZIP payload in process memory.
7. Multi-step interaction workflows have one PostgreSQL slot per user and are
   accessed through `application/interactions.py`. Claims, transitions and
   clears are compare-and-set operations; schedulers must not replace voice,
   chronometry, memoir or project-completion state owned by another workflow.
   Process-local flags are not a source of truth.
8. `application/normalizer.py` performs only conservative, meaning-preserving
   normalization and retains opaque user markers. Language-provider heuristics
   do not belong in domain services.

New business workflows should first become a service function callable without
Telegram objects. Handlers should remain thin adapters around that function.

## Deployment boundaries

The macOS LaunchAgent is the primary production target and may use local Ollama
and Whisper. The Docker target is cloud-only and has no host model dependency.
Both targets run the same migrations and preflight. Docker additionally exposes
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
