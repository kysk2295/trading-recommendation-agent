from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tests.test_kr_autonomous_market_service import NOW, _calendar, _receipts
from tests.test_kr_social_signal_store import _signal
from trading_agent.kr_autonomous_market_models import KrAutonomousMarketCorroboration, corroboration_id
from trading_agent.kr_autonomous_market_service import KrCorroborationProjectionInput, project_kr_corroboration
from trading_agent.kr_autonomous_trade_models import (
    InvalidKrAutonomousTradeError,
    KrAutonomousNoTrade,
    KrAutonomousRejected,
    KrAutonomousSetupKind,
    KrAutonomousTradeOutcome,
    KrAutonomousTradeRequest,
    KrAutonomousTradeThesis,
    KrCriticReason,
    KrNoTradeReason,
    KrOpenVirtualExposure,
    KrTradeRecommendation,
    thesis_id,
)
from trading_agent.kr_autonomous_trade_planner import criticize_kr_autonomous_trade, plan_kr_autonomous_trade


def _request(*, verified: bool = True) -> KrAutonomousTradeRequest:
    signal = (
        _signal()
        if verified
        else _signal().model_copy(
            update={
                "independent_source_cluster_ids": (_signal().independent_source_cluster_ids[0],),
                "independent_source_count": 1,
            }
        )
    )
    if not verified:
        from trading_agent.kr_social_signal_models import KrSocialSignal, KrSocialVerificationState, _signal_id

        signal = signal.model_copy(update={"verification_state": KrSocialVerificationState.UNVERIFIED_SOCIAL})
        signal = KrSocialSignal.model_validate(
            signal.model_copy(update={"signal_id": _signal_id(signal)}).model_dump(mode="python")
        )
    market = project_kr_corroboration(
        KrCorroborationProjectionInput(
            signal=signal, calendar_snapshot=_calendar(), receipts=_receipts(), observed_at=NOW
        )
    )
    thesis = KrAutonomousTradeThesis.model_construct(
        thesis_id="",
        task_id=signal.task_id,
        symbol=signal.symbol,
        theme=signal.theme,
        hypothesis="Current independent evidence supports a bounded continuation setup.",
        counterevidence=("The observed response may lose its completed-bar low.",),
        setup_kind=KrAutonomousSetupKind.MOMENTUM_RECLAIM,
        social_signal_id=signal.signal_id,
        market_corroboration_id=market.corroboration_id,
        evidence_refs=tuple(sorted({*signal.evidence_ids, *market.evidence_ids})),
        submitted_at=NOW,
    )
    thesis = KrAutonomousTradeThesis.model_validate(
        thesis.model_copy(update={"thesis_id": thesis_id(thesis)}).model_dump(mode="python")
    )
    return KrAutonomousTradeRequest(
        thesis=thesis,
        social_signal=signal,
        market=market,
        evaluated_at=NOW,
        next_wake_at=NOW + dt.timedelta(minutes=1),
        open_exposures=(),
        previous_event_id=None,
    )


def test_verified_plan_uses_ask_completed_bar_grid_and_budgets() -> None:
    # Given: verified causal social evidence and fresh current-session market truth.
    request = _request()

    # When: the deterministic planner evaluates it.
    result = plan_kr_autonomous_trade(request)

    # Then: only bounded market facts determine grid levels and sizing.
    assert isinstance(result, KrTradeRecommendation)
    assert result.outcome is KrAutonomousTradeOutcome.RECOMMEND
    assert (result.entry, result.stop, result.targets) == (
        Decimal("103"),
        Decimal("101"),
        (Decimal("105"), Decimal("107")),
    )
    assert result.quantity == 9708
    assert result.virtual_only is True
    assert result.trading_authority is False
    assert result.social_signal_id == request.social_signal.signal_id
    assert result.market_corroboration_id == request.market.corroboration_id
    assert result.evidence_refs == request.thesis.evidence_refs
    assert result.critic_verdict.verdict_id == result.critic_verdict_id
    assert result.critic_verdict.thesis_id == result.thesis_id
    assert result.critic_verdict.proposal_id == result.proposal_id


def test_unverified_plan_uses_reduced_risk_and_notional_budget() -> None:
    # Given: a single-independent-source signal with otherwise admissible evidence.
    request = _request(verified=False)

    # When: the planner sizes the virtual recommendation.
    result = plan_kr_autonomous_trade(request)

    # Then: the unverified risk budget, not model prose, limits quantity.
    assert isinstance(result, KrTradeRecommendation)
    assert result.quantity == 2500


def test_max_virtual_notional_caps_quantity() -> None:
    # Given: a risk-based quantity above the verified notional allowance.
    request = _request()

    # When: the planner sizes the virtual recommendation.
    result = plan_kr_autonomous_trade(request)

    # Then: verified notional is capped at one million KRW.
    assert isinstance(result, KrTradeRecommendation)
    assert result.quantity == 9708


