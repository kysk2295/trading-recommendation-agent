from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_strategy_lab_research_kernel import _bundle
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.strategy_lab_daily_report import (
    StrategyLabDailyState,
    project_strategy_lab_daily_report,
)
from trading_agent.strategy_lab_kernel import StrategyLabFleet
from trading_agent.strategy_lab_models import STRATEGY_LAB_IDS, EvidenceMode
from trading_agent.us_equity_calendar import NEW_YORK


def test_report_waits_until_fifteen_minutes_after_completed_session_close(tmp_path: Path) -> None:
    # Given a complete immutable six-lab cycle and a pre-cutoff XNYS timestamp.
    ledger = _complete_ledger(tmp_path)
    deliveries = HermesDeliveryStore(tmp_path / "deliveries.sqlite3")
    before_cutoff = dt.datetime(2026, 8, 17, 16, 14, tzinfo=NEW_YORK)

    # When the daily projection runs before the required post-close delay.
    with deliveries.writer() as writer:
        result = project_strategy_lab_daily_report(ExperimentLedgerReader(ledger.path), writer, now=before_cutoff)

    # Then no outbound event is inserted.
    assert result.examined == result.inserted == result.replayed == 0
    assert HermesDeliveryReader(deliveries.path).events() == ()


def test_report_projects_waiting_evidence_state_when_no_cycle_exists(tmp_path: Path) -> None:
    # Given no completed cycle and a daemon that is waiting for verified evidence after close.
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    with ledger.writer():
        pass
    deliveries = HermesDeliveryStore(tmp_path / "deliveries.sqlite3")
    after_cutoff = dt.datetime(2026, 8, 17, 16, 15, tzinfo=NEW_YORK)
    state = StrategyLabDailyState(
        status="waiting_evidence",
        current_cycle=0,
        trace_depths=tuple((lab_id, 0) for lab_id in STRATEGY_LAB_IDS),
        evidence_bundle_available=False,
    )

    # When the post-close projection runs twice for the same session.
    with deliveries.writer() as writer:
        first = project_strategy_lab_daily_report(
            ExperimentLedgerReader(ledger.path),
            writer,
            now=after_cutoff,
            runtime_state=state,
        )
        replay = project_strategy_lab_daily_report(
            ExperimentLedgerReader(ledger.path),
            writer,
            now=after_cutoff,
            runtime_state=state,
        )

    # Then Hermes receives one honest six-lab blocker report rather than silence.
    events = HermesDeliveryReader(deliveries.path).events()
    assert first.examined == first.inserted == 1
    assert replay.examined == replay.replayed == 1
    assert len(events) == 1
    event = events[0]
    assert event.status == "waiting_evidence"
    assert "runtime_status=waiting_evidence" in event.rendered_text
    assert "evidence_bundle=missing" in event.rendered_text
    assert "complete_cycle=false" in event.rendered_text
    assert all(lab_id in event.rendered_text for lab_id in _LAB_IDS)
    assert event.evidence_refs == ()


def test_report_projects_complete_six_lab_cycle_and_replays_same_session(tmp_path: Path) -> None:
    # Given a complete six-lab cycle after the completed XNYS session cutoff.
    ledger = _complete_ledger(tmp_path)
    deliveries = HermesDeliveryStore(tmp_path / "deliveries.sqlite3")
    after_cutoff = dt.datetime(2026, 8, 17, 16, 15, tzinfo=NEW_YORK)
    reader = ExperimentLedgerReader(ledger.path)
    traces_before = tuple(reader.strategy_lab_trace(lab_id) for lab_id in STRATEGY_LAB_IDS)

    # When the report runs twice for the same session.
    with deliveries.writer() as writer:
        first = project_strategy_lab_daily_report(reader, writer, now=after_cutoff)
        replay = project_strategy_lab_daily_report(reader, writer, now=after_cutoff)

    # Then one bounded, redacted daily summary has all six latest lab outcomes and is idempotent.
    events = HermesDeliveryReader(deliveries.path).events()
    assert first.examined == first.inserted == 1
    assert replay.examined == replay.replayed == 1
    assert len(events) == 1
    event = events[0]
    assert event.kind.value == "daily_summary"
    assert event.source_event_id == "strategy-lab-daily-summary:2026-08-17"
    assert len(event.rendered_text) <= 4096
    assert "order authority: false" in event.rendered_text
    assert "profitability claim: false" in event.rendered_text
    assert all(lab_id in event.rendered_text for lab_id in _LAB_IDS)
    assert "outcome=" in event.rendered_text
    assert "adaptation=" in event.rendered_text
    assert "dataset=" in event.rendered_text
    assert "selected_observations=" in event.rendered_text
    assert "feedback=" in event.rendered_text
    assert tuple(reader.strategy_lab_trace(lab_id) for lab_id in STRATEGY_LAB_IDS) == traces_before


