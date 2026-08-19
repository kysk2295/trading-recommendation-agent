from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Protocol, assert_never

from trading_agent.strategy_lab_errors import StrategyLabKernelError
from trading_agent.strategy_lab_keys import strategy_lab_evidence_sha256
from trading_agent.strategy_lab_models import (
    EvidenceMode,
    LabEvidenceBatch,
    SignalDirection,
    StrategyLabOutcome,
    StrategyLabProtocol,
    StrategyLabStatisticalResult,
)


class StrategyLabRunner(Protocol):
    def run(
        self,
        protocol: StrategyLabProtocol,
        batch: LabEvidenceBatch,
        evaluated_at: dt.datetime,
    ) -> StrategyLabStatisticalResult: ...


class StatisticalStrategyLabRunner:
    def run(
        self,
        protocol: StrategyLabProtocol,
        batch: LabEvidenceBatch,
        evaluated_at: dt.datetime,
    ) -> StrategyLabStatisticalResult:
        _require_protocol_batch_match(protocol, batch)
        if evaluated_at < batch.available_at:
            raise StrategyLabKernelError("strategy lab evidence is not available at evaluation time")
        selected = _selected_net_excess_returns(protocol, batch)
        match batch.evidence_mode:
            case EvidenceMode.SYNTHETIC:
                return StrategyLabStatisticalResult(
                    protocol_id=protocol.protocol_id,
                    outcome=StrategyLabOutcome.INCONCLUSIVE,
                    reason_codes=("synthetic_evidence",),
                    selected_observations=len(selected),
                    net_excess_return_mean=None,
                    ci95_lower=None,
                    ci95_upper=None,
                    evaluated_at=evaluated_at,
                )
            case EvidenceMode.HISTORICAL:
                return _historical_result(
                    protocol.protocol_id,
                    selected,
                    protocol.body.minimum_selected_observations,
                    evaluated_at,
                )
            case unreachable:
                assert_never(unreachable)


def _require_protocol_batch_match(protocol: StrategyLabProtocol, batch: LabEvidenceBatch) -> None:
    body = protocol.body
    if (
        body.lab_id is not batch.lab_id
        or body.dataset_id != batch.dataset_id
        or body.feature_name != batch.feature_name
        or body.target_name != batch.target_name
        or body.evidence_sha256 != strategy_lab_evidence_sha256(batch)
        or body.evidence_mode is not batch.evidence_mode
        or body.period_start != batch.period_start
        or body.period_end != batch.period_end
        or body.source_ref != batch.source_ref
        or body.cost_bps != batch.cost_bps
        or body.observation_count != len(batch.observations)
    ):
        raise StrategyLabKernelError("strategy lab protocol and evidence batch differ")


def _selected_net_excess_returns(protocol: StrategyLabProtocol, batch: LabEvidenceBatch) -> tuple[float, ...]:
    threshold = protocol.body.selected_threshold
    match protocol.body.direction:
        case SignalDirection.HIGH:
            selected = tuple(item for item in batch.observations if item.signal >= threshold)
        case SignalDirection.LOW:
            selected = tuple(item for item in batch.observations if item.signal <= threshold)
        case unreachable:
            assert_never(unreachable)
    cost = batch.cost_bps / 10_000
    return tuple(item.forward_return - item.baseline_return - cost for item in selected)


def _historical_result(
    protocol_id: str,
    selected: tuple[float, ...],
    minimum_selected_observations: int,
    evaluated_at: dt.datetime,
) -> StrategyLabStatisticalResult:
    if len(selected) < minimum_selected_observations:
        return StrategyLabStatisticalResult(
            protocol_id=protocol_id,
            outcome=StrategyLabOutcome.INCONCLUSIVE,
            reason_codes=(
                "historical_only_no_profitability_claim",
                "insufficient_selected_observations",
            ),
            selected_observations=len(selected),
            net_excess_return_mean=None,
            ci95_lower=None,
            ci95_upper=None,
            evaluated_at=evaluated_at,
        )
    mean = statistics.fmean(selected)
    standard_error = statistics.stdev(selected) / math.sqrt(len(selected))
    lower = mean - 1.96 * standard_error
    upper = mean + 1.96 * standard_error
    if lower > 0:
        outcome = StrategyLabOutcome.SUPPORTED
        reason_codes = ("ci95_net_excess_return_positive", "historical_only_no_profitability_claim")
    elif upper < 0:
        outcome = StrategyLabOutcome.REFUTED
        reason_codes = ("ci95_net_excess_return_negative", "historical_only_no_profitability_claim")
    else:
        outcome = StrategyLabOutcome.INCONCLUSIVE
        reason_codes = ("ci95_net_excess_return_crosses_zero", "historical_only_no_profitability_claim")
    return StrategyLabStatisticalResult(
        protocol_id=protocol_id,
        outcome=outcome,
        reason_codes=reason_codes,
        selected_observations=len(selected),
        net_excess_return_mean=mean,
        ci95_lower=lower,
        ci95_upper=upper,
        evaluated_at=evaluated_at,
    )
