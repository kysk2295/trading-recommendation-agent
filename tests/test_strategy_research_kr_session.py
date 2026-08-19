from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_kis_kr_session_calendar import _receipt
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.market_context_models import MarketContextSnapshot, MarketRegimeLabel
from trading_agent.research_agent_cycle_models import ResearchAgentTriggerKind
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_source_common import ResearchAgentEvidenceMaterial, canonical_model_json
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef as OpportunityEvidenceRef,
)
from trading_agent.signal_contract_models import FeatureValue, OpportunityCandidate, OpportunitySnapshot, SourceCoverage
from trading_agent.strategy_research_evidence_service import (
    CycleStoreMarketContextEvidenceService,
    CycleStoreOpportunityEvidenceService,
    KisKrMarketSessionGate,
    StrategyResearchEvidenceRejected,
)

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 7, 20, 13, 0, tzinfo=KST)


def test_kis_calendar_admits_current_kr_sources_while_nyse_is_closed(tmp_path: Path) -> None:
    calendar = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    receipt = replace(_receipt(), received_at=NOW - dt.timedelta(hours=4))
    assert calendar.append(receipt, project_kis_kr_session_calendar(receipt)) is True
    with ResearchAgentCycleStore(tmp_path / "cycles.sqlite3") as store:
        opportunity, context = _kr_sources()
        assert store.append_evidence(opportunity)
        assert store.append_evidence(context)
        gate = KisKrMarketSessionGate(calendar.path)

        candidate = CycleStoreOpportunityEvidenceService(store.all_evidence, gate).candidate(
            opportunity.evidence_id,
            NOW,
        )
        current = CycleStoreMarketContextEvidenceService(store.all_evidence, gate).current("kr_equities", NOW)

    assert candidate.opportunity.strategy_lane.market_id is MarketId.KR_EQUITIES
    assert current.snapshot.market_id is MarketId.KR_EQUITIES


def test_kr_sources_fail_closed_without_current_kis_calendar(tmp_path: Path) -> None:
    with ResearchAgentCycleStore(tmp_path / "cycles.sqlite3") as store:
        opportunity, _ = _kr_sources()
        assert store.append_evidence(opportunity)

        with pytest.raises(StrategyResearchEvidenceRejected, match="kr_session_calendar_missing"):
            _ = CycleStoreOpportunityEvidenceService(
                store.all_evidence,
                KisKrMarketSessionGate(tmp_path / "missing.sqlite3"),
            ).candidate(opportunity.evidence_id, NOW)


def _kr_sources():
    observed = NOW - dt.timedelta(minutes=1)
    opportunity_snapshot = OpportunitySnapshot(
        opportunity_id="kr-opportunity-20260720t125900-abcd1234",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.KR_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="theme_momentum",
        ),
        producer_strategy_version="kr-theme-momentum-v1",
        observed_at=observed,
        valid_until=NOW + dt.timedelta(minutes=4),
        candidates=(
            OpportunityCandidate(
                symbol="005930",
                rank=1,
                score=Decimal("1"),
                features=(FeatureValue(name="spread_bps", value="2.5"),),
            ),
        ),
        evidence_refs=(
            OpportunityEvidenceRef(namespace="kis/kr", record_id="quote-005930", observed_at=observed),
        ),
        source_coverage=(SourceCoverage(source_id="kis_kr", observed_at=observed, record_count=1, complete=True),),
    )
    context_snapshot = MarketContextSnapshot(
        context_id="kr-context-20260720t125800",
        market_id=MarketId.KR_EQUITIES,
        observed_at=observed,
        valid_until=NOW + dt.timedelta(minutes=4),
        regime_labels=(MarketRegimeLabel.TRENDING,),
        breadth_and_volatility_features=(FeatureValue(name="advance_ratio", value="0.61"),),
        macro_and_flow_refs=(),
        coverage=(SourceCoverage(source_id="kis_kr_breadth", observed_at=observed, record_count=2, complete=True),),
        producer_version="kr-breadth-v1",
    )
    return (
        ResearchAgentEvidenceMaterial(
            family="opportunity_manager",
            trigger=ResearchAgentTriggerKind.NEW_DATA,
            source_key=f"opportunity.{opportunity_snapshot.opportunity_id}",
            observed_at=observed,
            available_at=observed,
            market_id="kr_equities",
            canonical_payload=canonical_model_json(opportunity_snapshot),
        ).evidence(),
        ResearchAgentEvidenceMaterial(
            family="market_context",
            trigger=ResearchAgentTriggerKind.MARKET_EVENT,
            source_key=f"market_context.{context_snapshot.context_id}",
            observed_at=observed,
            available_at=observed,
            market_id="kr_equities",
            canonical_payload=canonical_model_json(context_snapshot),
        ).evidence(),
    )
