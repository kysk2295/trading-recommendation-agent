from __future__ import annotations

import datetime as dt
import plistlib
import shutil
import stat
from dataclasses import replace
from pathlib import Path

import pytest

import run_kr_strategy_research_live_cycle as live_cycle
import run_kr_strategy_research_service as service
from tests.test_kis_kr_session_calendar import _receipt
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_strategy_research_service_config import (
    KR_STRATEGY_RESEARCH_SERVICE_LABEL,
    load_kr_strategy_research_service_config,
    verify_kr_strategy_research_launch_agent,
)

ROOT = Path(__file__).resolve().parents[1]
KST = dt.timezone(dt.timedelta(hours=9))


def test_provision_writes_private_secret_free_two_minute_launch_agent(tmp_path: Path) -> None:
    config_path = tmp_path / "private" / "service.json"
    plist_path = tmp_path / "private" / "service.plist"

    result = service.main(_provision_args(tmp_path, config_path, plist_path))

    config = load_kr_strategy_research_service_config(config_path)
    payload = plistlib.loads(plist_path.read_bytes())
    assert result == 0
    assert config.label == KR_STRATEGY_RESEARCH_SERVICE_LABEL
    assert payload["StartInterval"] == 120
    assert payload["RunAtLoad"] is True
    assert "KeepAlive" not in payload
    assert "EnvironmentVariables" not in payload
    assert payload["ProgramArguments"][-3:] == [
        "tick",
        "--config",
        str(config_path),
    ]
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600
    assert verify_kr_strategy_research_launch_agent(config_path, plist_path).ready


def test_closed_session_exits_before_any_market_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed = dt.datetime(2026, 7, 20, 15, 31, tzinfo=KST)
    receipt = replace(_receipt(), received_at=observed.replace(hour=8, minute=50))
    calendar = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    assert calendar.append(receipt, project_kis_kr_session_calendar(receipt))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        live_cycle.run_kr_same_cycle_opportunity,
        "main",
        lambda argv, **_: calls.append(tuple(argv)) or 0,
    )

    result = live_cycle.main(_cycle_args(tmp_path, calendar.path), clock=lambda: observed)

    assert result == 0
    assert calls == []
    assert not (tmp_path / "cycles").exists()


def test_service_help_and_bad_config_fail_without_market_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        live_cycle,
        "main",
        lambda argv, **_: calls.append(tuple(argv)) or 0,
    )

    assert service.main(("--help",)) == 0
    assert service.main(("tick", "--config", str(tmp_path / "missing.json"))) == 2
    assert calls == []


def _provision_args(tmp_path: Path, config_path: Path, plist_path: Path) -> tuple[str, ...]:
    uv = Path(shutil.which("uv") or "/bin/false").resolve()
    return (
        "provision",
        "--project-root",
        str(ROOT),
        "--uv-path",
        str(uv),
        "--policy",
        str(tmp_path / "policy.json"),
        "--database",
        str(tmp_path / "research.sqlite3"),
        "--experiment-ledger",
        str(tmp_path / "ledger.sqlite3"),
        "--delivery-database",
        str(tmp_path / "delivery.sqlite3"),
        "--calendar-store",
        str(tmp_path / "calendar.sqlite3"),
        "--cycle-root",
        str(tmp_path / "cycles"),
        "--live-session-root",
        str(tmp_path / "live"),
        "--market-context-root",
        str(tmp_path / "context"),
        "--runtime-output-root",
        str(tmp_path / "reports"),
        "--config",
        str(config_path),
        "--plist",
        str(plist_path),
    )


def _cycle_args(tmp_path: Path, calendar: Path) -> tuple[str, ...]:
    return (
        "--policy",
        str(tmp_path / "policy.json"),
        "--database",
        str(tmp_path / "research.sqlite3"),
        "--experiment-ledger",
        str(tmp_path / "ledger.sqlite3"),
        "--delivery-database",
        str(tmp_path / "delivery.sqlite3"),
        "--calendar-store",
        str(calendar),
        "--cycle-root",
        str(tmp_path / "cycles"),
        "--live-session-root",
        str(tmp_path / "live"),
        "--market-context-root",
        str(tmp_path / "context"),
    )
