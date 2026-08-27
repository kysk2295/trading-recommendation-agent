from decimal import ROUND_FLOOR, Decimal
from typing import Final, assert_never

from trading_agent.kr_autonomous_trade_boundary import (
    KrTradeEventContext,
    build_kr_integrity_verdict,
    has_missing_kr_trade_spread,
    project_kr_trade_rejection_context,
    revalidate_kr_autonomous_trade_request,
)
from trading_agent.kr_autonomous_trade_models import (
    InvalidKrAutonomousTradeError,
    KrAutonomousCriticStatus,
    KrAutonomousCriticVerdict,
    KrAutonomousNoTrade,
    KrAutonomousRejected,
    KrAutonomousTradeEvent,
    KrAutonomousTradeProposal,
    KrAutonomousTradeRequest,
    KrCriticReason,
    KrNoTradeReason,
    KrTradeRecommendation,
    event_id,
    proposal_id,
    verdict_id,
)
from trading_agent.kr_price_grid import (
    round_kr_equity_price_down,
    round_kr_equity_price_up,
)
from trading_agent.kr_social_signal_models import KrSocialVerificationState

VERIFIED_RISK_KRW: Final = Decimal(25_000)
UNVERIFIED_RISK_KRW: Final = Decimal(5_000)
VERIFIED_MAX_NOTIONAL_KRW: Final = Decimal(1_000_000)
UNVERIFIED_MAX_NOTIONAL_KRW: Final = Decimal(300_000)


def plan_kr_autonomous_trade(request: KrAutonomousTradeRequest) -> KrAutonomousTradeEvent:
    trusted = revalidate_kr_autonomous_trade_request(request)
    if trusted is None:
        context = project_kr_trade_rejection_context(request)
        if context is None:
            raise InvalidKrAutonomousTradeError from None
        if has_missing_kr_trade_spread(request):
            return _no_trade(context, (KrNoTradeReason.MISSING_SPREAD,))
        return _rejected(context, build_kr_integrity_verdict(context))
    request = trusted
    reasons = _no_trade_reasons(request)
    if reasons:
        return _no_trade(request, reasons)
    proposal, failure = _proposal(request)
    if proposal is None:
        return _no_trade(request, (failure or KrNoTradeReason.INVALID_STOP,))
    verdict = _critic(request, proposal)
    match verdict.status:
        case KrAutonomousCriticStatus.APPROVED:
            return _recommendation(request, proposal, verdict)
        case KrAutonomousCriticStatus.MORE_RESEARCH:
            return _no_trade(request, (KrNoTradeReason.INVALID_STOP,))
        case KrAutonomousCriticStatus.REJECTED:
            return _rejected(request, verdict)
        case unreachable:
            assert_never(unreachable)


def criticize_kr_autonomous_trade(request: KrAutonomousTradeRequest) -> KrAutonomousCriticVerdict:
    trusted = revalidate_kr_autonomous_trade_request(request)
    if trusted is None:
        context = project_kr_trade_rejection_context(request)
        if context is None:
            raise InvalidKrAutonomousTradeError from None
        return build_kr_integrity_verdict(context)
    request = trusted
    proposal, _ = _proposal(request)
    return _critic(request, proposal)


