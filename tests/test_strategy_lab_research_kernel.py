from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from run_strategy_lab_cycle import main
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.strategy_lab_kernel import StatisticalStrategyLabRunner, StrategyLabFleet
from trading_agent.strategy_lab_models import (
    STRATEGY_LAB_IDS,
    EvidenceMode,
    LabEvidenceBatch,
    LabObservation,
    SignalDirection,
    StrategyLabAdaptation,
    StrategyLabEvidenceBundle,
    StrategyLabId,
    StrategyLabOutcome,
    StrategyLabProtocol,
    StrategyLabStatisticalResult,
    strategy_lab_spec,
)

UTC = dt.UTC


class _ProtocolPersistenceRunner:
    def __init__(self, ledger: ExperimentLedgerStore) -> None:
        self._ledger = ledger
        self._delegate = StatisticalStrategyLabRunner()

    def run(
        self,
        protocol: StrategyLabProtocol,
        batch: LabEvidenceBatch,
        evaluated_at: dt.datetime,
    ):
        persisted = ExperimentLedgerReader(self._ledger.path).strategy_lab_protocols(
            protocol.body.lab_id
        )
        assert protocol.protocol_id in {item.protocol_id for item in persisted}
        return self._delegate.run(protocol, batch, evaluated_at)


def test_six_labs_run_independent_feedback_linked_research_cycles(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    bundle = _bundle(EvidenceMode.HISTORICAL)
    fleet = StrategyLabFleet(ledger, _ProtocolPersistenceRunner(ledger))

    first = fleet.run_cycle(bundle, dt.datetime(2026, 8, 17, 1, tzinfo=UTC))
    second = fleet.run_cycle(bundle, dt.datetime(2026, 8, 17, 2, tzinfo=UTC))

    assert first.cycle_number == 1
    assert second.cycle_number == 2
    assert tuple(node.body.lab_id for node in first.nodes) == STRATEGY_LAB_IDS
    assert len({node.node_id for node in (*first.nodes, *second.nodes)}) == 12

    reader = ExperimentLedgerReader(ledger.path)
    expected_adaptations = (
        StrategyLabAdaptation.REPLICATION,
        StrategyLabAdaptation.BOUNDED_ALTERNATIVE,
        StrategyLabAdaptation.MORE_EVIDENCE,
    )
    for index, lab_id in enumerate(STRATEGY_LAB_IDS):
        trace = reader.strategy_lab_trace(lab_id)
        protocols = reader.strategy_lab_protocols(lab_id)
        assert len(trace) == len(protocols) == 2
        assert trace[0].body.parent_node_id is None
        assert trace[1].body.parent_node_id == trace[0].node_id
        assert protocols[1].body.hypothesis.parent_node_id == trace[0].node_id
        assert protocols[1].body.hypothesis.adaptation is expected_adaptations[index % 3]
        expected_threshold = (
            strategy_lab_spec(lab_id).thresholds[1]
            if expected_adaptations[index % 3]
            is StrategyLabAdaptation.BOUNDED_ALTERNATIVE
            else strategy_lab_spec(lab_id).thresholds[0]
        )
        assert protocols[1].body.selected_threshold == expected_threshold
        assert protocols[1].body.evidence_sha256 != protocols[0].body.evidence_sha256
        assert protocols[1].body.observation_count == 4
        assert protocols[1].body.minimum_selected_observations == 4
        assert protocols[1].body.primary_metric == "net_excess_return_ci95"
        assert protocols[0].body.dataset_id != protocols[1].body.dataset_id
        assert trace[1].body.lifecycle_authority is False
        assert trace[1].body.allocation_authority is False
        assert trace[1].body.order_authority is False
        assert trace[1].body.profitability_claim is False


def test_synthetic_evidence_cannot_support_a_hypothesis(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    result = StrategyLabFleet(ledger).run_cycle(
        _bundle(EvidenceMode.SYNTHETIC),
        dt.datetime(2026, 8, 17, 1, tzinfo=UTC),
    )

    assert {node.body.result.outcome for node in result.nodes} == {
        StrategyLabOutcome.INCONCLUSIVE
    }
    assert all("synthetic_evidence" in node.body.result.reason_codes for node in result.nodes)


def test_supported_result_requires_positive_interval_metrics() -> None:
    with pytest.raises(ValidationError):
        _ = StrategyLabStatisticalResult(
            protocol_id="a" * 64,
            outcome=StrategyLabOutcome.SUPPORTED,
            reason_codes=("unsupported_claim",),
            selected_observations=4,
            net_excess_return_mean=None,
            ci95_lower=None,
            ci95_upper=None,
            evaluated_at=dt.datetime(2026, 8, 17, tzinfo=UTC),
        )


def test_strategy_lab_trace_tables_are_append_only(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = StrategyLabFleet(ledger).run_cycle(
        _bundle(EvidenceMode.HISTORICAL),
        dt.datetime(2026, 8, 17, 1, tzinfo=UTC),
    )

    with sqlite3.connect(ledger.path) as connection, pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute(
            "UPDATE strategy_lab_trace_nodes SET outcome='supported'"
        )


def test_failed_lab_does_not_leave_a_partial_six_lab_trace(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    with pytest.raises(RuntimeError, match="forced_runner_failure"):
        _ = StrategyLabFleet(ledger, _FailingRunner()).run_cycle(
            _bundle(EvidenceMode.HISTORICAL),
            dt.datetime(2026, 8, 17, 1, tzinfo=UTC),
        )

    reader = ExperimentLedgerReader(ledger.path)
    assert len(reader.strategy_lab_protocols()) == 6
    assert all(not reader.strategy_lab_trace(lab_id) for lab_id in STRATEGY_LAB_IDS)
    recovered = StrategyLabFleet(ledger).run_cycle(
        _bundle(EvidenceMode.HISTORICAL),
        dt.datetime(2026, 8, 17, 2, tzinfo=UTC),
    )
    assert len(recovered.nodes) == 6


def test_future_or_nonfinite_evidence_fails_before_protocol_registration(
    tmp_path: Path,
) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    with pytest.raises(ValueError, match="available"):
        _ = StrategyLabFleet(ledger).run_cycle(
            _bundle(EvidenceMode.HISTORICAL),
            dt.datetime(2026, 1, 15, tzinfo=UTC),
        )
    assert not ExperimentLedgerReader(ledger.path).strategy_lab_protocols()

    with pytest.raises(ValidationError):
        _ = LabObservation(signal=float("nan"), forward_return=0.01, baseline_return=0.0)


def test_cli_runs_two_observable_cycles_and_rejects_incomplete_bundle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(_bundle(EvidenceMode.SYNTHETIC).model_dump_json(), encoding="utf-8")
    ledger_path = tmp_path / "experiment.sqlite3"

    exit_code = main(
        (
            "--evidence-bundle",
            str(bundle_path),
            "--experiment-ledger",
            str(ledger_path),
            "--iterations",
            "2",
            "--as-of",
            "2026-08-17T01:00:00+00:00",
        )
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["status"] == "complete"
    assert report["completed_iterations"] == 2
    assert report["lab_count"] == 6
    assert all(item["trace_depth"] == 2 for item in report["labs"])
    assert all(item["feedback_linked"] is True for item in report["labs"])
    assert report["order_authority"] is False
    assert report["trading_mutation"] == 0

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text('{"schema_version":1,"batches":[]}', encoding="utf-8")
    assert main(
        (
            "--evidence-bundle",
            str(incomplete),
            "--experiment-ledger",
            str(tmp_path / "invalid.sqlite3"),
            "--iterations",
            "1",
        )
    ) == 1
    blocked = json.loads(capsys.readouterr().out)
    assert blocked == {"reason": "evidence_or_trace_invalid", "status": "blocked"}


def _bundle(mode: EvidenceMode) -> StrategyLabEvidenceBundle:
    batches: list[LabEvidenceBatch] = []
    outcomes = ("positive", "negative", "mixed")
    for index, lab_id in enumerate(STRATEGY_LAB_IDS):
        spec = strategy_lab_spec(lab_id)
        for iteration in (1, 2):
            signal = _selected_signal(lab_id)
            outcome = outcomes[index % 3] if iteration == 1 else "positive"
            returns = {
                "positive": (0.03, 0.03, 0.03, 0.03),
                "negative": (-0.03, -0.03, -0.03, -0.03),
                "mixed": (-0.03, 0.03, -0.03, 0.03),
            }[outcome]
            start = dt.date(2026, iteration, 1)
            end = dt.date(2026, iteration, 28)
            batches.append(
                LabEvidenceBatch(
                    lab_id=lab_id,
                    dataset_id=f"{lab_id.value}-2026-{iteration:02d}",
                    period_start=start,
                    period_end=end,
                    available_at=dt.datetime(2026, iteration + 1, 1, tzinfo=UTC),
                    source_ref=f"local-fixture:{lab_id.value}:{iteration}",
                    evidence_mode=mode,
                    feature_name=spec.feature_name,
                    target_name=spec.target_name,
                    cost_bps=0,
                    observations=tuple(
                        LabObservation(signal=signal, forward_return=value, baseline_return=0.0)
                        for value in returns
                    ),
                )
            )
    return StrategyLabEvidenceBundle(batches=tuple(batches))


def _selected_signal(lab_id: StrategyLabId) -> float:
    spec = strategy_lab_spec(lab_id)
    if spec.direction is SignalDirection.HIGH:
        return max(spec.thresholds) + 1.0
    return min(spec.thresholds) - 1.0


class _FailingRunner:
    def __init__(self) -> None:
        self._delegate = StatisticalStrategyLabRunner()

    def run(
        self,
        protocol: StrategyLabProtocol,
        batch: LabEvidenceBatch,
        evaluated_at: dt.datetime,
    ):
        if protocol.body.lab_id is StrategyLabId.CATALYST_EVENT:
            raise RuntimeError("forced_runner_failure")
        return self._delegate.run(protocol, batch, evaluated_at)
