#!/usr/bin/env python3
"""Small local guard; CI additionally runs gitleaks over full Git history."""

import re
import subprocess
from pathlib import Path

PATTERNS = {
    "Telegram bot token": re.compile(rb"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    "OpenAI-style API key": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def main() -> None:
    files = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    findings: list[str] = []
    scanned = 0
    for filename in files:
        path = Path(filename)
        if not path.is_file():
            continue
        data = path.read_bytes()
        scanned += 1
        for label, pattern in PATTERNS.items():
            if pattern.search(data):
                findings.append(f"{filename}: {label}")
    if findings:
        raise SystemExit("Potential secrets found:\n" + "\n".join(findings))
    print(f"secret scan ok: {scanned} present tracked files")


if __name__ == "__main__":
    main()
