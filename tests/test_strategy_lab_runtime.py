from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path

from trading_agent.experiment_ledger_schema import (
    CREATE_EXPERIMENT_LEDGER_SCHEMA_V1,
    CREATE_MULTI_MARKET_LIFECYCLE_SCHEMA_V6,
    CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4,
    CREATE_MULTI_MARKET_TRIAL_SCHEMA_V5,
    CREATE_RESEARCH_DISCOVERY_SOURCE_SCHEMA_V7,
    CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2,
    CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3,
    EXPERIMENT_LEDGER_SCHEMA_VERSION,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.strategy_lab_models import (
    STRATEGY_LAB_IDS,
    EvidenceMode,
    LabEvidenceBatch,
    LabObservation,
    SignalDirection,
    StrategyLabEvidenceBundle,
    StrategyLabId,
    StrategyLabOutcome,
    strategy_lab_spec,
)
from trading_agent.strategy_lab_runtime import StrategyLabRuntime

UTC = dt.UTC
NOW = dt.datetime(2026, 8, 17, 1, tzinfo=UTC)


def test_tick_runs_one_six_lab_cycle_and_resumes_from_sqlite(tmp_path: Path) -> None:
    # Given: a private canonical bundle with two immediately available batches per lab.
    bundle_path = tmp_path / "evidence.json"
    _write_private_bundle(bundle_path, _bundle((NOW, NOW)))
    ledger_path = tmp_path / "experiment.sqlite3"

    # When: one runtime instance ticks once, then a new instance ticks the same ledger.
    first = StrategyLabRuntime(bundle_path, ExperimentLedgerStore(ledger_path)).tick(NOW)
    second = StrategyLabRuntime(bundle_path, ExperimentLedgerStore(ledger_path)).tick(NOW)

    # Then: each tick persists exactly one complete six-lab cycle and never mutates trading.
    assert first.status == "completed"
    assert first.current_cycle == 1
    assert tuple(item.depth for item in first.trace_depths) == (1,) * 6
    assert first.next_wake_at == NOW
    assert second.status == "completed"
    assert second.current_cycle == 2
    assert tuple(item.depth for item in second.trace_depths) == (2,) * 6
    assert second.next_wake_at is None
    assert all(
        len(ExperimentLedgerReader(ledger_path).strategy_lab_trace(lab_id)) == 2
        for lab_id in STRATEGY_LAB_IDS
    )
    assert first.broker_mutation == first.trading_mutation == 0
    assert ledger_path.stat().st_mode & 0o777 == 0o600


def test_tick_waits_for_the_last_available_current_batch(tmp_path: Path) -> None:
    # Given: the next atomic fleet batch is private but cannot complete until a future time.
    bundle_path = tmp_path / "evidence.json"
    available_at = NOW + dt.timedelta(hours=2)
    _write_private_bundle(bundle_path, _bundle((available_at,), spread_availability=True))
    ledger_path = tmp_path / "experiment.sqlite3"

    # When: the runtime ticks before the final availability time.
    waiting = StrategyLabRuntime(bundle_path, ExperimentLedgerStore(ledger_path)).tick(NOW)

    # Then: it waits without a partial trace or trading mutation.
    assert waiting.status == "waiting_availability"
    assert waiting.current_cycle == 0
    assert waiting.next_wake_at == available_at + dt.timedelta(hours=5)
    assert waiting.broker_mutation == waiting.trading_mutation == 0
    assert not ledger_path.exists()


def test_tick_waits_for_missing_evidence_without_creating_a_ledger(tmp_path: Path) -> None:
    # Given: no bundle exists at the watched private path.
    bundle_path = tmp_path / "missing.json"
    ledger_path = tmp_path / "experiment.sqlite3"

    # When: the runtime ticks.
    status = StrategyLabRuntime(bundle_path, ExperimentLedgerStore(ledger_path)).tick(NOW)

    # Then: it waits for evidence and makes no persistence or trading mutation.
    assert status.status == "waiting_evidence"
    assert status.current_cycle == 0
    assert status.next_wake_at is None
    assert status.broker_mutation == status.trading_mutation == 0
    assert not ledger_path.exists()


def test_tick_migrates_an_existing_v7_ledger_before_waiting_for_evidence(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "missing.json"
    ledger_path = tmp_path / "experiment.sqlite3"
    with sqlite3.connect(ledger_path) as connection:
        connection.executescript(
            CREATE_EXPERIMENT_LEDGER_SCHEMA_V1
            + CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2
            + CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3
            + CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4
            + CREATE_MULTI_MARKET_TRIAL_SCHEMA_V5
            + CREATE_MULTI_MARKET_LIFECYCLE_SCHEMA_V6
            + CREATE_RESEARCH_DISCOVERY_SOURCE_SCHEMA_V7
        )
        _ = connection.execute("PRAGMA user_version = 7")
        connection.commit()
    os.chmod(ledger_path, 0o600)

    status = StrategyLabRuntime(bundle_path, ExperimentLedgerStore(ledger_path)).tick(NOW)

    with sqlite3.connect(ledger_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
    assert status.status == "waiting_evidence"
    assert status.current_cycle == 0
    assert version == (EXPERIMENT_LEDGER_SCHEMA_VERSION,)


def test_tick_waits_for_exhausted_evidence_without_raising(tmp_path: Path) -> None:
    # Given: every lab has exactly one already-processed batch in the private bundle.
    bundle_path = tmp_path / "evidence.json"
    _write_private_bundle(bundle_path, _bundle((NOW,)))
    ledger_path = tmp_path / "experiment.sqlite3"
    runtime = StrategyLabRuntime(bundle_path, ExperimentLedgerStore(ledger_path))
    _ = runtime.tick(NOW)

    # When: a fresh runtime ticks after the bundle is exhausted.
    status = StrategyLabRuntime(bundle_path, ExperimentLedgerStore(ledger_path)).tick(NOW)

    # Then: it reports waiting evidence and retains the complete append-only first cycle.
    assert status.status == "waiting_evidence"
    assert status.current_cycle == 1
    assert tuple(item.depth for item in status.trace_depths) == (1,) * 6
    assert all(
        len(ExperimentLedgerReader(ledger_path).strategy_lab_trace(lab_id)) == 1
        for lab_id in STRATEGY_LAB_IDS
    )


def test_tick_blocks_a_malformed_private_bundle(tmp_path: Path) -> None:
    # Given: a private file whose contents are not a canonical evidence bundle.
    bundle_path = tmp_path / "evidence.json"
    bundle_path.write_text("{", encoding="utf-8")
    os.chmod(bundle_path, 0o600)
    ledger_path = tmp_path / "experiment.sqlite3"

    # When: the runtime reads the malformed file.
    status = StrategyLabRuntime(bundle_path, ExperimentLedgerStore(ledger_path)).tick(NOW)

    # Then: it blocks without writing a partial experiment ledger.
    assert status.status == "blocked"
    assert status.current_cycle == 0
    assert not ledger_path.exists()


def test_tick_blocks_a_bundle_that_mismatches_fixed_lab_specifications(tmp_path: Path) -> None:
    # Given: a private parseable bundle with one lab's fixed feature changed.
    bundle_path = tmp_path / "evidence.json"
    _write_private_bundle(bundle_path, _bundle((NOW,), mismatched_lab=STRATEGY_LAB_IDS[0]))
    ledger_path = tmp_path / "experiment.sqlite3"

    # When: the runtime validates the bundle before fleet execution.
    status = StrategyLabRuntime(bundle_path, ExperimentLedgerStore(ledger_path)).tick(NOW)

    # Then: it blocks before protocol or trace persistence.
    assert status.status == "blocked"
    assert status.current_cycle == 0
    assert not ledger_path.exists()


def test_tick_preserves_synthetic_inconclusive_results(tmp_path: Path) -> None:
    # Given: a private synthetic evidence bundle that is available now.
    bundle_path = tmp_path / "evidence.json"
    _write_private_bundle(bundle_path, _bundle((NOW,)))
    ledger_path = tmp_path / "experiment.sqlite3"

    # When: the runtime executes its one allowed fleet cycle.
    status = StrategyLabRuntime(bundle_path, ExperimentLedgerStore(ledger_path)).tick(NOW)

    # Then: each persisted result remains inconclusive and the runtime claims no trading mutation.
    assert status.status == "completed"
    assert {
        node.body.result.outcome
        for lab_id in STRATEGY_LAB_IDS
        for node in ExperimentLedgerReader(ledger_path).strategy_lab_trace(lab_id)
    } == {StrategyLabOutcome.INCONCLUSIVE}
    assert status.broker_mutation == status.trading_mutation == 0


def _write_private_bundle(path: Path, bundle: StrategyLabEvidenceBundle) -> None:
    path.write_text(bundle.model_dump_json(), encoding="utf-8")
    os.chmod(path, 0o600)


def _bundle(
    available_at: tuple[dt.datetime, ...],
    mismatched_lab: StrategyLabId | None = None,
    spread_availability: bool = False,
) -> StrategyLabEvidenceBundle:
    batches: list[LabEvidenceBatch] = []
    for index, lab_id in enumerate(STRATEGY_LAB_IDS):
        spec = strategy_lab_spec(lab_id)
        for sequence, batch_available_at in enumerate(available_at, start=1):
            batches.append(
                LabEvidenceBatch(
                    lab_id=lab_id,
                    dataset_id=f"{lab_id.value}-{sequence}",
                    period_start=dt.date(2026, sequence, 1),
                    period_end=dt.date(2026, sequence, 2),
                    available_at=(
                        batch_available_at + dt.timedelta(hours=index)
                        if spread_availability
                        else batch_available_at
                    ),
                    source_ref=f"fixture:{lab_id.value}:{sequence}",
                    evidence_mode=EvidenceMode.SYNTHETIC,
                    feature_name=(
                        "mismatched_feature"
                        if lab_id is mismatched_lab
                        else spec.feature_name
                    ),
                    target_name=spec.target_name,
                    cost_bps=0,
                    observations=(
                        LabObservation(
                            signal=_selected_signal(lab_id),
                            forward_return=0.01,
                            baseline_return=0.0,
                        ),
                    ),
                )
            )
    return StrategyLabEvidenceBundle(batches=tuple(batches))


def _selected_signal(lab_id: StrategyLabId) -> float:
    spec = strategy_lab_spec(lab_id)
    return (
        max(spec.thresholds) + 1.0
        if spec.direction is SignalDirection.HIGH
        else min(spec.thresholds) - 1.0
    )
