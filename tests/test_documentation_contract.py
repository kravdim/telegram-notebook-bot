from pathlib import Path

from scripts.check_documentation import active_documents, documentation_errors


def test_active_documentation_links_are_local_and_resolvable():
    assert documentation_errors() == []


def test_archive_is_not_presented_as_current_documentation():
    active = active_documents()
    assert active
    assert all("archive" not in Path(path).parts for path in active)
