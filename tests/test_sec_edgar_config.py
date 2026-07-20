from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.sec_edgar_config import (
    DEFAULT_SEC_USER_AGENT_PATH,
    InvalidSecUserAgentError,
    SecUserAgentFileError,
    create_sec_edgar_http_client,
    load_sec_user_agent,
)

USER_AGENT = "TradingResearchOS research@example.com"


def test_sec_user_agent_is_loaded_from_exact_private_setting(tmp_path: Path) -> None:
    path = _setting(tmp_path, f"SEC_USER_AGENT={USER_AGENT}\n")

    setting = load_sec_user_agent(path)

    assert Path.home() / ".config/trading-agent/sec.env" == DEFAULT_SEC_USER_AGENT_PATH
    assert setting.value == USER_AGENT
    assert USER_AGENT not in repr(setting)


@pytest.mark.parametrize("mode", (0o400, 0o640, 0o644))
def test_sec_user_agent_rejects_non_private_mode(tmp_path: Path, mode: int) -> None:
    path = _setting(tmp_path, f"SEC_USER_AGENT={USER_AGENT}\n", mode=mode)

    with pytest.raises(SecUserAgentFileError):
        _ = load_sec_user_agent(path)


def test_sec_user_agent_rejects_symlink(tmp_path: Path) -> None:
    target = _setting(tmp_path, f"SEC_USER_AGENT={USER_AGENT}\n")
    link = tmp_path / "sec-link.env"
    link.symlink_to(target)

    with pytest.raises(SecUserAgentFileError):
        _ = load_sec_user_agent(link)


@pytest.mark.parametrize(
    "contents",
    (
        "",
        "SEC_USER_AGENT=anonymous\n",
        "OTHER=TradingResearchOS research@example.com\n",
        "SEC_USER_AGENT=TradingResearchOS research@example.com\nOTHER=value\n",
    ),
)
def test_sec_user_agent_requires_declared_application_and_contact(tmp_path: Path, contents: str) -> None:
    path = _setting(tmp_path, contents)

    with pytest.raises(InvalidSecUserAgentError):
        _ = load_sec_user_agent(path)


def test_sec_http_client_uses_exact_origin_without_redirects() -> None:
    with create_sec_edgar_http_client() as client:
        assert str(client.base_url).rstrip("/") == "https://data.sec.gov"
        assert client.follow_redirects is False


def _setting(tmp_path: Path, contents: str, *, mode: int = 0o600) -> Path:
    path = tmp_path / "sec.env"
    path.write_text(contents, encoding="utf-8")
    path.chmod(mode)
    return path
