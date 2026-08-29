import importlib.util
import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "platform/macos/com.notebook-bot.plist"
SPEC = importlib.util.spec_from_file_location(
    "dailyplanner_macos_launchagent",
    ROOT / "platform/macos/render_launchagent.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
render_launchagent = MODULE.render_launchagent
validate_proxy_url = MODULE.validate_proxy_url


def _render(tmp_path: Path, **kwargs):
    output = tmp_path / "Library/LaunchAgents/com.notebook-bot.plist"
    render_launchagent(
        TEMPLATE,
        output,
        tmp_path / "project",
        tmp_path / "home",
        **kwargs,
    )
    with output.open("rb") as source:
        return plistlib.load(source)


def test_direct_profile_is_default_and_contains_no_site_proxy_or_secret(tmp_path):
    payload = _render(tmp_path)
    environment = payload["EnvironmentVariables"]
    assert {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}.isdisjoint(environment)
    assert {"BOT_TOKEN", "MINIMAX_API_KEY", "DATABASE_URL"}.isdisjoint(environment)
    assert "127.0.0.1:108" not in TEMPLATE.read_text(encoding="utf-8")


def test_explicit_proxy_profile_renders_only_requested_endpoints(tmp_path):
    payload = _render(
        tmp_path,
        http_proxy="http://proxy.example:8080",
        all_proxy="socks5://socks.example:1080",
    )
    environment = payload["EnvironmentVariables"]
    assert environment["HTTP_PROXY"] == "http://proxy.example:8080"
    assert environment["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert environment["ALL_PROXY"] == "socks5://socks.example:1080"


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("http://user:password@proxy.example:8080", "http"),
        ("ftp://proxy.example:21", "http"),
        ("socks5://proxy.example", "all"),
        ("http://proxy.example:8080?token=secret", "http"),
    ],
)
def test_proxy_validation_rejects_unsafe_or_incomplete_urls(value, kind):
    with pytest.raises(ValueError):
        validate_proxy_url(value, kind)
