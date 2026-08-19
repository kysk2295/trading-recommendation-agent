from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from trading_agent.strategy_research_methodologies import strategy_research_methodology
from trading_agent.strategy_research_policy import MethodologyPolicyError
from trading_agent.strategy_research_types import ResearchAgentId, aware
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True)
class SourceAuthorityReceipt:
    authority: str
    source_id: str
    as_of: dt.datetime
    available_at: dt.datetime
    immutable: bool
    complete: bool
    wiring_only: bool = False


@dataclass(frozen=True, slots=True)
class MethodologyObservationInput:
    agent_id: ResearchAgentId
    observed_at: dt.datetime
    source_receipts: tuple[SourceAuthorityReceipt, ...]


@dataclass(frozen=True, slots=True)
class MethodologyObservation:
    agent_id: ResearchAgentId
    source_ids: tuple[str, ...]
    predictor_available_at: dt.datetime
    matures_at: dt.datetime
    observation_grammar: str
    predictor_grammar: str
    ready: bool
    waiting_reason: str | None
    wiring_only: bool
    profitability_claim: bool = False


def build_methodology_observation(input_: MethodologyObservationInput) -> MethodologyObservation:
    policy = strategy_research_methodology(input_.agent_id)
    if not aware(input_.observed_at) or any(
        not aware(item.as_of) or not aware(item.available_at) for item in input_.source_receipts
    ):
        raise MethodologyPolicyError("source_timestamp_invalid")
    if not input_.source_receipts:
        raise MethodologyPolicyError("source_receipt_missing")
    receipt_keys = tuple((item.authority, item.source_id) for item in input_.source_receipts)
    if len(receipt_keys) != len(set(receipt_keys)):
        raise MethodologyPolicyError("source_receipt_duplicate")
    source_ids = tuple(dict.fromkeys(item.source_id for item in input_.source_receipts))
    supplied = tuple(item.authority for item in input_.source_receipts)
    if any(authority not in policy.accepted_source_authorities for authority in supplied):
        raise MethodologyPolicyError("source_authority_mismatch")
    if any(item.available_at < item.as_of or item.available_at > input_.observed_at for item in input_.source_receipts):
        raise MethodologyPolicyError("source_timestamp_invalid")
    mutable = next((item for item in input_.source_receipts if not item.immutable), None)
    if mutable is not None:
        raise MethodologyPolicyError(f"{input_.agent_id.value}_source_mutable:{mutable.authority}")
    freshness = dict(policy.freshness_by_authority)
    stale = next(
        (
            item
            for item in input_.source_receipts
            if not item.wiring_only and input_.observed_at - item.as_of > freshness[item.authority]
        ),
        None,
    )
    if stale is not None:
        raise MethodologyPolicyError(f"{input_.agent_id.value}_source_stale:{stale.authority}")
    missing = tuple(authority for authority in policy.required_source_authorities if authority not in supplied)
    incomplete = tuple(item.authority for item in input_.source_receipts if not item.complete)
    predictor_available_at = max(item.available_at for item in input_.source_receipts)
    spread = next(
        (
            item
            for item in input_.source_receipts
            if item.authority in {"fresh_actionable_spread", "fresh_reversion_spread"}
        ),
        None,
    )
    session = next(
        (item for item in input_.source_receipts if item.authority == "current_market_session"),
        None,
    )
    ny_bounds = regular_session_bounds(input_.observed_at.astimezone(NEW_YORK).date())
    kr_time = input_.observed_at.astimezone(KST).time()
    session_current = (
        session is None
        or session.wiring_only
        or (
            (
                session.as_of.astimezone(NEW_YORK).date() == input_.observed_at.astimezone(NEW_YORK).date()
                and ny_bounds is not None
                and ny_bounds[0] <= input_.observed_at <= ny_bounds[1]
            )
            or (
                session.as_of.astimezone(KST).date() == input_.observed_at.astimezone(KST).date()
                and dt.time(9, 1) <= kr_time < dt.time(15, 30)
            )
        )
    )
    waiting_reason = (
        f"waiting_source_authority:{missing[0]}"
        if missing
        else f"waiting_source_completion:{incomplete[0]}"
        if incomplete
        else "waiting_fresh_spread"
        if spread is not None and input_.observed_at - spread.as_of > dt.timedelta(minutes=2)
        else "waiting_current_market_session"
        if not session_current
        else None
    )
    return MethodologyObservation(
        agent_id=input_.agent_id,
        source_ids=source_ids,
        predictor_available_at=predictor_available_at,
        matures_at=predictor_available_at + policy.target_horizon,
        observation_grammar=policy.observation_grammar,
        predictor_grammar=policy.predictor_grammar,
        ready=waiting_reason is None,
        waiting_reason=waiting_reason,
        wiring_only=any(item.wiring_only for item in input_.source_receipts),
    )


__all__ = (
    "MethodologyObservation",
    "MethodologyObservationInput",
    "SourceAuthorityReceipt",
    "build_methodology_observation",
)
