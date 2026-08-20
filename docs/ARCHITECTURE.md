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
      └──────────────── schedulers ──────────────────┘
                         │
                         ├──► Telegram delivery
                         └──► observability/SLO alerts
```

## Dependency rules

1. `handlers/` translates Telegram input/output and owns no persistence query.
2. `llm/contracts.py` validates provider output before `dispatcher.py` invokes a
   mutation. Unknown tools and unknown fields fail closed.
3. `db/crud/` is the repository boundary. Multi-row invariants and commits live
   there; task writes use an optimistic `version` guard.
4. `scheduler/` reads durable state and records delivery before advancing. The
   runtime advisory lock permits only one scheduler/polling process.
5. `observability.py` may read operational state but must never be required for a
   successful domain write or a valid backup archive.

New business workflows should first become a service function callable without
Telegram objects. Handlers should remain thin adapters around that function.
