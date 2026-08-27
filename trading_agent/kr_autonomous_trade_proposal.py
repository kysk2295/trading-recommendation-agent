from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal
from typing import Final, assert_never

from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousTradeProposal,
    KrAutonomousTradeRequest,
    KrNoTradeReason,
    proposal_id,
)
from trading_agent.kr_price_grid import round_kr_equity_price_down, round_kr_equity_price_up
from trading_agent.kr_social_signal_models import KrSocialVerificationState

VERIFIED_RISK_KRW: Final = Decimal(25_000)
UNVERIFIED_RISK_KRW: Final = Decimal(5_000)
VERIFIED_MAX_NOTIONAL_KRW: Final = Decimal(1_000_000)
UNVERIFIED_MAX_NOTIONAL_KRW: Final = Decimal(300_000)


def precritic_no_trade_reasons(request: KrAutonomousTradeRequest) -> tuple[KrNoTradeReason, ...]:
    reasons: list[KrNoTradeReason] = []
    if any(item.symbol == request.thesis.symbol for item in request.open_exposures):
        reasons.append(KrNoTradeReason.DUPLICATE_SYMBOL)
    if any(item.theme.casefold() == request.thesis.theme.casefold() for item in request.open_exposures):
        reasons.append(KrNoTradeReason.DUPLICATE_THEME)
    if request.evaluated_at >= request.market.valid_until:
        reasons.append(KrNoTradeReason.STALE_MARKET)
    snapshot = request.market.market_snapshot
    if snapshot.bid_price is None or snapshot.ask_price is None or request.market.spread_bps < 0:
        reasons.append(KrNoTradeReason.MISSING_SPREAD)
    return tuple(reasons)


def propose_kr_autonomous_trade(
    request: KrAutonomousTradeRequest,
) -> tuple[KrAutonomousTradeProposal | None, KrNoTradeReason | None]:
    ask = request.market.market_snapshot.ask_price
    if ask is None or not ask.is_finite() or ask <= 0:
        return None, KrNoTradeReason.MISSING_SPREAD
    entry = round_kr_equity_price_up(ask)
    stop = round_kr_equity_price_down(request.market.latest_completed_bar.low)
    if stop >= entry:
        return None, KrNoTradeReason.INVALID_STOP
    risk_per_share = entry - stop
    risk_budget, maximum_notional = _budgets(request.social_signal.verification_state)
    quantity = int(min(risk_budget / risk_per_share, maximum_notional / entry).to_integral_value(rounding=ROUND_FLOOR))
    if quantity <= 0:
        return None, KrNoTradeReason.ZERO_QUANTITY
    draft = KrAutonomousTradeProposal.model_construct(
        proposal_id="",
        timestamp=request.evaluated_at,
        entry=entry,
        stop=stop,
        targets=(
            round_kr_equity_price_up(entry + risk_per_share),
            round_kr_equity_price_up(entry + risk_per_share * 2),
        ),
        quantity=quantity,
        rationale=request.thesis.hypothesis,
        counterevidence=request.thesis.counterevidence,
        verification_state=request.social_signal.verification_state,
        valid_until=request.market.valid_until,
    )
    proposal = KrAutonomousTradeProposal.model_validate(
        draft.model_copy(update={"proposal_id": proposal_id(draft)}).model_dump(mode="python")
    )
    return proposal, None


def _budgets(state: KrSocialVerificationState) -> tuple[Decimal, Decimal]:
    match state:
        case KrSocialVerificationState.MULTI_SOURCE_CORROBORATED:
            return VERIFIED_RISK_KRW, VERIFIED_MAX_NOTIONAL_KRW
        case KrSocialVerificationState.UNVERIFIED_SOCIAL:
            return UNVERIFIED_RISK_KRW, UNVERIFIED_MAX_NOTIONAL_KRW
        case unreachable:
            assert_never(unreachable)
