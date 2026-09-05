#!/usr/bin/env python3
"""Validate links and ownership of the small active documentation set."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
ROOT_DOCS = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md"]
PRODUCT_DOCS = ["README.md", "ARCHITECTURE.md", "OPERATIONS.md", "PRIVACY.md", "THREAT_MODEL.md"]


def active_documents(root: Path = ROOT) -> list[Path]:
    """Return stable product docs plus the newest review and release handoff."""
    documents = [root / name for name in ROOT_DOCS]
    documents.extend(root / "docs" / name for name in PRODUCT_DOCS)
    for pattern in ("REVIEW_*_INDEPENDENT.md", "SESSION_CONTEXT_*.md"):
        candidates = sorted((root / "docs").glob(pattern))
        if candidates:
            documents.append(candidates[-1])
    return documents


def documentation_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for document in active_documents(root):
        if not document.is_file():
            errors.append(f"missing active document: {document.relative_to(root)}")
            continue
        text = document.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path_text = unquote(target.split("#", 1)[0])
            if not path_text:
                continue
            if path_text.startswith("/"):
                errors.append(
                    f"{document.relative_to(root)}: host-specific absolute link {target}"
                )
                continue
            resolved = (document.parent / path_text).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{document.relative_to(root)}: link escapes repository {target}")
                continue
            if not resolved.exists():
                errors.append(f"{document.relative_to(root)}: missing link target {target}")
    return errors


def main() -> None:
    errors = documentation_errors()
    if errors:
        raise SystemExit("documentation contract failed:\n" + "\n".join(errors))
    print(f"documentation contract ok: {len(active_documents())} active files")


if __name__ == "__main__":
    main()
