from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from tests.kr_autonomous_tool_test_support import thesis
from tests.test_kis_kr_market_projection import _quote_body
from tests.test_kr_autonomous_market_service import NOW, _calendar, _receipts
from trading_agent.autonomous_kr_tool_runtime import KrAutonomousToolServices
from trading_agent.autonomous_reasoning import (
    AutonomousComplete,
    AutonomousDelegate,
    AutonomousReasoningResponse,
    AutonomousToolArguments,
    AutonomousToolCall,
)
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.browser_social_evidence import BrowserSocialEvidence
from trading_agent.kr_autonomous_market_models import KrAutonomousMarketCorroboration
from trading_agent.kr_autonomous_market_service import KrCorroborationProjectionInput, project_kr_corroboration
from trading_agent.kr_autonomous_pending_plan_models import KrAutonomousPendingPlan, pending_plan_id
from trading_agent.kr_autonomous_pending_plan_store import KrAutonomousPendingPlanStore
from trading_agent.kr_autonomous_trade_models import KrAutonomousTradeRequest, KrTradeRecommendation
from trading_agent.kr_autonomous_trade_planner import finalize_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_proposal import propose_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_models import KrSocialSignal, KrSocialSignalRequest, normalize_kr_social_signal
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_theme_day_setup_progress import KrCompletedMinuteBar
from trading_agent.kr_virtual_position_engine import arm_kr_virtual_position
from trading_agent.kr_virtual_position_models import KrVirtualPositionEvent
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore
from trading_agent.signal_contract_models import EvidenceRef

type ExpectedChain = tuple[
    KrSocialSignal,
    KrAutonomousMarketCorroboration,
    KrAutonomousPendingPlan,
    KrTradeRecommendation,
]


def expected_chain(task_id: str, posts: tuple[BrowserSocialEvidence, ...]) -> ExpectedChain:
    signal = normalize_kr_social_signal(
        KrSocialSignalRequest(
            task_id=task_id,
            symbol="005930",
            theme="Semiconductor demand",
            claim_summary="Independent reporting supports a semiconductor demand acceleration claim.",
            evidence_ids=tuple(sorted(post.evidence_id for post in posts)),
            normalized_at=NOW,
        ),
        posts,
    )
    market = project_kr_corroboration(
        KrCorroborationProjectionInput(
            signal=signal,
            calendar_snapshot=_calendar(),
            receipts=_live_receipts(),
            observed_at=NOW,
        )
    )
    trade_thesis = thesis(signal, market)
    request = KrAutonomousTradeRequest(
        thesis=trade_thesis,
        social_signal=signal,
        market=market,
        evaluated_at=NOW,
        next_wake_at=NOW + dt.timedelta(minutes=1),
        open_exposures=(),
    )
    proposal, failure = propose_kr_autonomous_trade(request)
    assert proposal is not None and failure is None
    draft = KrAutonomousPendingPlan.model_construct(plan_id="", request=request, proposal=proposal)
    pending = KrAutonomousPendingPlan.model_validate(
        draft.model_copy(update={"plan_id": pending_plan_id(draft)}).model_dump(mode="python")
    )
    recommendation = finalize_kr_autonomous_trade(request, proposal, pending.plan_id)
    assert isinstance(recommendation, KrTradeRecommendation)
    return signal, market, pending, recommendation


