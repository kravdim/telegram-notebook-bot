import scripts.check_complexity_ratchet as ratchet


def test_complexity_ratchet_accepts_zero_exceptions(monkeypatch, capsys):
    monkeypatch.setattr(ratchet, "_declared_exceptions", lambda: ({}, []))

    ratchet.main()

    assert "zero complexity exceptions" in capsys.readouterr().out


def test_complexity_ratchet_rejects_new_exception(monkeypatch):
    import pytest

    declared = {("bot/config.py", "validate_runtime_config"): {"C901"}}
    monkeypatch.setattr(ratchet, "_declared_exceptions", lambda: (declared, []))

    with pytest.raises(SystemExit, match=r"validate_runtime_config: new complexity exception"):
        ratchet.main()
