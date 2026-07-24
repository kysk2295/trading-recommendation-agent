from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Final

from trading_agent.intraday_parameter_plateau_trace_models import (
    IntradayParameterPlateauStatus,
    IntradayParameterPlateauVariantTrace,
)

PARAMETER_PLATEAU_MIN_SESSIONS: Final = 20
PARAMETER_PLATEAU_MIN_TRADES: Final = 30
PARAMETER_PLATEAU_MIN_ELIGIBLE_NEIGHBORS: Final = 4


@dataclass(frozen=True, slots=True)
class IntradayParameterPlateauStatistics:
    status: IntradayParameterPlateauStatus
    blockers: tuple[str, ...]
    observed_sessions: int
    center_trade_count: int
    center_average_return: float | None
    eligible_neighbor_count: int
    positive_neighbor_count: int
    positive_neighbor_rate: float | None
    neighbor_average_return_min: float | None
    neighbor_average_return_median: float | None
    neighbor_average_return_max: float | None


def derive_parameter_plateau_statistics(
    variants: tuple[IntradayParameterPlateauVariantTrace, ...],
) -> IntradayParameterPlateauStatistics:
    center = variants[0]
    eligible = tuple(
        variant.average_return
        for variant in variants[1:]
        if variant.trade_count >= PARAMETER_PLATEAU_MIN_TRADES
        and variant.average_return is not None
    )
    positive = tuple(value for value in eligible if value > 0.0)
    rate = len(positive) / len(eligible) if eligible else None
    blockers: list[str] = []
    if len(center.session_dates) < PARAMETER_PLATEAU_MIN_SESSIONS:
        blockers.append(
            "minimum_synchronous_sessions:"
            f"{len(center.session_dates)}/{PARAMETER_PLATEAU_MIN_SESSIONS}"
        )
    if center.trade_count < PARAMETER_PLATEAU_MIN_TRADES:
        blockers.append(
            "minimum_center_trades:"
            f"{center.trade_count}/{PARAMETER_PLATEAU_MIN_TRADES}"
        )
    if len(eligible) < PARAMETER_PLATEAU_MIN_ELIGIBLE_NEIGHBORS:
        blockers.append(
            "minimum_eligible_neighbors:"
            f"{len(eligible)}/{PARAMETER_PLATEAU_MIN_ELIGIBLE_NEIGHBORS}"
        )
    ready = (
        not blockers
        and center.average_return is not None
        and center.average_return > 0.0
        and rate is not None
        and rate >= 0.75
        and min(eligible) > 0.0
    )
    status = (
        IntradayParameterPlateauStatus.COLLECTING
        if blockers
        else (
            IntradayParameterPlateauStatus.PLATEAU_READY
            if ready
            else IntradayParameterPlateauStatus.PLATEAU_NOT_FOUND
        )
    )
    return IntradayParameterPlateauStatistics(
        status=status,
        blockers=tuple(blockers),
        observed_sessions=len(center.session_dates),
        center_trade_count=center.trade_count,
        center_average_return=center.average_return,
        eligible_neighbor_count=len(eligible),
        positive_neighbor_count=len(positive),
        positive_neighbor_rate=rate,
        neighbor_average_return_min=min(eligible) if eligible else None,
        neighbor_average_return_median=(
            statistics.median(eligible) if eligible else None
        ),
        neighbor_average_return_max=max(eligible) if eligible else None,
    )


__all__ = (
    "IntradayParameterPlateauStatistics",
    "derive_parameter_plateau_statistics",
)
