"""Verify prepared source against Git objects before executing release code."""

import asyncio
import hashlib
import os
import re
import signal
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from bot.operations.maintenance import MaintenanceError


async def run(command: list[str], cwd: Path, env: dict[str, str], timeout: float = 120) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *command, cwd=cwd, env=env, start_new_session=True,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        raise
    if process.returncode:
        raise MaintenanceError(f"Maintenance validation/command failed (exit {process.returncode})")
    return output


def digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _git_blob(path: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)  # Git object identity, not a security signature.
    digest.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_files(directory: Path, entries: bytes) -> None:
    tracked = set()
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, name_bytes = entry.split(b"\t", 1)
        mode, kind, expected = metadata.decode().split()
        name = name_bytes.decode()
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise MaintenanceError("Invalid release tree path")
        path = directory / name
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise MaintenanceError("Release symlinks/submodules require explicit review")
        if path.is_symlink() or not path.is_file() or _git_blob(path) != expected:
            raise MaintenanceError("Prepared release differs from exact Git revision")
        if bool(path.stat().st_mode & 0o111) != (mode == "100755"):
            raise MaintenanceError("Prepared release executable mode differs from Git")
        tracked.add(name)
    for parent, directories, files in os.walk(directory):
        relative = Path(parent).relative_to(directory)
        for name in list(directories):
            path = Path(parent) / name
            if path.is_symlink():
                raise MaintenanceError("Unexpected symlink directory in release")
            if (relative == Path(".") and name == ".venv") or name == "__pycache__":
                directories.remove(name)
        for name in files:
            relative_name = str(relative / name)
            if relative_name in {".env", "config.yaml", ".dailyplanner-release-ready"}:
                continue
            if relative_name not in tracked:
                raise MaintenanceError("Unexpected file in prepared release")


@dataclass(frozen=True)
class Release:
    repository: Path
    directory: Path
    sha: str

    async def verify(self, env: dict[str, str]) -> dict[str, str]:
        if (not re.fullmatch(r"[0-9a-f]{40}", self.sha) or self.directory.name != self.sha
                or not self.directory.is_absolute() or self.directory.resolve() != self.directory):
            raise MaintenanceError("An exact SHA and resolved versioned release path are required")
        commit = await run(["git", "--no-replace-objects", "rev-parse", f"{self.sha}^{{commit}}"],
                           self.repository, env)
        if commit.decode().strip() != self.sha:
            raise MaintenanceError("Release SHA is not an exact commit")
        tree = await run(["git", "--no-replace-objects", "ls-tree", "-rz", self.sha], self.repository, env)
        _check_files(self.directory, tree)
        for name in (".env", "config.yaml"):
            path = self.directory / name
            if not path.is_file() or path.stat().st_mode & 0o022:
                raise MaintenanceError("Release configuration is missing or writable by others")
            if name == ".env" and path.stat().st_mode & 0o077:
                raise MaintenanceError("Release dotenv must be private (0600)")
        await run(["uv", "sync", "--check", "--frozen", "--offline", "--no-dev", "--extra", "stt"],
                  self.directory, env)
        python = self.directory / ".venv/bin/python"
        return {"source": hashlib.sha256(tree).hexdigest(),
                "config": digest_file(self.directory / "config.yaml"),
                "dotenv": digest_file(self.directory / ".env"),
                "lock": digest_file(self.directory / "uv.lock"),
                "python": digest_file(python)}

    async def command(self, arguments: list[str], env: dict[str, str]) -> None:
        await run([str(self.directory / ".venv/bin/python"), *arguments], self.directory,
                  {**env, "PYTHONPATH": str(self.directory), "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONPYCACHEPREFIX": str(Path(tempfile.gettempdir()) / uuid.uuid4().hex)})
