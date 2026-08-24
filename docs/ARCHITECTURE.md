# Architecture boundaries

DailyPlanner is one asynchronous process with PostgreSQL as the durable source
of truth. Telegram, LLM, STT and embeddings are external adapters; failure of an
AI adapter must not stop scheduler delivery.

```text
Telegram updates
      │
      ▼
handlers ──► llm/contracts + dispatcher ──► db/crud ──► PostgreSQL
      │                                              ▲
      └──────── schedulers ──► delivery service/outbox┘
                                      │
                                      ├──► Telegram delivery
                                      └──► observability/SLO alerts
```

## Dependency rules

1. `handlers/` translates Telegram input/output. Простые scoped reads и
   настройки пока могут открывать сессию напрямую; cross-entity invariants
   обязаны жить в application service (например, task completion workflow).
2. `llm/contracts.py` validates provider output before `dispatcher.py` invokes a
   mutation. Unknown tools and unknown fields fail closed.
3. `db/crud/` — repository boundary для операций одной сущности. Multi-row
   workflows живут в `services/`; task writes используют optimistic `version`
   guard и row locks там, где нужна межканальная идемпотентность.
4. `scheduler/` reads durable state. Multipart digest/memoir payloads are
   immutable `delivery_batches` with per-message progress in `delivery_parts`;
   a DB lease excludes concurrent senders and retries resume pending parts. The
   runtime advisory lock permits only one scheduler/polling process.
5. `observability.py` may read operational state but must never be required for a
   successful domain write or a valid backup archive.

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
