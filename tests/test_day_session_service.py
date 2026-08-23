from __future__ import annotations

import datetime as dt
import plistlib
import shutil
from pathlib import Path
from typing import Literal

import pytest

import run_day_session_service as cli
from trading_agent.day_session_service import DaySessionServiceResult, run_day_session_service_tick
from trading_agent.day_session_service_config import (
    KR_DAY_SESSION_LABEL,
    US_DAY_SESSION_LABEL,
    KrDaySessionServiceConfig,
    UsDaySessionServiceConfig,
    load_day_session_service_config,
    verify_day_session_launch_agent,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def test_provision_writes_exact_bounded_launch_agents(tmp_path: Path) -> None:
    # Given: private config destinations for both market services.
    uv = Path(shutil.which("uv") or "/bin/false").resolve()
    us_config = tmp_path / "us.json"
    us_plist = tmp_path / "us.plist"
    kr_config = tmp_path / "kr.json"
    kr_plist = tmp_path / "kr.plist"

    # When: each service is provisioned through the operator CLI.
    us = cli.main(_provision("us", uv, us_config, us_plist, tmp_path))
    kr = cli.main(_provision("kr", uv, kr_config, kr_plist, tmp_path))

    # Then: RunAtLoad and the bounded 120-second cadence are exact and secret-free.
    assert us == kr == 0
    for config_path, plist_path, label in (
        (us_config, us_plist, US_DAY_SESSION_LABEL),
        (kr_config, kr_plist, KR_DAY_SESSION_LABEL),
    ):
        payload = plistlib.loads(plist_path.read_bytes())
        assert load_day_session_service_config(config_path).label == label
        assert payload["RunAtLoad"] is True
        assert payload["StartInterval"] == 120
        assert "KeepAlive" not in payload
        assert "EnvironmentVariables" not in payload
        assert verify_day_session_launch_agent(config_path, plist_path).ready


@pytest.mark.parametrize("market", ("us", "kr"))
def test_sunday_tick_is_service_success_without_child_or_state(
    market: Literal["us", "kr"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a configured service tick on Sunday in its local market.
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr("trading_agent.day_session_service._run_child", lambda command: calls.append(command))
    config = _config(market, tmp_path)

    # When: launchd invokes the tick.
    result = run_day_session_service_tick(
        config,
        clock=lambda: dt.datetime(2026, 8, 23, 3, 0, tzinfo=dt.UTC),
    )

    # Then: the service succeeds with explicit no-action before authority or input access.
    assert result == DaySessionServiceResult(market=market, status="no_action", reason="session_closed")
    assert calls == []
    assert not config.state_root.exists()


def test_missing_inputs_and_authority_mismatch_are_retryable_service_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an open-session US service with no current evidence, then a moved SHA.
    config = _config("us", tmp_path)
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)

    # When: the source roots are empty.
    missing = run_day_session_service_tick(
        config,
        clock=lambda: dt.datetime(2026, 8, 24, 15, 0, tzinfo=dt.UTC),
    )
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: "commit_mismatch")
    moved = run_day_session_service_tick(
        config,
        clock=lambda: dt.datetime(2026, 8, 24, 15, 0, tzinfo=dt.UTC),
    )

    # Then: both remain retryable launch-service successes with no silent work.
    assert missing.status == moved.status == "no_action"
    assert missing.reason == "source_missing"
    assert moved.reason == "commit_mismatch"


def test_child_provider_failure_is_preserved_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: complete US path bindings and a failing fixed Claude provider child.
    config = _config("us", tmp_path)
    for name in ("scanner.json", "articles.json", "news-evidence.json", "market-context.json"):
        (config.source_root / name).parent.mkdir(parents=True, exist_ok=True)
        (config.source_root / name).touch()
    for folder in ("quotes", "completed-ticks"):
        path = config.source_root / folder / "one.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)
    monkeypatch.setattr(
        "trading_agent.day_session_service._run_us",
        lambda *_: (2, "claude_decision_call_failed"),
    )

    # When: the service runs the current evidence composition.
    result = run_day_session_service_tick(
        config,
        clock=lambda: dt.datetime(2026, 8, 24, 15, 0, tzinfo=dt.UTC),
    )

    # Then: Claude remains the only provider and its blocker stays retryable.
    assert isinstance(config, UsDaySessionServiceConfig)
    assert config.live_model_provider == "claude-code"
    assert result.status == "no_action"
    assert result.reason == "claude_decision_call_failed"


def _config(
    market: Literal["us", "kr"],
    root: Path,
) -> UsDaySessionServiceConfig | KrDaySessionServiceConfig:
    common = {
        "project_root": ROOT,
        "expected_commit": SHA,
        "uv_path": Path(shutil.which("uv") or "/bin/false").resolve(),
        "source_root": root / "sources",
        "state_root": root / "state",
    }
    if market == "us":
        return UsDaySessionServiceConfig(**common)
    return KrDaySessionServiceConfig(**common)


def _provision(
    market: str,
    uv: Path,
    config: Path,
    plist: Path,
    root: Path,
) -> tuple[str, ...]:
    return (
        "provision",
        "--market",
        market,
        "--project-root",
        str(ROOT),
        "--expected-commit",
        SHA,
        "--uv-path",
        str(uv),
        "--source-root",
        str(root / "sources"),
        "--state-root",
        str(root / "state"),
        "--config",
        str(config),
        "--plist",
        str(plist),
    )
