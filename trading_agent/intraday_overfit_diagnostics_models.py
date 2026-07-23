from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import statistics
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from math import comb
from statistics import NormalDist
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

INTRADAY_OVERFIT_DIAGNOSTICS_VERSION: Final = "intraday_overfit_diagnostics_v1"
OVERFIT_MIN_VARIANTS: Final = 3
OVERFIT_MIN_SESSIONS: Final = 20
OVERFIT_MIN_TRADES: Final = 30
OVERFIT_MIN_BLOCK_SESSIONS: Final = 5
OVERFIT_MAX_CSCV_PARTITIONS: Final = 16
OVERFIT_MAX_CSCV_COMBINATIONS: Final = 12_870
EULER_MASCHERONI: Final = 0.5772156649015329
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class InvalidIntradayOverfitDiagnosticsError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday overfit diagnostics evidence is invalid"


class IntradayOverfitDiagnosticsStatus(StrEnum):
    COLLECTING = "collecting"
    DIAGNOSTIC_READY = "diagnostic_ready"


class IntradayOverfitCandidateTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_schema_version: Literal[1, 2] = 2
    trial_id: str
    strategy_version: str
    experiment_artifact_id: str
    review_artifact_id: str
    trade_count: int = Field(ge=0)
    session_dates: tuple[dt.date, ...]
    net_session_returns: tuple[float, ...]

    @model_validator(mode="after")
    def validate_trace(self) -> Self:
        values = self.net_session_returns
        if (
            _IDENTIFIER.fullmatch(self.trial_id) is None
            or _IDENTIFIER.fullmatch(self.strategy_version) is None
            or _HEX64.fullmatch(self.experiment_artifact_id) is None
            or _HEX64.fullmatch(self.review_artifact_id) is None
            or len(self.session_dates) != len(values)
            or len(values) > 1_000
            or self.session_dates != tuple(sorted(set(self.session_dates)))
            or any(not math.isfinite(value) or value <= -1.0 for value in values)
            or self.trade_count < sum(not math.isclose(value, 0.0, abs_tol=1e-15) for value in values)
            or (self.trace_schema_version == 1 and (self.session_dates or values))
            or (self.trace_schema_version == 2 and not self.session_dates)
        ):
            raise InvalidIntradayOverfitDiagnosticsError
        return self


class IntradayOverfitSharpeEstimate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version: str
    sharpe_ratio: float

    @model_validator(mode="after")
    def validate_estimate(self) -> Self:
        if _IDENTIFIER.fullmatch(self.strategy_version) is None or not math.isfinite(self.sharpe_ratio):
            raise InvalidIntradayOverfitDiagnosticsError
        return self


class IntradayOverfitStatistics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    candidates: tuple[IntradayOverfitCandidateTrace, ...]
    total_lane_historical_trials: int = Field(ge=1, le=1_000_000)
    status: IntradayOverfitDiagnosticsStatus
    blockers: tuple[str, ...]
    selected_strategy_version: str | None
    sharpe_estimates: tuple[IntradayOverfitSharpeEstimate, ...]
    expected_max_sharpe: float | None
    deflated_sharpe_probability: float | None
    cscv_partitions: int | None
    cscv_logits: tuple[float, ...]
    pbo_probability: float | None

    @model_validator(mode="after")
    def validate_statistics(self) -> Self:
        ordered = tuple(sorted(self.candidates, key=lambda item: item.strategy_version))
        identifiers = (
            tuple(item.trial_id for item in self.candidates),
            tuple(item.strategy_version for item in self.candidates),
            tuple(item.experiment_artifact_id for item in self.candidates),
            tuple(item.review_artifact_id for item in self.candidates),
        )
        expected = _derive_statistics(self.candidates, self.total_lane_historical_trials)
        actual_values = (
            self.expected_max_sharpe,
            self.deflated_sharpe_probability,
            self.pbo_probability,
        )
        expected_values = (
            expected.expected_max_sharpe,
            expected.deflated_sharpe_probability,
            expected.pbo_probability,
        )
        if (
            not 2 <= len(self.candidates) <= 3
            or self.candidates != ordered
            or any(len(set(values)) != len(values) for values in identifiers)
            or self.total_lane_historical_trials < len(self.candidates)
            or self.status is not expected.status
            or self.blockers != expected.blockers
            or self.selected_strategy_version != expected.selected_strategy_version
            or not _sharpe_estimates_match(self.sharpe_estimates, expected.sharpe_estimates)
            or not all(
                _optional_close(actual, target)
                for actual, target in zip(
                    actual_values,
                    expected_values,
                    strict=True,
                )
            )
            or self.cscv_partitions != expected.cscv_partitions
            or not _float_tuples_close(self.cscv_logits, expected.cscv_logits)
        ):
            raise ValueError("invalid intraday overfit statistics")
        return self


class IntradayOverfitDiagnosticsPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    diagnostics_version: Literal["intraday_overfit_diagnostics_v1"]
    reviewed_at: dt.datetime
    data_version: str
    manifest_sha256: str
    evaluator_version: str
    side_cost_bps: int = Field(ge=20, le=100)
    statistics: IntradayOverfitStatistics
    automatic_state_change_allowed: Literal[False] = False
    order_authority_change_allowed: Literal[False] = False
    allocation_change_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if (
            not _aware(self.reviewed_at)
            or _HEX64.fullmatch(self.data_version) is None
            or _HEX64.fullmatch(self.manifest_sha256) is None
            or _IDENTIFIER.fullmatch(self.evaluator_version) is None
        ):
            raise InvalidIntradayOverfitDiagnosticsError
        return self


class IntradayOverfitDiagnosticsArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    payload: IntradayOverfitDiagnosticsPayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if self.artifact_id != expected:
            raise InvalidIntradayOverfitDiagnosticsError
        return self


@dataclass(frozen=True, slots=True)
class _DerivedStatistics:
    status: IntradayOverfitDiagnosticsStatus
    blockers: tuple[str, ...]
    selected_strategy_version: str | None = None
    sharpe_estimates: tuple[IntradayOverfitSharpeEstimate, ...] = ()
    expected_max_sharpe: float | None = None
    deflated_sharpe_probability: float | None = None
    cscv_partitions: int | None = None
    cscv_logits: tuple[float, ...] = ()
    pbo_probability: float | None = None


def calculate_intraday_overfit_statistics(
    candidates: tuple[IntradayOverfitCandidateTrace, ...],
    *,
    total_lane_historical_trials: int,
) -> IntradayOverfitStatistics:
    checked = tuple(
        IntradayOverfitCandidateTrace.model_validate(candidate.model_dump())
        for candidate in candidates
    )
    derived = _derive_statistics(checked, total_lane_historical_trials)
    return IntradayOverfitStatistics(
        candidates=checked,
        total_lane_historical_trials=total_lane_historical_trials,
        status=derived.status,
        blockers=derived.blockers,
        selected_strategy_version=derived.selected_strategy_version,
        sharpe_estimates=derived.sharpe_estimates,
        expected_max_sharpe=derived.expected_max_sharpe,
        deflated_sharpe_probability=derived.deflated_sharpe_probability,
        cscv_partitions=derived.cscv_partitions,
        cscv_logits=derived.cscv_logits,
        pbo_probability=derived.pbo_probability,
    )


