from __future__ import annotations

import datetime as dt
from typing import assert_never

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.strategy_lab_errors import StrategyLabKernelError
from trading_agent.strategy_lab_keys import (
    strategy_lab_evidence_sha256,
    strategy_lab_hypothesis_id,
    strategy_lab_node_id,
    strategy_lab_protocol_id,
)
from trading_agent.strategy_lab_models import (
    LabEvidenceBatch,
    StrategyLabAdaptation,
    StrategyLabCycle,
    StrategyLabEvidenceBundle,
    StrategyLabHypothesis,
    StrategyLabId,
    StrategyLabOutcome,
    StrategyLabProtocol,
    StrategyLabProtocolBody,
    StrategyLabStatisticalResult,
    StrategyLabTraceNode,
    StrategyLabTraceNodeBody,
    strategy_lab_spec,
)
from trading_agent.strategy_lab_statistics import (
    StatisticalStrategyLabRunner,
    StrategyLabRunner,
    _require_protocol_batch_match,
)


class StrategyLabFleet:
    def __init__(
        self,
        ledger: ExperimentLedgerStore,
        runner: StrategyLabRunner | None = None,
    ) -> None:
        self._ledger = ledger
        self._runner = StatisticalStrategyLabRunner() if runner is None else runner

    def run_cycle(self, bundle: StrategyLabEvidenceBundle, evaluated_at: dt.datetime) -> StrategyLabCycle:
        if evaluated_at.tzinfo is None:
            raise StrategyLabKernelError("strategy lab evaluation timestamp must be aware")
        traces = tuple(self._ledger.strategy_lab_trace(lab_id) for lab_id in _lab_ids())
        depths = tuple(len(trace) for trace in traces)
        if len(set(depths)) != 1:
            raise StrategyLabKernelError("strategy lab traces must advance together")
        cycle_number = depths[0] + 1
        selections: list[tuple[StrategyLabId, LabEvidenceBatch, StrategyLabTraceNode | None]] = []
        for lab_id, trace in zip(_lab_ids(), traces, strict=True):
            batches = bundle.batches_for(lab_id)
            if cycle_number > len(batches):
                raise StrategyLabKernelError("strategy lab evidence exhausted")
            previous = trace[-1] if trace else None
            selections.append((lab_id, batches[cycle_number - 1], previous))
        if any(evaluated_at < batch.available_at for _, batch, _ in selections):
            raise StrategyLabKernelError("strategy lab evidence is not available at evaluation time")
        plans = tuple(
            (
                _reusable_or_compiled_protocol(self._ledger, lab_id, batch, previous, evaluated_at),
                batch,
                previous,
            )
            for lab_id, batch, previous in selections
        )
        with self._ledger.writer() as writer:
            for protocol, _, _ in plans:
                _ = writer.register_strategy_lab_protocol(protocol)
        nodes = tuple(
            _trace_node(cycle_number, protocol, previous, self._runner.run(protocol, batch, evaluated_at))
            for protocol, batch, previous in plans
        )
        with self._ledger.writer() as writer:
            for node in nodes:
                _ = writer.append_strategy_lab_trace_node(node)
        return StrategyLabCycle(cycle_number=cycle_number, nodes=nodes)


def _lab_ids() -> tuple[StrategyLabId, ...]:
    from trading_agent.strategy_lab_models import STRATEGY_LAB_IDS

    return STRATEGY_LAB_IDS


