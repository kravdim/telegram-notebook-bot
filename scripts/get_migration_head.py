#!/usr/bin/env python3
"""Print the single Alembic head for a staged release directory."""

import argparse
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    project = parser.parse_args().project.resolve()
    migrations = ScriptDirectory.from_config(Config(str(project / "alembic.ini")))
    heads = migrations.get_heads()
    if len(heads) != 1:
        raise SystemExit(f"Expected one Alembic head, found {heads!r}")
    print(heads[0])


if __name__ == "__main__":
    main()
