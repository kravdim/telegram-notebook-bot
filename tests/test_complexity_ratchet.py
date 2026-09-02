import pytest

import scripts.check_complexity_ratchet as ratchet


def _declared_from_baseline():
    return {key: set(metrics) for key, metrics in ratchet.BASELINES.items()}


def _measured_from_baseline():
    return {key: dict(metrics) for key, metrics in ratchet.BASELINES.items()}


def test_numeric_complexity_ratchet_accepts_unchanged_baseline(monkeypatch, capsys):
    monkeypatch.setattr(
        ratchet, "_declared_exceptions", lambda: (_declared_from_baseline(), [])
    )
    monkeypatch.setattr(ratchet, "_ruff_measurements", _measured_from_baseline)

    ratchet.main()

    assert "16 numeric metrics" in capsys.readouterr().out


def test_numeric_complexity_ratchet_rejects_metric_growth(monkeypatch):
    measurements = _measured_from_baseline()
    key = ("bot/config.py", "validate_runtime_config")
    measurements[key]["C901"] += 1
    monkeypatch.setattr(
        ratchet, "_declared_exceptions", lambda: (_declared_from_baseline(), [])
    )
    monkeypatch.setattr(ratchet, "_ruff_measurements", lambda: measurements)

    with pytest.raises(SystemExit, match=r"C901 increased: 33 > 32"):
        ratchet.main()