def reasoning_responses(
    order: str,
    signal: KrSocialSignal,
    market: KrAutonomousMarketCorroboration,
    pending: KrAutonomousPendingPlan,
    recommendation: KrTradeRecommendation,
) -> tuple[AutonomousReasoningResponse, ...]:
    normalize = tool_call(
        "social.signal.normalize",
        {
            "claim_summary": signal.claim_summary,
            "evidence_ids_json": json.dumps(signal.evidence_ids, separators=(",", ":")),
            "symbol": signal.symbol,
            "theme": signal.theme,
        },
    )
    market_call = tool_call("kr.market.corroborate", {"signal_id": signal.signal_id, "symbol": signal.symbol})
    prefix: tuple[AutonomousReasoningResponse, ...]
    if order == "observer_delegates":
        prefix = (
            delegate(AutonomousAgentRole.MARKET_OBSERVER),
            normalize,
            delegate(AutonomousAgentRole.OPPORTUNITY),
            market_call,
        )
    else:
        prefix = (delegate(AutonomousAgentRole.RESEARCH), normalize, market_call)
    position_id = arm_kr_virtual_position(recommendation, NOW).position_id
    return (
        *prefix,
        delegate(AutonomousAgentRole.TRADING),
        tool_call(
            "kr.trade.plan",
            {
                "thesis_json": json.dumps(
                    thesis(signal, market).model_dump(mode="json"),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            },
        ),
        delegate(AutonomousAgentRole.CRITIC),
        tool_call("critic.request", {"plan_id": pending.plan_id}),
        delegate(AutonomousAgentRole.TRADING),
        tool_call("kr.virtual.execute", {"recommendation_id": recommendation.event_id}),
        delegate(AutonomousAgentRole.POSITION),
        tool_call("kr.position.reconcile", {"position_id": position_id}),
        AutonomousComplete(
            summary="The autonomous KR fixture completed with immutable virtual outcome evidence.",
            completion_evidence_refs=(signal.evidence_ids[0],),
            reason="All bounded research, Critic, and virtual-position work is durably complete.",
        ),
    )


def snapshot(services: KrAutonomousToolServices, plan_id: str) -> str:
    pending = KrAutonomousPendingPlanStore(pending_path(services)).plan(plan_id)
    assert pending is not None
    reader = AutonomousTaskStore(services.task_database).reader()
    task = reader.task(pending.request.thesis.task_id)
    assert task is not None
    document = {
        "task": task.model_dump(mode="json"),
        "steps": [step.model_dump(mode="json") for step in reader.steps(task.task_id)],
        "signals": [
            item.model_dump(mode="json")
            for item in KrSocialSignalStore(services.social_signal_database).signals_for_task(task.task_id)
        ],
        "pending": None if pending is None else pending.model_dump(mode="json"),
        "trades": [item.model_dump(mode="json") for item in KrAutonomousTradeStore(trade_path(services)).events()],
        "positions": [
            item.model_dump(mode="json") for item in KrVirtualPositionStore(position_path(services)).all_events()
        ],
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True)


def events_json(events: tuple[KrVirtualPositionEvent, ...]) -> str:
    return json.dumps([event.model_dump(mode="json") for event in events], separators=(",", ":"), sort_keys=True)


def trade_path(services: KrAutonomousToolServices) -> Path:
    assert services.trade_database is not None
    return services.trade_database


def pending_path(services: KrAutonomousToolServices) -> Path:
    assert services.pending_plan_database is not None
    return services.pending_plan_database


def position_path(services: KrAutonomousToolServices) -> Path:
    assert services.position_database is not None
    return services.position_database


def collision_bar(recommendation: KrTradeRecommendation) -> KrCompletedMinuteBar:
    start = recommendation.timestamp.astimezone(NOW.tzinfo).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    observed = start + dt.timedelta(minutes=1, seconds=1)
    return KrCompletedMinuteBar(
        symbol=recommendation.symbol,
        start_at=start,
        end_at=start + dt.timedelta(minutes=1),
        observed_at=observed,
        open=recommendation.entry,
        high=recommendation.targets[0],
        low=recommendation.stop,
        close=recommendation.entry,
        volume=100,
        trading_value_krw=recommendation.entry * Decimal(100),
        evidence_ref=EvidenceRef(namespace="kr/fixture", record_id="future-collision", observed_at=observed),
    )


def _live_receipts():
    receipts = _receipts()
    return (
        replace(receipts[0], received_at=NOW),
        replace(receipts[1], received_at=NOW),
        replace(receipts[2], received_at=NOW, raw_payload=_quote_body(accepted_hour="130404")),
    )


def tool_call(name: str, values: dict[str, str]) -> AutonomousToolCall:
    return AutonomousToolCall(
        tool_name=name,
        args=AutonomousToolArguments(values),
        reason="The fixture model selected this bounded autonomous KR tool from current evidence.",
    )


def delegate(role: AutonomousAgentRole) -> AutonomousDelegate:
    return AutonomousDelegate(
        role=role,
        objective=f"Let the {role.value} agent choose its next bounded KR research action.",
        reason="The fixture model selected this role from the current durable task context.",
    )
