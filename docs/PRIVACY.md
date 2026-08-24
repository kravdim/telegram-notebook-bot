# Data retention and deletion

DailyPlanner stores task, project, reminder, diary, memoir, time-tracking,
birthday and interaction data in PostgreSQL. Embeddings are derived from user
text and are personal data even when they are not directly readable.

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
if the runtime still holds its PostgreSQL singleton lease. The deletion process
holds that same lease to prevent a restart during the operation. It first
removes access from the whitelist using an atomic YAML replacement, deletes the
user, cascaded domain rows, LLM payloads and FSM state inside one database
transaction, then re-counts every user-owned table before commit. Output
contains only the ID, row counts, verification result and timestamp—not stored
content. If verification fails, the database transaction rolls back while
access stays closed for a safe retry.
Backups age out under the retention policy; do not selectively rewrite immutable
archives. Emergency deletion from backups requires destroying the affected
archives and immediately creating and drilling a fresh backup.

Secrets belong only in `.env` or the deployment secret store. CI scans tracked
files and full Git history; `*.session`, backups and credential files are ignored.