def _compile_protocol(
    lab_id: StrategyLabId,
    batch: LabEvidenceBatch,
    previous: StrategyLabTraceNode | None,
    frozen_at: dt.datetime,
) -> StrategyLabProtocol:
    spec = strategy_lab_spec(lab_id)
    if batch.feature_name != spec.feature_name or batch.target_name != spec.target_name:
        raise StrategyLabKernelError("strategy lab evidence does not match fixed specification")
    adaptation = _adaptation(previous)
    selected_threshold = (
        spec.thresholds[1] if adaptation is StrategyLabAdaptation.BOUNDED_ALTERNATIVE else spec.thresholds[0]
    )
    parent_node_id = None if previous is None else previous.node_id
    hypothesis = StrategyLabHypothesis(
        hypothesis_id=strategy_lab_hypothesis_id(lab_id.value, batch.dataset_id, parent_node_id, adaptation.value),
        lab_id=lab_id,
        parent_node_id=parent_node_id,
        adaptation=adaptation,
        statement=f"{spec.feature_name} predicts {spec.target_name} through {spec.economic_mechanism}",
        falsification_rule="The 95% confidence interval for net excess return is below zero.",
    )
    body = StrategyLabProtocolBody(
        lab_id=lab_id,
        hypothesis=hypothesis,
        dataset_id=batch.dataset_id,
        feature_name=spec.feature_name,
        target_name=spec.target_name,
        direction=spec.direction,
        thresholds=spec.thresholds,
        selected_threshold=selected_threshold,
        economic_mechanism=spec.economic_mechanism,
        evidence_sha256=strategy_lab_evidence_sha256(batch),
        evidence_mode=batch.evidence_mode,
        period_start=batch.period_start,
        period_end=batch.period_end,
        source_ref=batch.source_ref,
        cost_bps=batch.cost_bps,
        observation_count=len(batch.observations),
        search_family_size=len(spec.thresholds),
        available_at=batch.available_at,
        frozen_at=frozen_at,
    )
    provisional = StrategyLabProtocol(protocol_id="0" * 64, body=body)
    return provisional.model_copy(update={"protocol_id": strategy_lab_protocol_id(provisional)})


def _reusable_or_compiled_protocol(
    ledger: ExperimentLedgerStore,
    lab_id: StrategyLabId,
    batch: LabEvidenceBatch,
    previous: StrategyLabTraceNode | None,
    frozen_at: dt.datetime,
) -> StrategyLabProtocol:
    matching = tuple(
        protocol for protocol in ledger.strategy_lab_protocols(lab_id) if protocol.body.dataset_id == batch.dataset_id
    )
    if not matching:
        return _compile_protocol(lab_id, batch, previous, frozen_at)
    if len(matching) != 1:
        raise StrategyLabKernelError("strategy lab protocol identity is ambiguous")
    protocol = matching[0]
    _require_protocol_batch_match(protocol, batch)
    if protocol.body.hypothesis.parent_node_id != (None if previous is None else previous.node_id):
        raise StrategyLabKernelError("strategy lab protocol parent does not match trace")
    return protocol


def _adaptation(previous: StrategyLabTraceNode | None) -> StrategyLabAdaptation:
    if previous is None:
        return StrategyLabAdaptation.INITIAL
    match previous.body.result.outcome:
        case StrategyLabOutcome.SUPPORTED:
            return StrategyLabAdaptation.REPLICATION
        case StrategyLabOutcome.REFUTED:
            return StrategyLabAdaptation.BOUNDED_ALTERNATIVE
        case StrategyLabOutcome.INCONCLUSIVE:
            return StrategyLabAdaptation.MORE_EVIDENCE
        case unreachable:
            assert_never(unreachable)


def _trace_node(
    iteration: int,
    protocol: StrategyLabProtocol,
    previous: StrategyLabTraceNode | None,
    result: StrategyLabStatisticalResult,
) -> StrategyLabTraceNode:
    body = StrategyLabTraceNodeBody(
        lab_id=protocol.body.lab_id,
        iteration=iteration,
        parent_node_id=None if previous is None else previous.node_id,
        protocol_id=protocol.protocol_id,
        result=result,
        feedback=_feedback(result.outcome),
    )
    provisional = StrategyLabTraceNode(node_id="0" * 64, body=body)
    return provisional.model_copy(update={"node_id": strategy_lab_node_id(provisional)})


def _feedback(outcome: StrategyLabOutcome) -> StrategyLabAdaptation:
    match outcome:
        case StrategyLabOutcome.SUPPORTED:
            return StrategyLabAdaptation.REPLICATION
        case StrategyLabOutcome.REFUTED:
            return StrategyLabAdaptation.BOUNDED_ALTERNATIVE
        case StrategyLabOutcome.INCONCLUSIVE:
            return StrategyLabAdaptation.MORE_EVIDENCE
        case unreachable:
            assert_never(unreachable)
