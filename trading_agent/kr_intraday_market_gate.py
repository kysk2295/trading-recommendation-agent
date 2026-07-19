from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_instrument import is_kr_instrument_symbol_v2
from trading_agent.signal_contract_models import EvidenceRef

_MAX_EVIDENCE_AGE = dt.timedelta(seconds=5)
_NEAR_UPPER_LIMIT_RETURN = Decimal("0.27")


class KrIntradayMarketGateError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR intraday market gate input is invalid"


class KrSessionState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class KrViState(StrEnum):
    CLEAR = "clear"
    STATIC_ACTIVE = "static_active"
    DYNAMIC_ACTIVE = "dynamic_active"
    UNKNOWN = "unknown"


class KrTradingMode(StrEnum):
    CONTINUOUS = "continuous"
    CALL_AUCTION = "call_auction"
    UNKNOWN = "unknown"


class KrHaltState(StrEnum):
    CLEAR = "clear"
    HALTED = "halted"
    UNKNOWN = "unknown"


class KrDesignationState(StrEnum):
    CLEAR = "clear"
    CAUTION = "caution"
    WARNING = "warning"
    DANGER = "danger"
    UNKNOWN = "unknown"


class KrIntradayGateStatus(StrEnum):
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"


class KrIntradayGateReason(StrEnum):
    FUTURE_EVIDENCE = "future_evidence"
    STALE_EVIDENCE = "stale_evidence"
    SESSION_CLOSED = "session_closed"
    SESSION_UNKNOWN = "session_unknown"
    VI_ACTIVE = "vi_active"
    VI_UNKNOWN = "vi_unknown"
    CALL_AUCTION = "call_auction"
    TRADING_MODE_UNKNOWN = "trading_mode_unknown"
    HALTED = "halted"
    HALT_UNKNOWN = "halt_unknown"
    DESIGNATED = "designated"
    DESIGNATION_UNKNOWN = "designation_unknown"
    UPPER_LIMIT = "upper_limit"
    NEAR_UPPER_LIMIT = "near_upper_limit"
    LOWER_LIMIT = "lower_limit"
    QUOTE_MISSING = "quote_missing"
    CROSSED_QUOTE = "crossed_quote"


class KrMarketConstraintSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    symbol: str
    observed_at: dt.datetime
    previous_close: Decimal
    last_price: Decimal
    bid_price: Decimal | None
    ask_price: Decimal | None
    lower_limit_price: Decimal
    upper_limit_price: Decimal
    session_state: KrSessionState
    vi_state: KrViState
    trading_mode: KrTradingMode
    halt_state: KrHaltState
    designation_state: KrDesignationState
    evidence_refs: tuple[EvidenceRef, ...]

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        prices = (
            self.previous_close,
            self.last_price,
            self.lower_limit_price,
            self.upper_limit_price,
        )
        quote_prices = tuple(price for price in (self.bid_price, self.ask_price) if price is not None)
        evidence_ids = tuple(item.canonical_id for item in self.evidence_refs)
        if (
            not is_kr_instrument_symbol_v2(self.symbol)
            or not _aware(self.observed_at)
            or any(not _positive_finite(price) for price in (*prices, *quote_prices))
            or not self.lower_limit_price < self.previous_close < self.upper_limit_price
            or not self.evidence_refs
            or evidence_ids != tuple(sorted(set(evidence_ids)))
            or any(item.observed_at > self.observed_at for item in self.evidence_refs)
        ):
            raise KrIntradayMarketGateError
        return self


@dataclass(frozen=True, slots=True)
class KrIntradayGateResult:
    status: KrIntradayGateStatus
    reasons: tuple[KrIntradayGateReason, ...]


def assess_kr_shadow_entry(
    snapshot: KrMarketConstraintSnapshot,
    evaluated_at: dt.datetime,
) -> KrIntradayGateResult:
    try:
        current = KrMarketConstraintSnapshot.model_validate(snapshot.model_dump(mode="python"))
        if type(snapshot) is not KrMarketConstraintSnapshot or not _aware(evaluated_at):
            raise KrIntradayMarketGateError
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise KrIntradayMarketGateError from None
    reasons: list[KrIntradayGateReason] = []
    age = evaluated_at - current.observed_at
    if age < dt.timedelta(0):
        reasons.append(KrIntradayGateReason.FUTURE_EVIDENCE)
    elif age > _MAX_EVIDENCE_AGE:
        reasons.append(KrIntradayGateReason.STALE_EVIDENCE)
    _session_reasons(current.session_state, reasons)
    _vi_reasons(current.vi_state, reasons)
    _mode_reasons(current.trading_mode, reasons)
    _halt_reasons(current.halt_state, reasons)
    _designation_reasons(current.designation_state, reasons)
    _price_reasons(current, reasons)
    status = KrIntradayGateStatus.ELIGIBLE if not reasons else KrIntradayGateStatus.BLOCKED
    return KrIntradayGateResult(status, tuple(reasons))