def _derive_statistics(
    candidates: tuple[IntradayOverfitCandidateTrace, ...],
    total_lane_historical_trials: int,
) -> _DerivedStatistics:
    blockers: list[str] = []
    if len(candidates) < OVERFIT_MIN_VARIANTS:
        blockers.append(f"minimum_candidate_variants:{len(candidates)}/{OVERFIT_MIN_VARIANTS}")
    for candidate in candidates:
        if candidate.trace_schema_version != 2:
            blockers.append(f"outcome_trace_schema_v2_required:{candidate.strategy_version}")
        if candidate.trade_count < OVERFIT_MIN_TRADES:
            blockers.append(
                f"minimum_comparison_trades:{candidate.strategy_version}:"
                f"{candidate.trade_count}/{OVERFIT_MIN_TRADES}"
            )
    traced = tuple(candidate for candidate in candidates if candidate.trace_schema_version == 2)
    session_count = len(traced[0].session_dates) if traced else 0
    if traced and any(candidate.session_dates != traced[0].session_dates for candidate in traced[1:]):
        blockers.append("synchronous_session_dates_required")
    elif traced and session_count < OVERFIT_MIN_SESSIONS:
        blockers.append(f"minimum_synchronous_sessions:{session_count}/{OVERFIT_MIN_SESSIONS}")
    if total_lane_historical_trials < len(candidates):
        raise InvalidIntradayOverfitDiagnosticsError
    if blockers:
        return _collecting(blockers)

    estimates: list[IntradayOverfitSharpeEstimate] = []
    for candidate in candidates:
        sharpe = _sharpe(candidate.net_session_returns)
        if sharpe is None:
            blockers.append(f"return_variation_required:{candidate.strategy_version}")
        else:
            estimates.append(
                IntradayOverfitSharpeEstimate(
                    strategy_version=candidate.strategy_version,
                    sharpe_ratio=sharpe,
                )
            )
    if blockers:
        return _collecting(blockers)

    ordered_estimates = tuple(sorted(estimates, key=lambda item: item.strategy_version))
    best = max(item.sharpe_ratio for item in ordered_estimates)
    selected = tuple(
        item.strategy_version
        for item in ordered_estimates
        if math.isclose(item.sharpe_ratio, best, rel_tol=1e-12, abs_tol=1e-12)
    )
    partitions = _cscv_partition_count(session_count)
    if len(selected) != 1:
        blockers.append("unique_full_sample_selection_required")
    if partitions is None:
        blockers.append("equal_cscv_partitions_required")
    if blockers or partitions is None:
        return _collecting(blockers)

    logits = _cscv_logits(candidates, partitions)
    if logits is None:
        return _collecting(("cscv_sharpe_selection_required",))
    selected_version = selected[0]
    selected_candidate = next(
        candidate for candidate in candidates if candidate.strategy_version == selected_version
    )
    expected_max = _expected_max_sharpe(
        tuple(item.sharpe_ratio for item in ordered_estimates),
        total_lane_historical_trials,
    )
    probability = _deflated_sharpe_probability(
        selected_candidate.net_session_returns,
        expected_max,
    )
    if probability is None:
        return _collecting(("deflated_sharpe_denominator_required",))
    return _DerivedStatistics(
        status=IntradayOverfitDiagnosticsStatus.DIAGNOSTIC_READY,
        blockers=(),
        selected_strategy_version=selected_version,
        sharpe_estimates=ordered_estimates,
        expected_max_sharpe=expected_max,
        deflated_sharpe_probability=probability,
        cscv_partitions=partitions,
        cscv_logits=logits,
        pbo_probability=sum(value < 0.0 for value in logits) / len(logits),
    )


def _collecting(blockers: list[str] | tuple[str, ...]) -> _DerivedStatistics:
    return _DerivedStatistics(
        status=IntradayOverfitDiagnosticsStatus.COLLECTING,
        blockers=tuple(sorted(set(blockers))),
    )


def _sharpe(values: tuple[float, ...]) -> float | None:
    if len(values) < 2:
        return None
    deviation = statistics.stdev(values)
    if math.isclose(deviation, 0.0, abs_tol=1e-15):
        return None
    return statistics.fmean(values) / deviation


def _expected_max_sharpe(
    sharpe_ratios: tuple[float, ...],
    total_trials: int,
) -> float:
    mean = statistics.fmean(sharpe_ratios)
    sigma = math.sqrt(
        statistics.fmean((value - mean) ** 2 for value in sharpe_ratios)
    )
    normal = NormalDist()
    expected_standard_maximum = (
        (1.0 - EULER_MASCHERONI)
        * normal.inv_cdf(1.0 - 1.0 / total_trials)
        + EULER_MASCHERONI
        * normal.inv_cdf(1.0 - 1.0 / (total_trials * math.e))
    )
    return sigma * expected_standard_maximum


def _deflated_sharpe_probability(
    values: tuple[float, ...],
    rejection_threshold: float,
) -> float | None:
    sharpe = _sharpe(values)
    if sharpe is None:
        return None
    mean = statistics.fmean(values)
    population_deviation = math.sqrt(
        statistics.fmean((value - mean) ** 2 for value in values)
    )
    if math.isclose(population_deviation, 0.0, abs_tol=1e-15):
        return None
    skewness = statistics.fmean(
        ((value - mean) / population_deviation) ** 3
        for value in values
    )
    kurtosis = statistics.fmean(
        ((value - mean) / population_deviation) ** 4
        for value in values
    )
    denominator_squared = (
        1.0
        - skewness * sharpe
        + ((kurtosis - 1.0) / 4.0) * sharpe**2
    )
    if denominator_squared <= 0.0:
        return None
    statistic = (
        (sharpe - rejection_threshold)
        * math.sqrt(len(values) - 1)
        / math.sqrt(denominator_squared)
    )
    return NormalDist().cdf(statistic)


