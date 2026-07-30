from __future__ import annotations

import datetime as dt

import pytest

from trading_agent.market_context_breadth_producer import (
    BreadthMemberObservation,
    MarketContextBreadthProducerError,
    produce_market_context_from_breadth,
)
from trading_agent.market_context_models import MarketRegimeLabel
from trading_agent.research_identity_models import MarketId

UTC = dt.UTC
OBSERVED = dt.datetime(2026, 7, 21, 14, 30, tzinfo=UTC)
VALID = OBSERVED + dt.timedelta(minutes=5)


def test_risk_on_high_vol_breadth_snapshot_is_deterministic() -> None:
    members = _members(
        (280, 12_000),
        (260, 11_000),
        (240, 10_500),
        (220, 10_000),
        (200, 9_500),
        (180, 9_000),
        (-20, 3_000),
        (-30, 2_500),
    )
    first = produce_market_context_from_breadth(
        members,
        market_id=MarketId.US_EQUITIES,
        observed_at=OBSERVED,
        valid_until=VALID,
    )
    second = produce_market_context_from_breadth(
        members,
        market_id=MarketId.US_EQUITIES,
        observed_at=OBSERVED,
        valid_until=VALID,
    )

    assert first == second
    assert first.producer_version == "market-context-breadth-v1"
    assert first.order_authority is False
    assert MarketRegimeLabel.HIGH_VOL in first.regime_labels
    assert MarketRegimeLabel.RISK_ON in first.regime_labels
    assert MarketRegimeLabel.TRENDING in first.regime_labels
    assert first.coverage[0].source_id == "local_breadth_members"
    names = tuple(item.name for item in first.breadth_and_volatility_features)
    assert names == tuple(sorted(names))


def test_empty_or_duplicate_members_fail_closed() -> None:
    with pytest.raises(MarketContextBreadthProducerError):
        produce_market_context_from_breadth(
            (),
            market_id=MarketId.US_EQUITIES,
            observed_at=OBSERVED,
            valid_until=VALID,
        )
    member = BreadthMemberObservation("AAPL", 100, 10_000)
    with pytest.raises(MarketContextBreadthProducerError):
        produce_market_context_from_breadth(
            (member, member),
            market_id=MarketId.US_EQUITIES,
            observed_at=OBSERVED,
            valid_until=VALID,
        )


def test_kr_market_context_id_namespace() -> None:
    snapshot = produce_market_context_from_breadth(
        _members((10, 10_000), (-10, 10_000)),
        market_id=MarketId.KR_EQUITIES,
        observed_at=OBSERVED,
        valid_until=VALID,
    )
    assert snapshot.market_id is MarketId.KR_EQUITIES
    assert snapshot.context_id.startswith("ctx.kr_equities.")


def _members(*rows: tuple[int, int]) -> tuple[BreadthMemberObservation, ...]:
    return tuple(
        BreadthMemberObservation(
            symbol=f"S{index:02d}",
            session_return_bps=return_bps,
            relative_volume_bps=volume_bps,
        )
        for index, (return_bps, volume_bps) in enumerate(rows)
    )
