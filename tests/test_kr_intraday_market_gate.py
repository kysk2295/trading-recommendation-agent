from __future__ import annotations

import datetime as dt
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_agent.kr_intraday_market_gate import (
    KrDesignationState,
    KrHaltState,
    KrIntradayGateReason,
    KrIntradayGateStatus,
    KrMarketConstraintSnapshot,
    KrSessionState,
    KrTradingMode,
    KrViState,
    assess_kr_shadow_entry,
)
from trading_agent.signal_contract_models import EvidenceRef

_OBSERVED = dt.datetime(2026, 7, 20, 1, 5, tzinfo=dt.UTC)


def test_shadow_entry_is_eligible_when_every_kr_constraint_is_observed_clear() -> None:
    snapshot = _snapshot()

    result = assess_kr_shadow_entry(snapshot, _OBSERVED + dt.timedelta(seconds=4))

    assert result.status is KrIntradayGateStatus.ELIGIBLE
    assert result.reasons == ()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("session_state", KrSessionState.UNKNOWN, KrIntradayGateReason.SESSION_UNKNOWN),
        ("session_state", KrSessionState.CLOSED, KrIntradayGateReason.SESSION_CLOSED),
        ("vi_state", KrViState.UNKNOWN, KrIntradayGateReason.VI_UNKNOWN),
        ("vi_state", KrViState.STATIC_ACTIVE, KrIntradayGateReason.VI_ACTIVE),
        ("trading_mode", KrTradingMode.UNKNOWN, KrIntradayGateReason.TRADING_MODE_UNKNOWN),
        ("trading_mode", KrTradingMode.CALL_AUCTION, KrIntradayGateReason.CALL_AUCTION),
        ("halt_state", KrHaltState.UNKNOWN, KrIntradayGateReason.HALT_UNKNOWN),
        ("halt_state", KrHaltState.HALTED, KrIntradayGateReason.HALTED),
        ("designation_state", KrDesignationState.UNKNOWN, KrIntradayGateReason.DESIGNATION_UNKNOWN),
        ("designation_state", KrDesignationState.WARNING, KrIntradayGateReason.DESIGNATED),
    ),
)
def test_shadow_entry_blocks_unknown_or_active_market_constraint(
    field: str,
    value: KrSessionState | KrViState | KrTradingMode | KrHaltState | KrDesignationState,
    reason: KrIntradayGateReason,
) -> None:
    snapshot = _snapshot().model_copy(update={field: value})

    result = assess_kr_shadow_entry(snapshot, _OBSERVED + dt.timedelta(seconds=1))

    assert result.status is KrIntradayGateStatus.BLOCKED
    assert reason in result.reasons


def test_shadow_entry_blocks_near_upper_limit_from_raw_prices() -> None:
    snapshot = _snapshot().model_copy(update={"last_price": Decimal("12700")})

    result = assess_kr_shadow_entry(snapshot, _OBSERVED + dt.timedelta(seconds=1))

    assert result.reasons == (KrIntradayGateReason.NEAR_UPPER_LIMIT,)


def test_shadow_entry_blocks_stale_future_and_unusable_quotes() -> None:
    stale = assess_kr_shadow_entry(_snapshot(), _OBSERVED + dt.timedelta(seconds=6))
    future = assess_kr_shadow_entry(_snapshot(), _OBSERVED - dt.timedelta(microseconds=1))
    missing = assess_kr_shadow_entry(
        _snapshot().model_copy(update={"bid_price": None}),
        _OBSERVED,
    )
    crossed = assess_kr_shadow_entry(
        _snapshot().model_copy(update={"bid_price": Decimal("10100"), "ask_price": Decimal("10000")}),
        _OBSERVED,
    )

    assert stale.reasons == (KrIntradayGateReason.STALE_EVIDENCE,)
    assert future.reasons == (KrIntradayGateReason.FUTURE_EVIDENCE,)
    assert missing.reasons == (KrIntradayGateReason.QUOTE_MISSING,)
    assert crossed.reasons == (KrIntradayGateReason.CROSSED_QUOTE,)


def test_constraint_snapshot_is_frozen_and_requires_canonical_evidence() -> None:
    snapshot = _snapshot()

    with pytest.raises((FrozenInstanceError, ValidationError)):
        snapshot.last_price = Decimal("1")
    with pytest.raises(ValidationError):
        _ = KrMarketConstraintSnapshot.model_validate(snapshot.model_dump(mode="python") | {"evidence_refs": ()})


def _snapshot() -> KrMarketConstraintSnapshot:
    return KrMarketConstraintSnapshot(
        symbol="005930",
        observed_at=_OBSERVED,
        previous_close=Decimal("10000"),
        last_price=Decimal("10500"),
        bid_price=Decimal("10490"),
        ask_price=Decimal("10500"),
        lower_limit_price=Decimal("7000"),
        upper_limit_price=Decimal("13000"),
        session_state=KrSessionState.OPEN,
        vi_state=KrViState.CLEAR,
        trading_mode=KrTradingMode.CONTINUOUS,
        halt_state=KrHaltState.CLEAR,
        designation_state=KrDesignationState.CLEAR,
        evidence_refs=(
            EvidenceRef(namespace="quote/kis-kr", record_id="quote-1", observed_at=_OBSERVED),
            EvidenceRef(namespace="status/ls-kr", record_id="status-1", observed_at=_OBSERVED),
        ),
    )
