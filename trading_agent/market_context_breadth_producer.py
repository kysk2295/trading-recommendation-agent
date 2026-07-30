from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, override

from trading_agent.market_context_models import (
    MarketContextContractError,
    MarketContextSnapshot,
    MarketRegimeLabel,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import FeatureValue, SourceCoverage

_PRODUCER_VERSION: Final = "market-context-breadth-v1"
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,14}$")
_SOURCE_ID: Final = "local_breadth_members"


class MarketContextBreadthProducerError(ValueError):
    @override
    def __str__(self) -> str:
        return "market context breadth producer input is invalid"


@dataclass(frozen=True, slots=True)
class BreadthMemberObservation:
    """One universe member's completed-session return and relative volume proxy."""

    symbol: str
    session_return_bps: int
    relative_volume_bps: int

    def __post_init__(self) -> None:
        if (
            type(self.symbol) is not str
            or _SYMBOL.fullmatch(self.symbol) is None
            or type(self.session_return_bps) is not int
            or type(self.relative_volume_bps) is not int
            or not -100_000 <= self.session_return_bps <= 100_000
            or not 0 <= self.relative_volume_bps <= 1_000_000
        ):
            raise MarketContextBreadthProducerError


def produce_market_context_from_breadth(
    members: tuple[BreadthMemberObservation, ...],
    *,
    market_id: MarketId,
    observed_at: dt.datetime,
    valid_until: dt.datetime,
    source_record_count: int | None = None,
) -> MarketContextSnapshot:
    """Deterministic breadth/vol regime snapshot. No network, orders, or allocation."""
    try:
        if (
            not members
            or len(members) > 5_000
            or len({item.symbol for item in members}) != len(members)
            or type(observed_at) is not dt.datetime
            or type(valid_until) is not dt.datetime
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
            or valid_until.tzinfo is None
            or valid_until.utcoffset() is None
            or valid_until <= observed_at
        ):
            raise MarketContextBreadthProducerError
        ordered = tuple(sorted(members, key=lambda item: item.symbol))
        advances = sum(item.session_return_bps > 0 for item in ordered)
        declines = sum(item.session_return_bps < 0 for item in ordered)
        unchanged = len(ordered) - advances - declines
        advance_decline = _ratio(advances, max(declines, 1))
        up_volume_share = _ratio(
            sum(item.relative_volume_bps for item in ordered if item.session_return_bps > 0),
            max(sum(item.relative_volume_bps for item in ordered), 1),
        )
        median_abs_return = _median_abs_return_bps(ordered)
        regime_labels = _regimes(
            advance_decline=advance_decline,
            median_abs_return_bps=median_abs_return,
            up_volume_share=up_volume_share,
        )
        features = (
            FeatureValue(name="advance_count", value=str(advances)),
            FeatureValue(name="advance_decline_ratio", value=format(advance_decline, "f")),
            FeatureValue(name="decline_count", value=str(declines)),
            FeatureValue(name="median_abs_return_bps", value=str(median_abs_return)),
            FeatureValue(name="member_count", value=str(len(ordered))),
            FeatureValue(name="unchanged_count", value=str(unchanged)),
            FeatureValue(name="up_volume_share", value=format(up_volume_share, "f")),
        )
        context_id = _context_id(market_id, observed_at, ordered)
        record_count = len(ordered) if source_record_count is None else source_record_count
        if type(record_count) is not int or record_count < len(ordered):
            raise MarketContextBreadthProducerError
        return MarketContextSnapshot(
            context_id=context_id,
            market_id=market_id,
            observed_at=observed_at,
            valid_until=valid_until,
            regime_labels=regime_labels,
            breadth_and_volatility_features=features,
            macro_and_flow_refs=(),
            coverage=(
                SourceCoverage(
                    source_id=_SOURCE_ID,
                    observed_at=observed_at,
                    record_count=record_count,
                    complete=True,
                ),
            ),
            producer_version=_PRODUCER_VERSION,
        )
    except (InvalidOperation, MarketContextContractError, TypeError, ValueError):
        raise MarketContextBreadthProducerError from None


def _regimes(
    *,
    advance_decline: Decimal,
    median_abs_return_bps: int,
    up_volume_share: Decimal,
) -> tuple[MarketRegimeLabel, ...]:
    labels: list[MarketRegimeLabel] = []
    if median_abs_return_bps >= 150:
        labels.append(MarketRegimeLabel.HIGH_VOL)
    elif median_abs_return_bps <= 40:
        labels.append(MarketRegimeLabel.LOW_VOL)
    if advance_decline >= Decimal("1.5") and up_volume_share >= Decimal("0.55"):
        labels.append(MarketRegimeLabel.RISK_ON)
        labels.append(MarketRegimeLabel.TRENDING)
    elif advance_decline <= Decimal("0.67") and up_volume_share <= Decimal("0.45"):
        labels.append(MarketRegimeLabel.RISK_OFF)
        labels.append(MarketRegimeLabel.TRENDING)
    elif Decimal("0.85") <= advance_decline <= Decimal("1.15"):
        labels.append(MarketRegimeLabel.MEAN_REVERTING)
    if not labels:
        labels.append(MarketRegimeLabel.UNKNOWN)
    return tuple(sorted(set(labels), key=lambda item: item.value))


def _median_abs_return_bps(members: tuple[BreadthMemberObservation, ...]) -> int:
    values = tuple(sorted(abs(item.session_return_bps) for item in members))
    mid = len(values) // 2
    if len(values) % 2 == 1:
        return values[mid]
    return (values[mid - 1] + values[mid]) // 2


def _ratio(numerator: int, denominator: int) -> Decimal:
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def _context_id(
    market_id: MarketId,
    observed_at: dt.datetime,
    members: tuple[BreadthMemberObservation, ...],
) -> str:
    material = "|".join(
        (
            market_id.value,
            observed_at.astimezone(dt.UTC).isoformat(),
            _PRODUCER_VERSION,
            ",".join(
                f"{item.symbol}:{item.session_return_bps}:{item.relative_volume_bps}"
                for item in members
            ),
        )
    )
    digest = hashlib.sha256(material.encode()).hexdigest()[:24]
    return f"ctx.{market_id.value}.{digest}"


__all__ = (
    "BreadthMemberObservation",
    "MarketContextBreadthProducerError",
    "produce_market_context_from_breadth",
)