def test_duplicate_symbol_or_theme_yields_explicit_no_trade_wake() -> None:
    # Given: an open virtual exposure duplicates both candidate identity dimensions.
    request = _request().model_copy(
        update={"open_exposures": (KrOpenVirtualExposure(symbol="005930", theme="Semiconductor demand"),)}
    )

    # When: the planner evaluates duplicate exposure.
    result = plan_kr_autonomous_trade(request)

    # Then: it emits no levels or quantity and preserves an explicit wake.
    assert isinstance(result, KrAutonomousNoTrade)
    assert result.outcome is KrAutonomousTradeOutcome.NO_TRADE
    assert result.reason_codes == (KrNoTradeReason.DUPLICATE_SYMBOL, KrNoTradeReason.DUPLICATE_THEME)
    assert result.next_wake_at == request.next_wake_at
    assert "entry" not in type(result).model_fields and "quantity" not in type(result).model_fields


def test_stale_or_missing_spread_yields_no_trade() -> None:
    # Given: an evaluation beyond validity and a missing-spread marker in separate requests.
    stale = _request().model_copy(update={"evaluated_at": NOW + dt.timedelta(seconds=5)})
    missing_request = _request()
    missing = missing_request.model_copy(
        update={"market": missing_request.market.model_copy(update={"spread_bps": Decimal("-1")})}
    )

    # When: each unsafe market condition is planned.
    stale_result = plan_kr_autonomous_trade(stale)
    missing_result = plan_kr_autonomous_trade(missing)

    # Then: both fail closed as explicit no-trade artifacts.
    assert isinstance(stale_result, KrAutonomousNoTrade)
    assert stale_result.reason_codes == (KrNoTradeReason.STALE_MARKET,)
    assert isinstance(missing_result, KrAutonomousNoTrade)
    assert missing_result.reason_codes == (KrNoTradeReason.MISSING_SPREAD,)


def test_zero_quantity_yields_no_trade() -> None:
    # Given: a current ask above the maximum virtual notional allowance.
    request = _request()
    bid = Decimal("1999000")
    ask = Decimal("2000000")
    snapshot = request.market.market_snapshot.model_copy(
        update={
            "previous_close": Decimal("1800000"),
            "last_price": Decimal("2000000"),
            "bid_price": bid,
            "ask_price": ask,
            "lower_limit_price": Decimal("1000000"),
            "upper_limit_price": Decimal("3000000"),
        }
    )
    bar = request.market.latest_completed_bar.model_copy(
        update={
            "open": Decimal("1900000"),
            "high": Decimal("2000000"),
            "low": Decimal("1900000"),
            "close": Decimal("1900000"),
            "trading_value_krw": Decimal("190000000"),
        }
    )
    spread = (ask - bid) / ((bid + ask) / 2) * 10000
    market = request.market.model_copy(
        update={
            "market_snapshot": snapshot,
            "latest_completed_bar": bar,
            "spread_bps": spread,
            "trading_value_krw": bar.trading_value_krw,
        }
    )
    market = market.model_copy(update={"corroboration_id": corroboration_id(market)})
    market = KrAutonomousMarketCorroboration.model_validate(market.model_dump(mode="python"))
    thesis = request.thesis.model_copy(update={"market_corroboration_id": market.corroboration_id})
    thesis = thesis.model_copy(update={"thesis_id": thesis_id(thesis)})

    # When: deterministic sizing floors the bounded quantity.
    result = plan_kr_autonomous_trade(request.model_copy(update={"market": market, "thesis": thesis}))

    # Then: zero shares cannot become a recommendation.
    assert isinstance(result, KrAutonomousNoTrade)
    assert result.reason_codes == (KrNoTradeReason.ZERO_QUANTITY,)


def test_forged_nested_ask_is_rejected_before_planning() -> None:
    # Given: model_copy bypasses nested corroboration identity validation and changes the ask.
    request = _request()
    snapshot = request.market.market_snapshot.model_copy(update={"ask_price": Decimal("104")})
    forged_market = request.market.model_copy(update={"market_snapshot": snapshot})

    # When: the forged request crosses the public planning boundary.
    result = plan_kr_autonomous_trade(request.model_copy(update={"market": forged_market}))

    # Then: the stale content address is rejected instead of producing entry 104.
    assert isinstance(result, KrAutonomousRejected)
    assert result.reason_codes == (KrCriticReason.EVIDENCE_LINEAGE,)


def test_missing_nested_market_returns_rejection_without_exception() -> None:
    # Given: a model_copy request whose nested market artifact is missing entirely.
    request = _request().model_copy(update={"market": None})

    # When: the malformed request crosses the public planning boundary.
    result = plan_kr_autonomous_trade(request)

    # Then: integrity rejection does not dereference the missing market.
    assert isinstance(result, KrAutonomousRejected)
    assert result.reason_codes == (KrCriticReason.EVIDENCE_LINEAGE,)


def test_missing_thesis_fails_with_stable_domain_error() -> None:
    # Given: model_construct bypasses the required thesis boundary entirely.
    request = _request().model_copy(update={"thesis": None})

    # When/Then: neither public boundary leaks an AttributeError or fabricates identity.
    with pytest.raises(InvalidKrAutonomousTradeError):
        _ = plan_kr_autonomous_trade(request)
    with pytest.raises(InvalidKrAutonomousTradeError):
        _ = criticize_kr_autonomous_trade(request)