def _session_reasons(state: KrSessionState, reasons: list[KrIntradayGateReason]) -> None:
    match state:
        case KrSessionState.OPEN:
            return
        case KrSessionState.CLOSED:
            reasons.append(KrIntradayGateReason.SESSION_CLOSED)
        case KrSessionState.UNKNOWN:
            reasons.append(KrIntradayGateReason.SESSION_UNKNOWN)
        case unreachable:
            assert_never(unreachable)


def _vi_reasons(state: KrViState, reasons: list[KrIntradayGateReason]) -> None:
    match state:
        case KrViState.CLEAR:
            return
        case KrViState.STATIC_ACTIVE | KrViState.DYNAMIC_ACTIVE:
            reasons.append(KrIntradayGateReason.VI_ACTIVE)
        case KrViState.UNKNOWN:
            reasons.append(KrIntradayGateReason.VI_UNKNOWN)
        case unreachable:
            assert_never(unreachable)


def _mode_reasons(state: KrTradingMode, reasons: list[KrIntradayGateReason]) -> None:
    match state:
        case KrTradingMode.CONTINUOUS:
            return
        case KrTradingMode.CALL_AUCTION:
            reasons.append(KrIntradayGateReason.CALL_AUCTION)
        case KrTradingMode.UNKNOWN:
            reasons.append(KrIntradayGateReason.TRADING_MODE_UNKNOWN)
        case unreachable:
            assert_never(unreachable)


def _halt_reasons(state: KrHaltState, reasons: list[KrIntradayGateReason]) -> None:
    match state:
        case KrHaltState.CLEAR:
            return
        case KrHaltState.HALTED:
            reasons.append(KrIntradayGateReason.HALTED)
        case KrHaltState.UNKNOWN:
            reasons.append(KrIntradayGateReason.HALT_UNKNOWN)
        case unreachable:
            assert_never(unreachable)


def _designation_reasons(state: KrDesignationState, reasons: list[KrIntradayGateReason]) -> None:
    match state:
        case KrDesignationState.CLEAR:
            return
        case KrDesignationState.CAUTION | KrDesignationState.WARNING | KrDesignationState.DANGER:
            reasons.append(KrIntradayGateReason.DESIGNATED)
        case KrDesignationState.UNKNOWN:
            reasons.append(KrIntradayGateReason.DESIGNATION_UNKNOWN)
        case unreachable:
            assert_never(unreachable)


def _price_reasons(
    snapshot: KrMarketConstraintSnapshot,
    reasons: list[KrIntradayGateReason],
) -> None:
    if snapshot.last_price >= snapshot.upper_limit_price:
        reasons.append(KrIntradayGateReason.UPPER_LIMIT)
    elif snapshot.last_price <= snapshot.lower_limit_price:
        reasons.append(KrIntradayGateReason.LOWER_LIMIT)
    elif snapshot.last_price / snapshot.previous_close - Decimal(1) >= _NEAR_UPPER_LIMIT_RETURN:
        reasons.append(KrIntradayGateReason.NEAR_UPPER_LIMIT)
    if snapshot.bid_price is None or snapshot.ask_price is None:
        reasons.append(KrIntradayGateReason.QUOTE_MISSING)
    elif snapshot.bid_price > snapshot.ask_price:
        reasons.append(KrIntradayGateReason.CROSSED_QUOTE)


def _positive_finite(value: Decimal) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "KrDesignationState",
    "KrHaltState",
    "KrIntradayGateReason",
    "KrIntradayGateResult",
    "KrIntradayGateStatus",
    "KrIntradayMarketGateError",
    "KrMarketConstraintSnapshot",
    "KrSessionState",
    "KrTradingMode",
    "KrViState",
    "assess_kr_shadow_entry",
)
