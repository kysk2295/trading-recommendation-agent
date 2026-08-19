from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import cast

import pytest

from trading_agent.contract_outbox import append_trade_signal_publication
from trading_agent.day_discovery_loop import DayDiscoveryError
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.recommendation_signal_projection import project_intraday_recommendation
from trading_agent.research_agent_actions import InvalidResearchAgentActionError, ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import (
    ActionId,
    CycleId,
    DecisionId,
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_day_actions import DayResearchActionExecutor
from trading_agent.research_agent_source_common import ResearchAgentEvidenceMaterial, canonical_payload_json
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.researcher_llm import ResearcherLlmError
from trading_agent.signal_contract_models import EvidenceRef
from trading_agent.trade_signal_publication import TradeSignalPublication

NOW = dt.datetime(2026, 8, 3, 14, 35, tzinfo=dt.UTC)


def test_publish_recommendation_resolves_existing_plan_and_signal(tmp_path: Path) -> None:
    recommendation = _recommendation()
    session = _session(tmp_path)
    _write_publication(session, recommendation)
    context = _context(_evidence(recommendation), ResearchAgentDecisionKind.PUBLISH_RECOMMENDATION)

    result = DayResearchActionExecutor(tmp_path).execute(context)

    assert result.status is ResearchAgentResultStatus.COMPLETED
    assert context.evidence[0].payload_sha256 in result.artifact_refs
    assert recommendation["recommendation_id"] in result.artifact_refs
    assert "symbol=ACME" in result.summary
    assert "entry=10.0" in result.summary
    assert "stop=9.5" in result.summary
    assert "targets=10.5,11.0" in result.summary
    assert "rationale=completed bar breakout" in result.summary


def test_publish_recommendation_rejects_signal_price_disagreement(tmp_path: Path) -> None:
    recommendation = _recommendation()
    session = _session(tmp_path)
    mismatched = recommendation | {"entry": 10.1}
    _write_publication(session, mismatched)

    with pytest.raises(InvalidResearchAgentActionError, match="authority_artifact_unresolved"):
        DayResearchActionExecutor(tmp_path).execute(
            _context(_evidence(recommendation), ResearchAgentDecisionKind.PUBLISH_RECOMMENDATION)
        )


def test_publish_recommendation_without_existing_plan_is_typed_no_setup(tmp_path: Path) -> None:
    context = _context(_evidence(None), ResearchAgentDecisionKind.PUBLISH_RECOMMENDATION)

    result = DayResearchActionExecutor(tmp_path).execute(context)

    assert result.status is ResearchAgentResultStatus.NO_ACTION
    assert result.reason == "no_setup"
    assert result.artifact_refs == ()


def test_review_open_state_returns_latest_immutable_event_artifact(tmp_path: Path) -> None:
    recommendation = _recommendation(state="stopped")
    context = _context(_evidence(recommendation), ResearchAgentDecisionKind.REVIEW_OPEN_STATE)

    result = DayResearchActionExecutor(tmp_path).execute(context)

    assert result.status is ResearchAgentResultStatus.COMPLETED
    assert "state=stopped" in result.summary
    assert "event_id=2" in result.summary
    assert context.evidence[0].payload_sha256 in result.artifact_refs


def test_discovery_failure_is_translated_to_typed_action_failure(tmp_path: Path) -> None:
    class FailingDiscovery:
        def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
            del context
            raise DayDiscoveryError("cycle_receipt_invalid")

    context = _context(_evidence(None), ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS)

    with pytest.raises(InvalidResearchAgentActionError, match="cycle_receipt_invalid"):
        DayResearchActionExecutor(tmp_path, FailingDiscovery()).execute(context)


def test_discovery_model_failure_is_translated_to_typed_action_failure(tmp_path: Path) -> None:
    class FailingDiscovery:
        def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
            del context
            raise ResearcherLlmError("fixture_invalid")

    context = _context(_evidence(None), ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS)

    with pytest.raises(InvalidResearchAgentActionError, match="day_discovery_model_invalid"):
        DayResearchActionExecutor(tmp_path, FailingDiscovery()).execute(context)


def _recommendation(*, state: str = "setup") -> dict[str, object]:
    return {
        "created_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
        "entry": 10.0,
        "events": [
            {
                "event_id": 1,
                "note": "recommendation created",
                "occurred_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
                "price": None,
                "state": "setup",
            },
            *(
                [
                    {
                        "event_id": 2,
                        "note": "stop won same-bar collision",
                        "occurred_at": (NOW - dt.timedelta(seconds=20)).isoformat(),
                        "price": 9.5,
                        "state": "stopped",
                    }
                ]
                if state == "stopped"
                else []
            ),
        ],
        "rationale": "completed bar breakout",
        "recommendation_id": "rec-acme-1",
        "state": state,
        "stop": 9.5,
        "strategy": "opening_range_breakout",
        "symbol": "ACME",
        "target_1r": 10.5,
        "target_2r": 11.0,
    }


def _evidence(recommendation: dict[str, object] | None):
    payload = {
        "checkpoint_count": 1,
        "checkpoints": [
            {"last_close": 9.9, "processed_at": (NOW - dt.timedelta(minutes=1)).isoformat(), "symbol": "ACME"}
        ],
        "database_sha256": "a" * 64,
        "event_count": 0 if recommendation is None else len(cast(list[dict[str, object]], recommendation["events"])),
        "latest_checkpoint_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
        "latest_risk_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
        "recommendation_count": 0 if recommendation is None else 1,
        "recommendations": [] if recommendation is None else [recommendation],
        "risk_sha256": "b" * 64,
        "session": "20260803",
    }
    source_key = "day.session.20260803"
    return ResearchAgentEvidenceMaterial(
        family="day_trading",
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key=source_key,
        observed_at=NOW - dt.timedelta(minutes=1),
        available_at=NOW - dt.timedelta(minutes=1),
        market_id="us_equities",
        canonical_payload=canonical_payload_json(payload),
        subject_refs=(source_key,),
    ).evidence()


def _context(evidence, kind: ResearchAgentDecisionKind) -> ResearchAgentActionContext:
    cycle = ResearchAgentCycleV1(
        cycle_id=CycleId("a" * 64),
        evidence_id=evidence.evidence_id,
        action_request_id=ActionId("b" * 64),
        agent_family_id="day_trading",
        market_id="us_equities",
        evidence_sequence=1,
        cursor_before=0,
        state=ResearchAgentCycleState.STARTED,
        started_at=NOW,
    )
    decision = ResearchAgentDecisionV1(
        decision_id=DecisionId("c" * 64),
        cycle_id=cycle.cycle_id,
        agent_family_id="day_trading",
        primary_decision=kind,
        requested_action=kind,
        question="Which existing Day artifact supports this action?",
        summary="Resolve the existing immutable Day artifact only.",
        subject_refs=(evidence.source_key,),
        evidence_refs=evidence.evidence_refs,
        decided_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
        model_id="fixture-v1",
        prompt_sha256="d" * 64,
        response_sha256="e" * 64,
    )
    return ResearchAgentActionContext(cycle, (evidence,), (), decision, NOW)


def _session(root: Path) -> Path:
    session = root / "20260803"
    session.mkdir()
    return session


def _write_publication(session: Path, values: dict[str, object]) -> None:
    recommendation = Recommendation(
        recommendation_id=str(values["recommendation_id"]),
        symbol=str(values["symbol"]),
        strategy=str(values["strategy"]),
        created_at=dt.datetime.fromisoformat(str(values["created_at"])),
        entry=float(str(values["entry"])),
        stop=float(str(values["stop"])),
        target_1r=float(str(values["target_1r"])),
        target_2r=float(str(values["target_2r"])),
        state=RecommendationState.SETUP,
        rationale=str(values["rationale"]),
    )
    signal = project_intraday_recommendation(
        recommendation,
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        strategy_version="orb-v1",
        valid_until=NOW + dt.timedelta(minutes=2),
        evidence_refs=(
            EvidenceRef(
                namespace="paper/recommendation",
                record_id=recommendation.recommendation_id,
                observed_at=recommendation.created_at,
            ),
        ),
    )
    publication = TradeSignalPublication(published_at=NOW, signal=signal)
    outbox = session / "trade-signals.v1.jsonl"
    assert append_trade_signal_publication(outbox, session / "cards", publication)
    outbox.chmod(0o600)
    assert hashlib.sha256(signal.model_dump_json().encode()).hexdigest()
