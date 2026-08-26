# Data retention and deletion

DailyPlanner stores task, project, reminder, diary, memoir, time-tracking,
birthday and interaction data in PostgreSQL. Embeddings are derived from user
text and are personal data even when they are not directly readable.

`/export` creates an Obsidian-compatible ZIP in a private temporary directory,
writes Markdown incrementally, rejects content or archives above the configured
`export.max_bytes`, and removes the temporary directory after success or error.
The bot does not retain export archives.

Default retention:

- domain data: until the user or operator deletes the account/data;
- transient interaction and idempotency state: operational only, periodically
  eligible for cleanup after 30 days;
- LLM logs: 90 days; prompt and response bodies are disabled by default with
  `privacy.store_llm_payloads: false`;
- backups: 30 days; checksum sidecars have the same lifecycle;
- application logs: rotate at 10 MiB with seven local generations and must not
  contain tokens or raw LLM payloads.

For a deletion request, export data if requested, then use the dry-run-first
operator workflow:

```bash
uv run python scripts/delete_user_data.py TELEGRAM_ID
uv run python scripts/delete_user_data.py TELEGRAM_ID \
  --execute --confirm DELETE-TELEGRAM_ID
```

Stop the bot before the execute form. It is bound to the exact target ID,
refuses administrator accounts and `ALLOW_ALL_USERS`, and refuses to continue
when an environment whitelist differs from the persisted YAML whitelist. It
also refuses to continue
if the runtime still holds its PostgreSQL singleton lease. The deletion process
holds that same lease to prevent a restart during the operation. It first
persists a PostgreSQL operation journal, removes access from the whitelist
using an atomic YAML replacement, deletes the
user, cascaded domain rows, LLM payloads and FSM state inside one database
transaction, then re-counts every user-owned table before commit. Output
contains only the ID, row counts, verification result and timestamp—not stored
content. If deletion or verification fails, the database transaction rolls
back and the original whitelist is atomically restored. If that compensating
write also fails, the command exits with an explicit dual-failure error for
operator recovery. A process crash or power loss between YAML and PostgreSQL is
reconciled by repeating the exact same confirmed command: phases `prepared`,
`access_revoked` and `completed` are idempotent, and an already completed
operation returns its recorded content-free result.
Completion is scoped to one operation generation. A later legal re-onboarding
of the same Telegram ID is detected from fresh row counts and the current
access list, and starts a new UUID-bound deletion operation. The command
returns `already-completed` only after a new zero/access check.
Backups age out under the retention policy; do not selectively rewrite immutable
archives. Emergency deletion from backups requires destroying the affected
archives and immediately creating and drilling a fresh backup.

Secrets belong only in `.env` or the deployment secret store. CI scans tracked
files and full Git history; `*.session`, backups and credential files are ignored.
