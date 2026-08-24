# Security policy

Please do not open public issues containing tokens, Telegram session files,
database dumps, personal planner data or raw production logs.

Report a vulnerability privately to the repository owner through GitHub's
private vulnerability reporting. Include the affected revision, reproduction
steps and expected impact. Revoke any exposed credential before reporting it.

The primary production target is the macOS LaunchAgent described in
`docs/OPERATIONS.md`; Docker/VPS is a cloud-adapter target with a container E2E
release gate. Application and recovery database credentials are separate. The
CREATEDB-only recovery password belongs in macOS Keychain or the platform secret
manager and must never be copied to `.env`, plist, logs or command arguments.
