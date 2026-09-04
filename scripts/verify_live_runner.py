#!/usr/bin/env python3
"""Fail closed on changed external corpus/dependencies, without importing the runner."""

import argparse
import ast
import hashlib
import json
import subprocess
from pathlib import Path


def verify_runner(directory: Path, lock: dict) -> int:
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=directory, text=True,
    ).strip()
    if commit != lock["repository_commit"]:
        raise ValueError("External runner commit differs from reviewed lock")
    for relative, digest in lock["files"].items():
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Unsafe runner lock path")
        if hashlib.sha256((directory / relative).read_bytes()).hexdigest() != digest:
            raise ValueError(f"External runner file differs from reviewed lock: {relative}")
    tree = ast.parse((directory / "tests_dailyplanner/run_messy_human.py").read_text())
    ids = [node.args[0].value for node in ast.walk(tree)
           if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
           and node.func.id == "Case" and node.args and isinstance(node.args[0], ast.Constant)]
    if len(ids) != len(set(ids)) or len(ids) != lock["expected_cases"]:
        raise ValueError("External runner corpus count or IDs changed")
    return len(ids)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--userbot-dir", type=Path, required=True)
    args = parser.parse_args()
    lock_path = Path(__file__).resolve().parents[1] / "tests/live/runner-lock.json"
    print(verify_runner(args.userbot_dir, json.loads(lock_path.read_text())))


if __name__ == "__main__":
    main()
