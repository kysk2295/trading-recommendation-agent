from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from trading_agent._autonomous_kr_tool_support import (
    canonical,
    canonical_evidence_ids,
    deny,
    exact_arguments,
    matching_final_event,
    nonrecommendation_response,
    observed_market,
    parse_config,
    parse_thesis,
    pending_lineage_is_valid,
    plan_response,
    trusted_task,
    utc_now,
)
from trading_agent.autonomous_reasoning import AutonomousToolArguments
from trading_agent.autonomous_tool_runtime import AutonomousToolExecutionContext
from trading_agent.browser_social_evidence_store import BrowserSocialEvidenceStore
from trading_agent.kr_autonomous_market_service import collect_and_project_kr_corroboration
from trading_agent.kr_autonomous_pending_plan_models import KrAutonomousPendingPlan, pending_plan_id
from trading_agent.kr_autonomous_pending_plan_store import KrAutonomousPendingPlanStore
from trading_agent.kr_autonomous_trade_boundary import revalidate_kr_autonomous_trade_request
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousNoTrade,
    KrAutonomousRejected,
    KrAutonomousTradeRequest,
    KrNoTradeReason,
    KrTradeRecommendation,
)
from trading_agent.kr_autonomous_trade_planner import finalize_kr_autonomous_trade, no_trade_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_proposal import precritic_no_trade_reasons, propose_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import InvalidKrAutonomousTradeStoreError, KrAutonomousTradeStore
from trading_agent.kr_social_signal_models import KrSocialSignalRequest, normalize_kr_social_signal
from trading_agent.kr_social_signal_store import KrSocialSignalStore


def normalize_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    browser_evidence_database: str,
    social_signal_database: str,
    task_database: str,
    service_config_json: str,
    trade_database: str,
    pending_plan_database: str,
) -> str:
    del service_config_json, trade_database, pending_plan_database
    values = exact_arguments(args, {"claim_summary", "evidence_ids_json", "symbol", "theme"})
    task = trusted_task(context, task_database)
    evidence_ids = canonical_evidence_ids(values["evidence_ids_json"])
    if task.root_source_evidence_id not in evidence_ids:
        deny("kr_tool_root_evidence_denied")
    records = tuple(BrowserSocialEvidenceStore(Path(browser_evidence_database)).get(item) for item in evidence_ids)
    if any(item is None for item in records):
        deny("kr_tool_evidence_missing")
    signal = normalize_kr_social_signal(
        KrSocialSignalRequest(
            task_id=task.task_id,
            symbol=values["symbol"],
            theme=values["theme"],
            claim_summary=values["claim_summary"],
            evidence_ids=evidence_ids,
            normalized_at=utc_now(),
        ),
        tuple(item for item in records if item is not None),
    )
    KrSocialSignalStore(Path(social_signal_database)).append(signal)
    return canonical(
        {
            "signal_id": signal.signal_id,
            "status": "ok",
            "symbol": signal.symbol,
            "verification_state": signal.verification_state.value,
        }
    )