def _proposal(
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
    targets = (
        round_kr_equity_price_up(entry + risk_per_share),
        round_kr_equity_price_up(entry + risk_per_share * 2),
    )
    draft = KrAutonomousTradeProposal.model_construct(
        proposal_id="",
        timestamp=request.evaluated_at,
        entry=entry,
        stop=stop,
        targets=targets,
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


def _no_trade_reasons(request: KrAutonomousTradeRequest) -> tuple[KrNoTradeReason, ...]:
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


def _critic(
    request: KrAutonomousTradeRequest,
    proposal: KrAutonomousTradeProposal | None,
) -> KrAutonomousCriticVerdict:
    thesis = request.thesis
    signal = request.social_signal
    market = request.market
    reasons: list[KrCriticReason] = []
    if thesis.task_id != signal.task_id or market.task_id != signal.task_id:
        reasons.append(KrCriticReason.TASK_LINEAGE)
    if (
        thesis.social_signal_id != signal.signal_id
        or market.social_signal_id != signal.signal_id
        or thesis.symbol != signal.symbol
        or thesis.theme != signal.theme
    ):
        reasons.append(KrCriticReason.SOCIAL_LINEAGE)
    if thesis.market_corroboration_id != market.corroboration_id or thesis.symbol != market.symbol:
        reasons.append(KrCriticReason.MARKET_LINEAGE)
    if thesis.evidence_refs != tuple(sorted({*signal.evidence_ids, *market.evidence_ids})):
        reasons.append(KrCriticReason.EVIDENCE_LINEAGE)
    if signal.earliest_published_at is None or signal.earliest_published_at >= market.market_response_at:
        reasons.append(KrCriticReason.NONCAUSAL_PUBLICATION)
    if signal.independent_source_count != len(signal.independent_source_cluster_ids):
        reasons.append(KrCriticReason.CLUSTER_COUNT)
    if request.evaluated_at >= market.valid_until or market.spread_bps < 0:
        reasons.append(KrCriticReason.CURRENT_MARKET)
    if proposal is None:
        reasons.append(KrCriticReason.INVALID_LEVELS)
    if any(
        item.symbol == thesis.symbol or item.theme.casefold() == thesis.theme.casefold()
        for item in request.open_exposures
    ):
        reasons.append(KrCriticReason.DUPLICATE_EXPOSURE)
    normalized_hypothesis = " ".join(thesis.hypothesis.casefold().split())
    if any(" ".join(item.casefold().split()) == normalized_hypothesis for item in thesis.counterevidence):
        reasons.append(KrCriticReason.CONTRADICTORY_EVIDENCE)
    rejection_reasons = {
        KrCriticReason.TASK_LINEAGE,
        KrCriticReason.SOCIAL_LINEAGE,
        KrCriticReason.MARKET_LINEAGE,
        KrCriticReason.EVIDENCE_LINEAGE,
        KrCriticReason.NONCAUSAL_PUBLICATION,
        KrCriticReason.CLUSTER_COUNT,
        KrCriticReason.CONTRADICTORY_EVIDENCE,
    }
    if any(reason in rejection_reasons for reason in reasons):
        status = KrAutonomousCriticStatus.REJECTED
    elif reasons:
        status = KrAutonomousCriticStatus.MORE_RESEARCH
    else:
        status = KrAutonomousCriticStatus.APPROVED
        reasons.append(KrCriticReason.APPROVED)
    draft = KrAutonomousCriticVerdict.model_construct(
        verdict_id="",
        proposal_id=None if proposal is None else proposal.proposal_id,
        thesis_id=thesis.thesis_id,
        status=status,
        reason_codes=tuple(reasons),
    )
    return KrAutonomousCriticVerdict.model_validate(
        draft.model_copy(update={"verdict_id": verdict_id(draft)}).model_dump(mode="python")
    )


def _recommendation(
    request: KrAutonomousTradeRequest,
    proposal: KrAutonomousTradeProposal,
    verdict: KrAutonomousCriticVerdict,
) -> KrTradeRecommendation:
    draft = KrTradeRecommendation.model_construct(
        event_id="",
        previous_event_id=request.previous_event_id,
        timestamp=request.evaluated_at,
        task_id=request.thesis.task_id,
        thesis_id=request.thesis.thesis_id,
        proposal_id=proposal.proposal_id,
        social_signal_id=request.social_signal.signal_id,
        market_corroboration_id=request.market.corroboration_id,
        evidence_refs=request.thesis.evidence_refs,
        symbol=request.thesis.symbol,
        theme=request.thesis.theme,
        entry=proposal.entry,
        stop=proposal.stop,
        targets=proposal.targets,
        quantity=proposal.quantity,
        rationale=proposal.rationale,
        counterevidence=proposal.counterevidence,
        verification_state=proposal.verification_state,
        critic_verdict_id=verdict.verdict_id,
        critic_verdict=verdict,
        valid_until=proposal.valid_until,
    )
    return KrTradeRecommendation.model_validate(
        draft.model_copy(update={"event_id": event_id(draft)}).model_dump(mode="python")
    )


def _no_trade(request: KrTradeEventContext, reasons: tuple[KrNoTradeReason, ...]) -> KrAutonomousNoTrade:
    draft = KrAutonomousNoTrade.model_construct(
        event_id="",
        previous_event_id=request.previous_event_id,
        timestamp=request.evaluated_at,
        task_id=request.thesis.task_id,
        thesis_id=request.thesis.thesis_id,
        symbol=request.thesis.symbol,
        theme=request.thesis.theme,
        reason_codes=reasons,
        next_wake_at=request.next_wake_at,
    )
    return KrAutonomousNoTrade.model_validate(
        draft.model_copy(update={"event_id": event_id(draft)}).model_dump(mode="python")
    )


def _rejected(request: KrTradeEventContext, verdict: KrAutonomousCriticVerdict) -> KrAutonomousRejected:
    draft = KrAutonomousRejected.model_construct(
        event_id="",
        previous_event_id=request.previous_event_id,
        timestamp=request.evaluated_at,
        task_id=request.thesis.task_id,
        thesis_id=request.thesis.thesis_id,
        symbol=request.thesis.symbol,
        theme=request.thesis.theme,
        reason_codes=verdict.reason_codes,
        critic_verdict_id=verdict.verdict_id,
        next_wake_at=request.next_wake_at,
    )
    return KrAutonomousRejected.model_validate(
        draft.model_copy(update={"event_id": event_id(draft)}).model_dump(mode="python")
    )
