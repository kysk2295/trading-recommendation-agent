from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

from tests.strategy_research_contract_fixtures import hypothesis
from tests.test_strategy_research_shadow import _observation, _supported_store
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_errors import HermesDeliveryConflictError
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.strategy_research_close_report import project_strategy_research_close_report
from trading_agent.strategy_research_forward_observations import ForwardResearchObservation
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_shadow import FutureShadowPolicy, append_future_shadow_observation
from trading_agent.strategy_research_types import EvidenceKind, ResearchAgentId
from trading_agent.us_equity_calendar import NEW_YORK

AGENT_IDS = tuple(item.value for item in ResearchAgentId)


def _initialized_ledger(path: Path) -> ExperimentLedgerStore:
    store = ExperimentLedgerStore(path)
    with store.writer():
        pass
    return store


def test_close_report_is_silent_before_cutoff_then_projects_six_meaningful_rows_once(tmp_path: Path) -> None:
    # Given: persisted V9 state with no new terminal result and an empty Hermes ledger.
    ledger = _initialized_ledger(tmp_path / "experiment.sqlite3")
    deliveries = HermesDeliveryStore(tmp_path / "hermes.sqlite3")

    # When: projection runs before close+15m, then twice at the cutoff.
    with deliveries.writer() as writer:
        before = project_strategy_research_close_report(
            ledger, writer, dt.datetime(2026, 8, 17, 16, 14, tzinfo=NEW_YORK)
        )
        first = project_strategy_research_close_report(
            ledger, writer, dt.datetime(2026, 8, 17, 16, 15, tzinfo=NEW_YORK)
        )
        replay = project_strategy_research_close_report(
            ledger, writer, dt.datetime(2026, 8, 17, 16, 16, tzinfo=NEW_YORK)
        )

    # Then: exactly one detailed summary exists, not a misleading no-new message.
    events = HermesDeliveryReader(deliveries.path).events()
    assert before.examined == before.inserted == 0
    assert (first.inserted, replay.replayed, len(events)) == (1, 1, 1)
    event = events[0]
    assert event.source_event_id == "strategy-research-close-report:2026-08-17"
    assert event.kind.value == "daily_summary"
    assert event.status == "six_agent_state"
    assert all(agent_id in event.rendered_text for agent_id in AGENT_IDS)
    assert event.rendered_text.count("owner=") == 6
    assert event.rendered_text.count("waiting_reason=") == 6
    assert "no new" not in event.rendered_text.casefold()
    assert "profitability claim: false" in event.rendered_text
    assert "order authority: false" in event.rendered_text


def test_close_report_respects_early_close_and_restart_derivation(tmp_path: Path) -> None:
    # Given: a fresh reader-equivalent store and the 2026 early-close boundary.
    ledger_path = tmp_path / "experiment.sqlite3"
    _ = _initialized_ledger(ledger_path)
    deliveries = HermesDeliveryStore(tmp_path / "hermes.sqlite3")

    # When: a restarted store projects at 13:15 New York time.
    with deliveries.writer() as writer:
        result = project_strategy_research_close_report(
            ExperimentLedgerStore(ledger_path), writer, dt.datetime(2026, 11, 27, 13, 15, tzinfo=NEW_YORK)
        )

    # Then: the early-close session key is used and all rows derive without runtime memory.
    event = HermesDeliveryReader(deliveries.path).events()[0]
    assert result.inserted == 1
    assert event.source_event_id == "strategy-research-close-report:2026-11-27"
    assert event.rendered_text.count("stage=waiting_evidence") == 6


def test_close_report_exposes_only_owner_safe_result_and_shadow_progress(tmp_path: Path) -> None:
    # Given: one supported owner with a persisted future-only shadow sample.
    ledger = _supported_store(tmp_path / "experiment.sqlite3")
    shadow, _ = append_future_shadow_observation(
        ledger,
        _observation(),
        FutureShadowPolicy(future_sample_target=40, maximum_ci_width=0.02),
    )
    deliveries = HermesDeliveryStore(tmp_path / "hermes.sqlite3")

    # When: a restarted reader projects the same New York session close.
    with deliveries.writer() as writer:
        result = project_strategy_research_close_report(
            ExperimentLedgerStore(ledger.path),
            writer,
            dt.datetime(2026, 8, 19, 16, 15, tzinfo=NEW_YORK),
        )

    # Then: the safe reference occurs only on its owner row and private metrics never leave the ledger.
    event = HermesDeliveryReader(deliveries.path).events()[0]
    assert result.inserted == 1
    assert event.rendered_text.count("result_ref=safe-result-1") == 1
    assert f"shadow={shadow.shadow_sample_count}/{shadow.shadow_sample_target}" in event.rendered_text
    assert "private" not in event.rendered_text
    assert "artifact://" not in event.rendered_text
    assert "order authority: false" in event.rendered_text


