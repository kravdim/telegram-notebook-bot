import hashlib
import json
import os
import time

import pytest
from sqlalchemy.engine import make_url

from scripts.restore_drill import (
    DrillResult,
    _append_report,
    _latest_verified_backup,
    _validate_operator,
    _verify_checksum,
)


def _backup(tmp_path, name="notebook_bot_2026-08-24_120000.sql.gz", data=b"dump"):
    path = tmp_path / name
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return path


def test_checksum_and_latest_backup_contract(tmp_path):
    older = _backup(tmp_path, "notebook_bot_2026-08-24_110000.sql.gz", b"old")
    latest = _backup(tmp_path, data=b"new")
    os.utime(older, (time.time() - 60, time.time() - 60))
    assert _latest_verified_backup(tmp_path, max_age_hours=1) == latest
    assert _verify_checksum(latest) == hashlib.sha256(b"new").hexdigest()


def test_stale_or_corrupt_backup_fails_closed(tmp_path):
    backup = _backup(tmp_path)
    old = time.time() - 7200
    os.utime(backup, (old, old))
    with pytest.raises(ValueError, match="stale"):
        _latest_verified_backup(tmp_path, max_age_hours=1)
    backup.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        _verify_checksum(backup)


def test_operator_must_be_createdb_but_not_privileged(monkeypatch):
    class Result:
        stdout = "1|0|0|0|1\n"

    monkeypatch.setattr("scripts.restore_drill._run", lambda *args, **kwargs: Result())
    _validate_operator(make_url("postgresql://operator:secret@localhost/postgres"), {})

    class DangerousResult:
        stdout = "1|1|1|0|1\n"

    monkeypatch.setattr("scripts.restore_drill._run", lambda *args, **kwargs: DangerousResult())
    with pytest.raises(ValueError, match="NOSUPERUSER"):
        _validate_operator(make_url("postgresql://operator:secret@localhost/postgres"), {})


def test_report_is_jsonl_without_connection_credentials(tmp_path):
    report = tmp_path / "drills.jsonl"
    result = DrillResult(
        backup="backup.sql.gz",
        backup_bytes=100,
        backup_sha256="a" * 64,
        migration="head",
        public_tables=20,
        users=2,
        tasks=3,
        delivery_batches=1,
        rto_seconds=1.25,
        completed_at="2026-08-24T12:00:00+00:00",
    )
    _append_report(report, result)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["rto_seconds"] == 1.25
    assert "url" not in payload
