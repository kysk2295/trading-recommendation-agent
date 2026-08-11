from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_kr_theme_day_trial_terminal import _request, _trial_stores
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_models import (
    HermesDeliveryEvent,
    HermesDeliveryKind,
    build_hermes_delivery_event,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_same_cycle_delivery import (
    KrSameCycleDeliveryRequest,
    project_kr_same_cycle_delivery,
)
from trading_agent.kr_theme_day_terminal_delivery import (
    KrThemeDayTerminalDeliverySources,
    project_kr_theme_day_terminal_delivery,
)
from trading_agent.kr_theme_day_trial_terminal import (
    finalize_kr_theme_day_shadow_trial,
)

NOW = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)


def test_dashboard_projects_us_and_kr_hermes_session_terminals(tmp_path: Path) -> None:
    # Given: canonical Hermes terminals for one US recommendation session and one KR no-recommendation session
    outputs = tmp_path / "outputs"
    store = HermesDeliveryStore(outputs / "hermes" / "delivery.sqlite3")
    with store.writer() as writer:
        _ = writer.append_event(
            _terminal(
                source_event_id="us-session-terminal-" + "a" * 64,
                kind=HermesDeliveryKind.DAILY_SUMMARY,
                market_id="us_equities",
                occurred_at=dt.datetime(2026, 7, 31, 20, tzinfo=dt.UTC),
                status="session_summary",
            )
        )
    kr_root = tmp_path / "kr-terminal-producer"
    kr_root.mkdir()
    stores, trial_id = _trial_stores(kr_root, with_entry=False)
    sources = KrThemeDayTerminalDeliverySources(
        entry_store=stores.entry_store,
        exit_store=stores.exit_store,
        terminal_store=stores.terminal_store,
        delivery_store=store,
    )
    _ = finalize_kr_theme_day_shadow_trial(
        ExperimentLedgerStore(kr_root / "experiment.sqlite3"),
        stores,
        _request(trial_id),
    )
    projected = project_kr_theme_day_terminal_delivery(sources, trial_id)
    assert projected.inserted == 1
    store.path.parent.chmod(0o700)

    # When: the real Dashboard v2 snapshot boundary reads local outputs
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then: both immutable terminal outcomes are visible without rendering raw Hermes text
    terminals = {
        item.item_id: item for item in snapshot.workspaces.markets.items if item.item_id.startswith("session_terminal.")
    }
    assert terminals["session_terminal.us_equities.20260731"].value == "recommendation"
    assert terminals["session_terminal.kr_equities.20260720"].value == "no_recommendation"
    assert all("secret terminal detail" not in (item.value or "") for item in terminals.values())
    assert sum(node.kind == "reviewer_decision" for node in snapshot.traces.nodes) >= 2


def test_dashboard_does_not_treat_intraday_kr_no_opportunity_as_terminal(
    tmp_path: Path,
) -> None:
    outputs = tmp_path / "outputs"
    store = HermesDeliveryStore(outputs / "hermes" / "delivery.sqlite3")
    projected = project_kr_same_cycle_delivery(
        store,
        KrSameCycleDeliveryRequest(
            collection_cycle_id="kr-cycle-20260731",
            strategy_version="kr-theme-day-v1",
            occurred_at=dt.datetime(2026, 7, 31, 6, 30, tzinfo=dt.UTC),
            opportunities=(),
        ),
    )
    assert projected.inserted == 1
    store.path.parent.chmod(0o700)

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    terminals = tuple(
        item for item in snapshot.workspaces.markets.items if item.item_id == "session_terminal.kr_equities.20260731"
    )
    assert terminals == ()


def test_dashboard_fails_closed_on_hermes_session_incident_and_replays_once(tmp_path: Path) -> None:
    # Given: one exactly-once pre-terminal KR incident replayed through the append-only store
    outputs = tmp_path / "outputs"
    store = HermesDeliveryStore(outputs / "hermes" / "delivery.sqlite3")
    event = _terminal(
        source_event_id="kr-source-preflight-incident-" + "c" * 64,
        kind=HermesDeliveryKind.INCIDENT,
        market_id="kr_equities",
        occurred_at=dt.datetime(2026, 7, 31, 1, tzinfo=dt.UTC),
        status="blocked_source_preflight",
    )
    with store.writer() as writer:
        assert writer.append_event(event).inserted is True
        assert writer.append_event(event).inserted is False
    store.path.parent.chmod(0o700)

    # When: the snapshot is collected after the replay
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then: one blocked terminal is projected and the markets workspace cannot claim success
    incidents = tuple(
        item for item in snapshot.workspaces.markets.items if item.item_id == "session_terminal.kr_equities.20260731"
    )
    assert len(incidents) == 1
    assert incidents[0].state == "blocked"
    assert incidents[0].value == "incident"
    assert snapshot.workspaces.markets.state == "blocked"
    assert any(
        node.kind == "blocker_terminal" and node.safe_ref == event.payload_sha256 for node in snapshot.traces.nodes
    )


def test_dashboard_aggregates_verified_kr_exits_as_one_recommendation_terminal(tmp_path: Path) -> None:
    # Given: one completed KR session with two independently delivered shadow exits
    outputs = tmp_path / "outputs"
    store = HermesDeliveryStore(outputs / "hermes" / "delivery.sqlite3")
    with store.writer() as writer:
        for suffix, minute in (("d", 20), ("e", 30)):
            _ = writer.append_event(
                _terminal(
                    source_event_id=f"kr-exit:{suffix * 64}",
                    kind=HermesDeliveryKind.EXIT,
                    market_id="kr_equities",
                    occurred_at=dt.datetime(2026, 7, 30, 6, minute, tzinfo=dt.UTC),
                    status="session_close",
                )
            )
    store.path.parent.chmod(0o700)

    # When: Dashboard projects the append-only Hermes session history
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then: the completed session is one recommendation terminal rather than a duplicate conflict
    terminals = tuple(
        item for item in snapshot.workspaces.markets.items if item.item_id == "session_terminal.kr_equities.20260730"
    )
    assert len(terminals) == 1
    assert terminals[0].state == "populated"
    assert terminals[0].value == "recommendation"


def _terminal(
    *,
    source_event_id: str,
    kind: HermesDeliveryKind,
    market_id: str,
    occurred_at: dt.datetime,
    status: str,
) -> HermesDeliveryEvent:
    return build_hermes_delivery_event(
        kind=kind,
        source_event_id=source_event_id,
        market_id=market_id,
        lane_id=None,
        occurred_at=occurred_at,
        payload_sha256=source_event_id[-64:],
        rendered_text="secret terminal detail",
        status=status,
    )