def corroborate_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    browser_evidence_database: str,
    social_signal_database: str,
    task_database: str,
    service_config_json: str,
    trade_database: str,
    pending_plan_database: str,
) -> str:
    del browser_evidence_database, trade_database, pending_plan_database
    values = exact_arguments(args, {"signal_id", "symbol"})
    task = trusted_task(context, task_database)
    signal = KrSocialSignalStore(Path(social_signal_database)).get(values["signal_id"])
    if (
        signal is None
        or signal.task_id != task.task_id
        or signal.symbol != values["symbol"]
        or task.root_source_evidence_id not in signal.evidence_ids
    ):
        deny("kr_tool_signal_lineage_denied")
    config = parse_config(service_config_json)
    market = collect_and_project_kr_corroboration(signal, config, utc_now())
    return json.dumps(
        {"market": market.model_dump(mode="json"), "status": "ok"},
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def plan_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    browser_evidence_database: str,
    social_signal_database: str,
    task_database: str,
    service_config_json: str,
    trade_database: str,
    pending_plan_database: str,
) -> str:
    del browser_evidence_database, service_config_json
    thesis = parse_thesis(exact_arguments(args, {"thesis_json"})["thesis_json"])
    task = trusted_task(context, task_database)
    signal = KrSocialSignalStore(Path(social_signal_database)).get(thesis.social_signal_id)
    market = observed_market(task_database, task, thesis.market_corroboration_id)
    if (
        signal is None
        or thesis.task_id != task.task_id
        or signal.task_id != task.task_id
        or signal.symbol != thesis.symbol
        or market.social_signal_id != signal.signal_id
        or market.symbol != thesis.symbol
        or task.root_source_evidence_id not in signal.evidence_ids
    ):
        deny("kr_tool_plan_lineage_denied")
    now = utc_now()
    events = KrAutonomousTradeStore(Path(trade_database)).events()
    request = KrAutonomousTradeRequest(
        thesis=thesis,
        social_signal=signal,
        market=market,
        evaluated_at=now,
        next_wake_at=now + dt.timedelta(minutes=1),
        open_exposures=(),
        previous_event_id=None if not events else events[-1].event_id,
    )
    trusted = revalidate_kr_autonomous_trade_request(request)
    if trusted is None:
        deny("kr_tool_plan_lineage_denied")
    reasons = precritic_no_trade_reasons(trusted)
    if reasons:
        event = no_trade_kr_autonomous_trade(trusted, reasons)
        KrAutonomousTradeStore(Path(trade_database)).append(event)
        return nonrecommendation_response(event)
    proposal, failure = propose_kr_autonomous_trade(trusted)
    if proposal is None:
        event = no_trade_kr_autonomous_trade(trusted, (failure or KrNoTradeReason.INVALID_STOP,))
        KrAutonomousTradeStore(Path(trade_database)).append(event)
        return nonrecommendation_response(event)
    draft = KrAutonomousPendingPlan.model_construct(plan_id="", request=trusted, proposal=proposal)
    pending = KrAutonomousPendingPlan.model_validate(
        draft.model_copy(update={"plan_id": pending_plan_id(draft)}).model_dump(mode="python")
    )
    KrAutonomousPendingPlanStore(Path(pending_plan_database)).append(pending)
    return plan_response(pending)


def critic_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    browser_evidence_database: str,
    social_signal_database: str,
    task_database: str,
    service_config_json: str,
    trade_database: str,
    pending_plan_database: str,
) -> str:
    del browser_evidence_database, service_config_json
    plan_id = exact_arguments(args, {"plan_id"})["plan_id"]
    task = trusted_task(context, task_database)
    pending = KrAutonomousPendingPlanStore(Path(pending_plan_database)).plan(plan_id)
    if pending is not None:
        if pending.request.thesis.task_id != task.task_id:
            deny("kr_tool_plan_lineage_denied")
        signal = KrSocialSignalStore(Path(social_signal_database)).get(pending.request.social_signal.signal_id)
        market = observed_market(task_database, task, pending.request.market.corroboration_id)
        if signal is None or not pending_lineage_is_valid(task, pending, signal, market):
            deny("kr_tool_critic_lineage_denied")
        trade_store = KrAutonomousTradeStore(Path(trade_database))
        events = trade_store.events()
        candidate = finalize_kr_autonomous_trade(pending.request, pending.proposal, plan_id)
        event = matching_final_event(events, candidate)
        if event is None:
            if utc_now() >= pending.request.market.valid_until:
                deny("kr_tool_pending_plan_stale")
            for _ in range(3):
                request = pending.request.model_copy(
                    update={"previous_event_id": None if not events else events[-1].event_id}
                )
                event = finalize_kr_autonomous_trade(request, pending.proposal, plan_id)
                try:
                    trade_store.append(event)
                    break
                except InvalidKrAutonomousTradeStoreError:
                    refreshed = trade_store.events()
                    if refreshed == events:
                        raise
                    event = matching_final_event(refreshed, event)
                    if event is not None:
                        break
                    events = refreshed
            else:
                raise InvalidKrAutonomousTradeStoreError
    else:
        event = KrAutonomousTradeStore(Path(trade_database)).event(plan_id)
    if event is None or event.task_id != task.task_id:
        deny("kr_tool_plan_lineage_denied")
    if isinstance(event, KrAutonomousNoTrade | KrAutonomousRejected):
        return nonrecommendation_response(event)
    if not isinstance(event, KrTradeRecommendation):
        deny("kr_tool_plan_lineage_denied")
    signal = KrSocialSignalStore(Path(social_signal_database)).get(event.social_signal_id)
    market = observed_market(task_database, task, event.market_corroboration_id)
    if (
        signal is None
        or task.root_source_evidence_id not in signal.evidence_ids
        or signal.task_id != task.task_id
        or market.social_signal_id != signal.signal_id
        or event.critic_verdict.proposal_id != event.proposal_id
    ):
        deny("kr_tool_critic_lineage_denied")
    return canonical(
        {
            "event_id": event.event_id,
            "plan_id": plan_id,
            "status": event.critic_verdict.status.value,
            "verdict_id": event.critic_verdict_id,
        }
    )
