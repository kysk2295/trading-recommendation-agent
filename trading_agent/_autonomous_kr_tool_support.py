from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import NoReturn

from pydantic import ValidationError

from trading_agent._autonomous_supervisor_steps import ObservationPayload, safe_payload
from trading_agent.autonomous_reasoning import AutonomousToolArguments
from trading_agent.autonomous_task_models import AutonomousResearchTask
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolExecutionContext, AutonomousToolInvocationError
from trading_agent.kr_autonomous_market_models import KrAutonomousMarketCorroboration
from trading_agent.kr_autonomous_pending_plan_models import KrAutonomousPendingPlan
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousNoTrade,
    KrAutonomousRejected,
    KrAutonomousTradeEvent,
    KrAutonomousTradeThesis,
    KrTradeRecommendation,
)
from trading_agent.kr_social_signal_models import KrSocialSignal
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig


def exact_arguments(args: AutonomousToolArguments, names: set[str]) -> dict[str, str]:
    values = dict(args.root)
    if set(values) != names:
        deny("kr_tool_arguments_invalid")
    return values


def trusted_task(
    context: AutonomousToolExecutionContext,
    task_database: str,
) -> AutonomousResearchTask:
    if context.market_scope != "kr_equities":
        deny("kr_tool_market_scope_denied")
    task = AutonomousTaskStore(Path(task_database)).reader().task(context.task_id)
    if task is None or task.market_scope != "kr_equities" or task.agent_family_id != context.agent_family_id:
        deny("kr_tool_task_context_denied")
    return task


def canonical_evidence_ids(raw: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(raw)
        canonical = json.dumps(decoded, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        values = tuple(decoded)
    except (TypeError, ValueError):
        deny("kr_tool_evidence_ids_invalid")
    if (
        raw != canonical
        or values != tuple(sorted(set(values)))
        or not all(isinstance(item, str) and len(item) == 64 for item in values)
    ):
        deny("kr_tool_evidence_ids_invalid")
    return values


def parse_thesis(raw: str) -> KrAutonomousTradeThesis:
    try:
        decoded = json.loads(raw)
        if raw != json.dumps(decoded, ensure_ascii=True, separators=(",", ":"), sort_keys=True):
            deny("kr_tool_thesis_not_canonical")
        return KrAutonomousTradeThesis.model_validate_json(raw)
    except (TypeError, ValidationError, ValueError):
        deny("kr_tool_thesis_invalid")


def parse_config(raw: str) -> ResearchAgentServiceConfig:
    try:
        return ResearchAgentServiceConfig.model_validate_json(raw)
    except (TypeError, ValidationError, ValueError):
        deny("kr_tool_config_invalid")


def observed_market(
    task_database: str,
    task: AutonomousResearchTask,
    market_id: str,
) -> KrAutonomousMarketCorroboration:
    steps = AutonomousTaskStore(Path(task_database)).reader().steps(task.task_id)
    for step in reversed(steps):
        try:
            payload = safe_payload(step)
            if not isinstance(payload, ObservationPayload) or payload.observation.tool_name != "kr.market.corroborate":
                continue
            market = KrAutonomousMarketCorroboration.model_validate_json(
                json.dumps(json.loads(payload.observation.bounded_json)["market"])
            )
        except (AttributeError, TypeError, ValidationError, ValueError):
            continue
        if market.corroboration_id == market_id and market.task_id == task.task_id:
            return market
    deny("kr_tool_market_observation_missing")


def plan_response(event: KrAutonomousPendingPlan | KrAutonomousTradeEvent) -> str:
    payload: dict[str, str | int] = {
        "plan_id": event.plan_id if isinstance(event, KrAutonomousPendingPlan) else event.event_id,
        "status": "pending_critic",
    }
    if isinstance(event, KrAutonomousPendingPlan):
        proposal = event.proposal
        payload |= {
            "entry": str(proposal.entry),
            "quantity": proposal.quantity,
            "stop": str(proposal.stop),
            "target_1": str(proposal.targets[0]),
            "target_2": str(proposal.targets[1]),
        }
    elif isinstance(event, KrTradeRecommendation):
        payload |= {
            "entry": str(event.entry),
            "quantity": event.quantity,
            "stop": str(event.stop),
            "target_1": str(event.targets[0]),
            "target_2": str(event.targets[1]),
        }
    else:
        payload["next_wake_at"] = event.next_wake_at.isoformat()
    return canonical(payload)


def nonrecommendation_response(event: KrAutonomousNoTrade | KrAutonomousRejected) -> str:
    return canonical(
        {
            "next_wake_at": event.next_wake_at.isoformat(),
            "plan_id": event.event_id,
            "reason_codes": ",".join(event.reason_codes),
            "status": event.outcome.value,
        }
    )


def matching_final_event(
    events: tuple[KrAutonomousTradeEvent, ...], candidate: KrAutonomousTradeEvent
) -> KrAutonomousTradeEvent | None:
    matches = tuple(event for event in events if _same_final_event(event, candidate))
    if len(matches) > 1:
        deny("kr_tool_finalization_ambiguous")
    return matches[0] if matches else None


def pending_lineage_is_valid(
    task: AutonomousResearchTask,
    pending: KrAutonomousPendingPlan,
    signal: KrSocialSignal,
    market: KrAutonomousMarketCorroboration,
) -> bool:
    return (
        signal == pending.request.social_signal
        and market == pending.request.market
        and task.root_source_evidence_id in pending.request.social_signal.evidence_ids
        and pending.request.thesis.social_signal_id == pending.request.social_signal.signal_id
        and pending.request.thesis.market_corroboration_id == pending.request.market.corroboration_id
    )


def _same_final_event(left: KrAutonomousTradeEvent, right: KrAutonomousTradeEvent) -> bool:
    return type(left) is type(right) and left.model_dump(
        mode="json", exclude={"event_id", "previous_event_id"}
    ) == right.model_dump(mode="json", exclude={"event_id", "previous_event_id"})


def canonical(value: dict[str, str | int]) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def deny(reason: str) -> NoReturn:
    raise AutonomousToolInvocationError(reason=reason)
