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
- application logs: follow platform rotation and must not contain tokens or raw
  LLM payloads.

For a deletion request, export data if requested, delete the `users` row inside
a transaction (foreign keys cascade domain rows and embeddings), verify no rows
remain for the Telegram ID, and record only a non-personal audit timestamp.
Backups age out under the retention policy; do not selectively rewrite immutable
archives. Emergency deletion from backups requires destroying the affected
archives and immediately creating and drilling a fresh backup.

Secrets belong only in `.env` or the deployment secret store. CI scans tracked
files and full Git history; `*.session`, backups and credential files are ignored.
