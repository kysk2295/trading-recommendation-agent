from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.daily_research_fixtures import write_complete_session
from trading_agent.dashboard_snapshot import (
    DashboardCredentialError,
    collect_dashboard_snapshot,
    load_dashboard_credentials,
)
from trading_agent.lane_contract_keys import (
    experiment_scope_key,
    lane_manifest_key,
)
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    INTRADAY_MANIFEST,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryStore

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


def test_snapshot_projects_latest_verified_account_pnl_without_identity(
    tmp_path: Path,
) -> None:
    outputs = tmp_path / "outputs"
    registry = LaneRegistryStore(outputs / "lane_control" / "lane_registry.sqlite3")
    scope = CURRENT_INTRADAY_EXPERIMENT_SCOPES[0]
    daily = LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=dt.date(2026, 7, 24),
        finalized_at=dt.datetime(2026, 7, 24, 20, 5, tzinfo=dt.UTC),
        manifest_key=lane_manifest_key(INTRADAY_MANIFEST),
        experiment_scope_keys=(experiment_scope_key(scope),),
        source_ledger_generation=42,
        source_ledger_sha256="a" * 64,
        champion_strategy_versions=(),
        data_quality_complete=True,
        allocation_eligible=False,
        incidents=(),
        conservative_equity=Decimal("100125.25"),
        realized_pnl=Decimal("125.25"),
        unrealized_pnl=Decimal("-20.50"),
        planned_open_risk=Decimal("0"),
        open_order_count=0,
        open_position_count=0,
    )
    with registry.writer() as writer:
        _ = writer.register_manifest(INTRADAY_MANIFEST)
        _ = writer.register_experiment_scope(scope)
        assert writer.append_daily_snapshot(daily) is True

    snapshot = collect_dashboard_snapshot(
        outputs,
        now=dt.datetime(2026, 7, 25, 12, 0, tzinfo=SEOUL),
    )
    payload = snapshot.model_dump(mode="json")

    assert snapshot.account.status == "verified"
    assert snapshot.account.session_date == dt.date(2026, 7, 24)
    assert snapshot.account.equity == Decimal("100125.25")
    assert snapshot.account.daily_pnl == Decimal("104.75")
    assert snapshot.account.realized_pnl == Decimal("125.25")
    assert snapshot.account.unrealized_pnl == Decimal("-20.50")
    assert snapshot.account.open_positions == 0
    serialized = json.dumps(payload)
    assert "account_fingerprint" not in serialized
    assert "account_id" not in serialized


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
