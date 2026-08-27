import hashlib
from typing import assert_never

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
    verdict_id,
)
from trading_agent.kr_autonomous_trade_proposal import precritic_no_trade_reasons, propose_kr_autonomous_trade


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
    reasons = precritic_no_trade_reasons(request)
    if reasons:
        return _no_trade(request, reasons)
    proposal, failure = propose_kr_autonomous_trade(request)
    if proposal is None:
        return _no_trade(request, (failure or KrNoTradeReason.INVALID_STOP,))
    return finalize_kr_autonomous_trade(request, proposal)


def finalize_kr_autonomous_trade(
    request: KrAutonomousTradeRequest, proposal: KrAutonomousTradeProposal, plan_id: str | None = None
) -> KrAutonomousTradeEvent:
    trusted = revalidate_kr_autonomous_trade_request(request)
    expected, _ = propose_kr_autonomous_trade(trusted) if trusted is not None else (None, None)
    if trusted is None or expected != proposal:
        raise InvalidKrAutonomousTradeError
    request = trusted
    plan_id = plan_id or _plan_id(request)
    verdict = _critic(request, proposal)
    match verdict.status:
        case KrAutonomousCriticStatus.APPROVED:
            return _recommendation(request, proposal, verdict, plan_id)
        case KrAutonomousCriticStatus.MORE_RESEARCH:
            return _no_trade(request, (KrNoTradeReason.INVALID_STOP,), plan_id)
        case KrAutonomousCriticStatus.REJECTED:
            return _rejected(request, verdict, plan_id)
        case unreachable:
            assert_never(unreachable)


def no_trade_kr_autonomous_trade(
    request: KrAutonomousTradeRequest, reasons: tuple[KrNoTradeReason, ...], plan_id: str | None = None
) -> KrAutonomousNoTrade:
    return _no_trade(request, reasons, plan_id or _plan_id(request))


def criticize_kr_autonomous_trade(request: KrAutonomousTradeRequest) -> KrAutonomousCriticVerdict:
    trusted = revalidate_kr_autonomous_trade_request(request)
    if trusted is None:
        context = project_kr_trade_rejection_context(request)
        if context is None:
            raise InvalidKrAutonomousTradeError from None
        return build_kr_integrity_verdict(context)
    request = trusted
    proposal, _ = propose_kr_autonomous_trade(request)
    return _critic(request, proposal)


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
    plan_id: str,
) -> KrTradeRecommendation:
    draft = KrTradeRecommendation.model_construct(
        event_id="",
        plan_id=plan_id,
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


def _no_trade(
    request: KrTradeEventContext, reasons: tuple[KrNoTradeReason, ...], plan_id: str | None = None
) -> KrAutonomousNoTrade:
    draft = KrAutonomousNoTrade.model_construct(
        event_id="",
        plan_id=plan_id or _plan_id(request),
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


def _rejected(
    request: KrTradeEventContext, verdict: KrAutonomousCriticVerdict, plan_id: str | None = None
) -> KrAutonomousRejected:
    draft = KrAutonomousRejected.model_construct(
        event_id="",
        plan_id=plan_id or _plan_id(request),
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


def _plan_id(request: KrTradeEventContext) -> str:
    return hashlib.sha256(request.model_dump_json().encode("utf-8")).hexdigest()