def test_report_accepts_known_projected_source_ids_without_payload_conflict(tmp_path: Path) -> None:
    # Given a complete report whose source identifier is already projected by a prior tick.
    ledger = _complete_ledger(tmp_path)
    deliveries = HermesDeliveryStore(tmp_path / "deliveries.sqlite3")
    after_cutoff = dt.datetime(2026, 8, 17, 16, 16, tzinfo=NEW_YORK)

    # When a later tick supplies the known source identifier.
    with deliveries.writer() as writer:
        result = project_strategy_lab_daily_report(
            ExperimentLedgerReader(ledger.path),
            writer,
            now=after_cutoff,
            projected_source_event_ids=frozenset({"strategy-lab-daily-summary:2026-08-17"}),
        )

    # Then it skips rendering and performs no delivery mutation.
    assert result.examined == result.inserted == result.replayed == 0
    assert HermesDeliveryReader(deliveries.path).events() == ()


def test_report_uses_early_close_session_for_weekend_catch_up(tmp_path: Path) -> None:
    # Given a complete six-lab cycle and the Saturday after the 2026 XNYS early close.
    ledger = _complete_ledger(tmp_path)
    deliveries = HermesDeliveryStore(tmp_path / "deliveries.sqlite3")
    weekend_tick = dt.datetime(2026, 11, 28, 9, 0, tzinfo=NEW_YORK)

    # When the projection catches up after the Friday early-close cutoff.
    with deliveries.writer() as writer:
        result = project_strategy_lab_daily_report(ExperimentLedgerReader(ledger.path), writer, now=weekend_tick)

    # Then it emits the Friday session's daily summary.
    event = HermesDeliveryReader(deliveries.path).events()[0]
    assert result.inserted == 1
    assert event.source_event_id == "strategy-lab-daily-summary:2026-11-27"


def test_report_emits_new_source_event_for_next_completed_session(tmp_path: Path) -> None:
    # Given a delivery already projected for one completed XNYS session.
    ledger = _complete_ledger(tmp_path)
    deliveries = HermesDeliveryStore(tmp_path / "deliveries.sqlite3")
    first_tick = dt.datetime(2026, 8, 17, 16, 16, tzinfo=NEW_YORK)
    next_tick = dt.datetime(2026, 8, 18, 16, 16, tzinfo=NEW_YORK)

    # When the following session also reaches its post-close cutoff.
    with deliveries.writer() as writer:
        _ = project_strategy_lab_daily_report(ExperimentLedgerReader(ledger.path), writer, now=first_tick)
        next_result = project_strategy_lab_daily_report(ExperimentLedgerReader(ledger.path), writer, now=next_tick)

    # Then the next session has a distinct outbound source event.
    source_event_ids = tuple(event.source_event_id for event in HermesDeliveryReader(deliveries.path).events())
    assert next_result.inserted == 1
    assert source_event_ids == (
        "strategy-lab-daily-summary:2026-08-17",
        "strategy-lab-daily-summary:2026-08-18",
    )


_LAB_IDS = (
    "intraday_momentum",
    "intraday_mean_reversion",
    "catalyst_event",
    "swing_trend_regime",
    "cross_sectional_quant",
    "derivatives_volatility",
)


def _complete_ledger(tmp_path: Path) -> ExperimentLedgerStore:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = StrategyLabFleet(ledger).run_cycle(_bundle(EvidenceMode.HISTORICAL), dt.datetime(2026, 8, 17, tzinfo=dt.UTC))
    return ledger