def _cscv_partition_count(session_count: int) -> int | None:
    maximum = min(OVERFIT_MAX_CSCV_PARTITIONS, session_count // OVERFIT_MIN_BLOCK_SESSIONS)
    values = tuple(
        partitions
        for partitions in range(4, maximum + 1, 2)
        if session_count % partitions == 0
        and comb(partitions, partitions // 2) <= OVERFIT_MAX_CSCV_COMBINATIONS
    )
    return None if not values else values[-1]


def _cscv_logits(
    candidates: tuple[IntradayOverfitCandidateTrace, ...],
    partitions: int,
) -> tuple[float, ...] | None:
    session_count = len(candidates[0].net_session_returns)
    block_size = session_count // partitions
    blocks = tuple(
        tuple(range(start, start + block_size))
        for start in range(0, session_count, block_size)
    )
    logits: list[float] = []
    for selected_blocks in combinations(range(partitions), partitions // 2):
        selected_set = frozenset(selected_blocks)
        in_sample = tuple(index for block in selected_blocks for index in blocks[block])
        out_sample = tuple(
            index
            for block in range(partitions)
            if block not in selected_set
            for index in blocks[block]
        )
        in_scores = tuple(
            _sharpe(tuple(candidate.net_session_returns[index] for index in in_sample))
            for candidate in candidates
        )
        out_scores = tuple(
            _sharpe(tuple(candidate.net_session_returns[index] for index in out_sample))
            for candidate in candidates
        )
        if any(value is None for value in (*in_scores, *out_scores)):
            return None
        checked_in = tuple(float(value) for value in in_scores if value is not None)
        checked_out = tuple(float(value) for value in out_scores if value is not None)
        maximum = max(checked_in)
        winners = tuple(
            index
            for index, score in enumerate(checked_in)
            if math.isclose(score, maximum, rel_tol=1e-12, abs_tol=1e-12)
        )
        if len(winners) != 1:
            return None
        selected_index = winners[0]
        selected_score = checked_out[selected_index]
        tied = sum(
            math.isclose(score, selected_score, rel_tol=1e-12, abs_tol=1e-12)
            for score in checked_out
        )
        if tied != 1:
            return None
        rank = 1 + sum(score < selected_score for score in checked_out)
        relative_rank = rank / (len(candidates) + 1.0)
        logits.append(math.log(relative_rank / (1.0 - relative_rank)))
    return tuple(logits)


def _sharpe_estimates_match(
    actual: tuple[IntradayOverfitSharpeEstimate, ...],
    expected: tuple[IntradayOverfitSharpeEstimate, ...],
) -> bool:
    return len(actual) == len(expected) and all(
        left.strategy_version == right.strategy_version
        and math.isclose(
            left.sharpe_ratio,
            right.sharpe_ratio,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        for left, right in zip(actual, expected, strict=True)
    )


def _float_tuples_close(
    actual: tuple[float, ...],
    expected: tuple[float, ...],
) -> bool:
    return len(actual) == len(expected) and all(
        math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
        for left, right in zip(actual, expected, strict=True)
    )


def _optional_close(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return (
        math.isfinite(actual)
        and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "INTRADAY_OVERFIT_DIAGNOSTICS_VERSION",
    "OVERFIT_MIN_SESSIONS",
    "OVERFIT_MIN_TRADES",
    "OVERFIT_MIN_VARIANTS",
    "IntradayOverfitCandidateTrace",
    "IntradayOverfitDiagnosticsArtifact",
    "IntradayOverfitDiagnosticsPayload",
    "IntradayOverfitDiagnosticsStatus",
    "IntradayOverfitSharpeEstimate",
    "IntradayOverfitStatistics",
    "InvalidIntradayOverfitDiagnosticsError",
    "calculate_intraday_overfit_statistics",
)
