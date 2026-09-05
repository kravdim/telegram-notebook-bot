#!/usr/bin/env python3
"""Plan by default; explicit fingerprint confirmation is required for downtime."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from bot.operations.maintenance import MaintenanceError, MaintenanceJournal, deploy, recover
from bot.operations.maintenance_deploy import MacMaintenance, confirmation
from bot.operations.maintenance_postgres import MaintenancePostgres
from bot.operations.maintenance_release import Release


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repository", type=Path, required=True)
    result.add_argument("--release-root", type=Path, required=True)
    result.add_argument("--previous", required=True, help="Exact previous commit SHA (40 hex)")
    result.add_argument("--candidate", required=True, help="Exact candidate commit SHA (40 hex)")
    result.add_argument("--plist", type=Path, required=True)
    result.add_argument("--state-dir", type=Path, required=True, help="Existing installer's state directory")
    result.add_argument("--recover", action="store_true")
    result.add_argument("--execute", action="store_true", help="Authorize the planned downtime workflow")
    result.add_argument("--confirm", help="Exact MAINTENANCE-... identifier printed by the plan")
    return result


async def execute(args: argparse.Namespace) -> dict:
    if args.execute and (sys.platform != "darwin" or not args.confirm):
        raise MaintenanceError("Execution requires macOS and explicit plan confirmation")
    if not args.execute and args.confirm:
        raise MaintenanceError("Confirmation requires --execute; omit both for a read-only plan")
    # Credentials are deliberately not accepted as command-line arguments.
    source, operator = os.environ["DATABASE_URL"], os.environ["OPERATOR_DATABASE_URL"]
    root = args.release_root.resolve()
    repository = args.repository.resolve()
    state = args.state_dir.resolve()
    journal = MaintenanceJournal(state / "maintenance.json")
    postgres = MaintenancePostgres(source, operator, state / "maintenance-backups")
    port = MacMaintenance(Release(repository, root / args.previous, args.previous),
                          Release(repository, root / args.candidate, args.candidate),
                          postgres, args.plist.absolute(), journal)
    try:
        record = await port.validate()
        token = confirmation({**record, "operation": "recover" if args.recover else "deploy"})
        if not args.execute:
            return {"status": "plan_only", "confirmation": token, "identity": record,
                    "journal_exists": journal.path.exists(), "production_changed": False}
        if args.confirm != token:
            raise MaintenanceError("Plan confirmation does not match current release/configuration identity")
        status = await (recover(port, journal) if args.recover else deploy(port, journal))
        return {"status": status, "journal": str(journal.path)}
    finally:
        await port.close()


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(execute(args))
    except (Exception, KeyboardInterrupt) as error:
        # Never print a raw SQLAlchemy/OS exception or a connection string.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, MaintenanceError) else "details withheld",
                          "guidance": "Do not clear locks or restore snapshots automatically; "
                          "inspect the private maintenance journal and reconcile state."}))
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["status"] != "restored_previous" else 2


if __name__ == "__main__":
    raise SystemExit(main())
