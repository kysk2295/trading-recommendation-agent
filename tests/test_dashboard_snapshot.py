from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.daily_research_fixtures import write_complete_session
from trading_agent.dashboard_snapshot import (
    DashboardCredentialError,
    collect_dashboard_snapshot,
    load_dashboard_credentials,
)

SEOUL = ZoneInfo("Asia/Seoul")


def test_snapshot_uses_latest_non_future_session_and_only_public_fields(
    tmp_path: Path,
) -> None:
    outputs = tmp_path / "outputs"
    write_complete_session(outputs / "live_sessions" / "20260724")
    write_complete_session(
        outputs / "live_sessions" / "20260726",
        session_date=dt.date(2026, 7, 26),
    )
    snapshot = collect_dashboard_snapshot(
        outputs,
        now=dt.datetime(2026, 7, 25, 12, 0, tzinfo=SEOUL),
        jobs=(
            ("ai.trading-agent.kr-m3-20260725", 220, 0),
            ("ai.trading-agent.us-forward-open-handoff-20260725", None, 0),
        ),
    )

    payload = snapshot.model_dump(mode="json")

    assert snapshot.forward.session_date == dt.date(2026, 7, 24)
    assert snapshot.forward.eligible is True
    assert snapshot.forward.watch_cycles == 1
    assert snapshot.recommendations[0].symbol == "DEMO"
    assert {agent.agent_id for agent in snapshot.agents} == {"kr-theme", "us-intraday"}
    serialized = json.dumps(payload)
    for forbidden in (
        "account_fingerprint",
        "account_id",
        "credential",
        "api_key",
        "secret",
        "request_header",
        str(tmp_path),
    ):
        assert forbidden not in serialized.lower()


def test_snapshot_preserves_failed_cycles_as_blockers(tmp_path: Path) -> None:
    session = tmp_path / "outputs" / "live_sessions" / "20260724"
    write_complete_session(session)
    (session / "watch_cycles.csv").write_text(
        "started_at,exit_code,status\n2026-07-24T10:00:00-04:00,1,failed\n",
        encoding="utf-8",
    )

    snapshot = collect_dashboard_snapshot(
        tmp_path / "outputs",
        now=dt.datetime(2026, 7, 25, 12, 0, tzinfo=SEOUL),
    )

    assert snapshot.forward.eligible is False
    assert snapshot.forward.failed_watch_cycles == 1
    assert snapshot.forward.blockers == ("watch_cycle_failures:1",)


def test_dashboard_credentials_require_owner_mode_600_regular_file(
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "dashboard.env"
    credentials.write_text(
        "DASHBOARD_URL=https://example.test\n"
        "DASHBOARD_INGEST_TOKEN=ingest-token-with-adequate-length\n",
        encoding="utf-8",
    )
    credentials.chmod(0o644)

    with pytest.raises(DashboardCredentialError, match="mode_600"):
        load_dashboard_credentials(credentials)

    credentials.chmod(0o600)
    loaded = load_dashboard_credentials(credentials)

    assert loaded.dashboard_url == "https://example.test"
    assert loaded.ingest_token.get_secret_value() == "ingest-token-with-adequate-length"


def test_dashboard_credentials_reject_unknown_or_missing_settings(
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "dashboard.env"
    credentials.write_text(
        "DASHBOARD_URL=https://example.test\nACCOUNT_ID=must-not-cross-boundary\n",
        encoding="utf-8",
    )
    credentials.chmod(0o600)

    with pytest.raises(DashboardCredentialError, match="invalid_settings"):
        load_dashboard_credentials(credentials)