def test_close_report_marks_fixture_state_wiring_only_without_profit_claim(tmp_path: Path) -> None:
    # Given: a fixture preregistration persisted for schema wiring only.
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    fixture = hypothesis(kind=EvidenceKind.FIXTURE)
    manifest = PreregistrationManifest.from_hypothesis(
        fixture,
        preregistered_at=fixture.created_at + dt.timedelta(minutes=1),
    )
    with ledger.writer() as writer:
        assert writer.register_strategy_research(manifest)
    deliveries = HermesDeliveryStore(tmp_path / "hermes.sqlite3")

    # When: the close report projects the fixture-backed active row.
    with deliveries.writer() as writer:
        result = project_strategy_research_close_report(
            ledger,
            writer,
            dt.datetime(2026, 8, 19, 16, 15, tzinfo=NEW_YORK),
        )

    # Then: the report labels wiring-only and preserves every authority denial.
    event = HermesDeliveryReader(deliveries.path).events()[0]
    assert result.inserted == 1
    assert "evidence_mode=wiring_only" in event.rendered_text
    assert "profitability claim: false" in event.rendered_text
    assert "trading authority: false" in event.rendered_text


def test_close_report_exposes_real_forward_sample_progress_without_claiming_profit(tmp_path: Path) -> None:
    ledger = _initialized_ledger(tmp_path / "experiment.sqlite3")
    deliveries = HermesDeliveryStore(tmp_path / "hermes.sqlite3")
    entered_at = dt.datetime(2026, 8, 19, 10, 0, tzinfo=NEW_YORK)
    sample = ForwardResearchObservation(
        observation_id="forward-sample-1",
        market_id="us_equities",
        source_opportunity_id="entry-1",
        exit_opportunity_id="exit-1",
        symbol="SPY",
        entered_at=entered_at,
        target_matured_at=entered_at + dt.timedelta(minutes=30),
        observed_at=entered_at + dt.timedelta(minutes=31),
        entry_price="500",
        exit_price="501",
        entry_spread_bps="1",
        exit_spread_bps="1",
        gross_return="0.002",
        net_return="0.0019",
        cluster_key="2026-08-19",
        evidence_refs=("bar/alpaca-sip:entry", "bar/alpaca-sip:exit"),
    )

    with deliveries.writer() as writer:
        result = project_strategy_research_close_report(
            ledger,
            writer,
            dt.datetime(2026, 8, 19, 16, 15, tzinfo=NEW_YORK),
            forward_observations=(sample,),
        )

    event = HermesDeliveryReader(deliveries.path).events()[0]
    assert result.inserted == 1
    assert "owner=intraday_momentum" in event.rendered_text
    assert "forward_samples=1" in event.rendered_text
    assert "profitability claim: false" in event.rendered_text


def test_close_report_same_session_state_drift_conflicts_without_overwrite(tmp_path: Path) -> None:
    # Given: an empty-state session summary already persisted immutably.
    ledger = _initialized_ledger(tmp_path / "experiment.sqlite3")
    deliveries = HermesDeliveryStore(tmp_path / "hermes.sqlite3")
    after_cutoff = dt.datetime(2026, 8, 19, 16, 15, tzinfo=NEW_YORK)
    with deliveries.writer() as writer:
        first = project_strategy_research_close_report(ledger, writer, after_cutoff)
    original = HermesDeliveryReader(deliveries.path).events()[0]
    fixture = hypothesis(kind=EvidenceKind.FIXTURE)
    manifest = PreregistrationManifest.from_hypothesis(
        fixture,
        preregistered_at=fixture.created_at + dt.timedelta(minutes=1),
    )
    with ledger.writer() as writer:
        assert writer.register_strategy_research(manifest)

    # When / Then: rebuilding the same session detects changed content and cannot append or overwrite.
    with deliveries.writer() as writer, pytest.raises(HermesDeliveryConflictError):
        project_strategy_research_close_report(ledger, writer, after_cutoff + dt.timedelta(minutes=1))
    events = HermesDeliveryReader(deliveries.path).events()
    assert first.inserted == 1
    assert events == (original,)
    assert "hypothesis=none" in original.rendered_text


def test_close_report_cli_help_and_bad_input_are_real_process_surfaces(tmp_path: Path) -> None:
    # Given: the close-report CLI path.
    script = Path("run_strategy_research_close_report.py")

    # When: help and a naive timestamp are invoked as real subprocesses.
    help_result = subprocess.run((sys.executable, str(script), "--help"), capture_output=True, text=True, check=False)
    bad = subprocess.run(
        (
            sys.executable,
            str(script),
            "--experiment-ledger",
            str(tmp_path / "missing.sqlite3"),
            "--hermes-ledger",
            str(tmp_path / "hermes.sqlite3"),
            "--now",
            "2026-08-17T16:15:00",
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: help succeeds and malformed time fails without a traceback.
    assert help_result.returncode == 0
    assert "--experiment-ledger" in help_result.stdout
    assert bad.returncode != 0
    assert "Traceback" not in bad.stderr
