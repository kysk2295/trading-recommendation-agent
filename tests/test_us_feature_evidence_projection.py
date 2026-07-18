from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.intraday_feature_kernel import (
    CompletedMinuteBar,
    FeatureSnapshotStatus,
    IntradayFeatureSnapshot,
    build_intraday_feature_snapshot,
)
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SignalActionability,
    SourceCoverage,
)
from trading_agent.us_feature_evidence_projection import (
    EvidenceGatedSignalRequest,
    UsFeatureEvidenceBinding,
    UsFeatureGateBlocked,
    UsFeatureGateBlockedReason,
    UsFeatureGateReady,
    project_evidence_gated_trade_signal_publications,
    project_us_opportunity_with_feature_evidence,
)

_UTC = dt.UTC
_START = dt.datetime(2026, 7, 17, 13, 30, tzinfo=_UTC)
_FEATURE_OBSERVED = _START + dt.timedelta(minutes=35, seconds=1)
_BASE_OBSERVED = _FEATURE_OBSERVED + dt.timedelta(seconds=1)
_EVALUATED = _BASE_OBSERVED + dt.timedelta(seconds=1)
_SYMBOL = "ACME"


def test_ready_feature_is_referenced_without_copying_indicators_and_keeps_signal_conditional() -> None:
    base = _opportunity()
    gate = project_us_opportunity_with_feature_evidence(
        base,
        (UsFeatureEvidenceBinding(_SYMBOL, _ready_feature()),),
        evaluated_at=_EVALUATED,
    )

    assert type(gate) is UsFeatureGateReady
    derived = gate.opportunity
    assert derived.opportunity_id != base.opportunity_id
    assert derived.candidates == base.candidates
    feature_refs = tuple(item for item in derived.evidence_refs if item.namespace == "research/intraday_feature")
    assert len(feature_refs) == 1
    assert len(feature_refs[0].record_id) == 64
    assert feature_refs[0].observed_at == _FEATURE_OBSERVED

    publications = project_evidence_gated_trade_signal_publications(
        gate,
        (_recommendation(),),
        _signal_request(),
    )

    assert len(publications) == 1
    signal = publications[0].signal
    assert signal.opportunity_id == derived.opportunity_id
    assert signal.actionability is SignalActionability.CONDITIONAL
    assert signal.quote_validation is None
    assert tuple(item.namespace for item in signal.evidence_refs) == (
        "opportunity/snapshot",
        "paper/recommendation",
    )


def test_derived_identity_changes_when_base_candidate_content_changes() -> None:
    base = _opportunity()
    changed_candidate = base.candidates[0].model_copy(update={"score": Decimal("0.13")})
    changed = OpportunitySnapshot(
        **{
            **base.model_dump(mode="python"),
            "candidates": (changed_candidate,),
        }
    )
    binding = (UsFeatureEvidenceBinding(_SYMBOL, _ready_feature()),)

    first = project_us_opportunity_with_feature_evidence(
        base,
        binding,
        evaluated_at=_EVALUATED,
    )
    second = project_us_opportunity_with_feature_evidence(
        changed,
        binding,
        evaluated_at=_EVALUATED,
    )

    assert type(first) is UsFeatureGateReady
    assert type(second) is UsFeatureGateReady
    assert first.opportunity.opportunity_id != second.opportunity.opportunity_id


def test_missing_candidate_feature_blocks_opportunity_and_signal_publication() -> None:
    gate = project_us_opportunity_with_feature_evidence(
        _opportunity(),
        (),
        evaluated_at=_EVALUATED,
    )

    assert gate == UsFeatureGateBlocked(
        reason=UsFeatureGateBlockedReason.MISSING_EVIDENCE,
        base_opportunity_id=_opportunity().opportunity_id,
        evaluated_at=_EVALUATED,
    )
    assert (
        project_evidence_gated_trade_signal_publications(
            gate,
            (_recommendation(),),
            _signal_request(),
        )
        == ()
    )


@pytest.mark.parametrize(
    ("feature_status", "reason"),
    (
        (FeatureSnapshotStatus.BLOCKED_GAP, UsFeatureGateBlockedReason.FEATURE_GAP),
        (FeatureSnapshotStatus.BLOCKED_STALE, UsFeatureGateBlockedReason.FEATURE_STALE),
        (
            FeatureSnapshotStatus.BLOCKED_INSUFFICIENT_HISTORY,
            UsFeatureGateBlockedReason.INSUFFICIENT_HISTORY,
        ),
    ),
)
def test_blocked_feature_status_prevents_publication(
    feature_status: FeatureSnapshotStatus,
    reason: UsFeatureGateBlockedReason,
) -> None:
    feature = replace(_ready_feature(), status=feature_status)

    gate = project_us_opportunity_with_feature_evidence(
        _opportunity(),
        (UsFeatureEvidenceBinding(_SYMBOL, feature),),
        evaluated_at=_EVALUATED,
    )

    assert type(gate) is UsFeatureGateBlocked
    assert gate.reason is reason
    assert project_evidence_gated_trade_signal_publications(gate, (_recommendation(),), _signal_request()) == ()


def test_ready_feature_older_than_runtime_freshness_window_is_blocked() -> None:
    gate = project_us_opportunity_with_feature_evidence(
        _opportunity(valid_for=dt.timedelta(minutes=10)),
        (UsFeatureEvidenceBinding(_SYMBOL, _ready_feature()),),
        evaluated_at=_FEATURE_OBSERVED + dt.timedelta(minutes=2, microseconds=1),
    )

    assert type(gate) is UsFeatureGateBlocked
    assert gate.reason is UsFeatureGateBlockedReason.FEATURE_STALE


def test_future_feature_evidence_is_blocked_as_noncausal() -> None:
    future = replace(
        _ready_feature(),
        observed_at=_EVALUATED + dt.timedelta(seconds=1),
    )

    gate = project_us_opportunity_with_feature_evidence(
        _opportunity(),
        (UsFeatureEvidenceBinding(_SYMBOL, future),),
        evaluated_at=_EVALUATED,
    )

    assert type(gate) is UsFeatureGateBlocked
    assert gate.reason is UsFeatureGateBlockedReason.NONCAUSAL_EVIDENCE


def _identity() -> ResearchInputIdentity:
    replay = CanonicalDatasetReplay(
        dataset_id="ds_m4_projection_fixture",
        event_count=35,
        canonical_event_content_sha256="a" * 64,
        parquet_sha256="c" * 64,
        raw_manifest_id="raw_m4_projection_fixture",
        raw_manifest_content_sha256="b" * 64,
    )
    return ResearchInputIdentity.from_verified_replay(
        "us_equities.day_trading.runtime_features",
        replay,
    )


def _ready_feature() -> IntradayFeatureSnapshot:
    bars = tuple(
        CompletedMinuteBar(
            start_at=_START + dt.timedelta(minutes=index),
            end_at=_START + dt.timedelta(minutes=index + 1),
            open=Decimal("100") + Decimal(index) / Decimal(10),
            high=Decimal("101") + Decimal(index) / Decimal(10),
            low=Decimal("99") + Decimal(index) / Decimal(10),
            close=Decimal("100.5") + Decimal(index) / Decimal(10),
            volume=100 + index,
        )
        for index in range(35)
    )
    return build_intraday_feature_snapshot(
        _identity(),
        "us-eq-fixture-acme",
        _FEATURE_OBSERVED,
        bars,
        Decimal("4000"),
    )


def _opportunity(*, valid_for: dt.timedelta = dt.timedelta(minutes=1)) -> OpportunitySnapshot:
    return OpportunitySnapshot(
        opportunity_id="us-opportunity-m4-base",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="kis-risk-screen-v1",
        observed_at=_BASE_OBSERVED,
        valid_until=_BASE_OBSERVED + valid_for,
        candidates=(
            OpportunityCandidate(
                symbol=_SYMBOL,
                rank=1,
                score=Decimal("0.12"),
                features=(FeatureValue(name="change_pct", value="0.12"),),
            ),
        ),
        evidence_refs=(
            EvidenceRef(
                namespace="kis/ranking",
                record_id="updown:NAS:1:ACME",
                observed_at=_BASE_OBSERVED,
            ),
        ),
        source_coverage=(
            SourceCoverage(
                source_id="kis_updown_nas",
                observed_at=_BASE_OBSERVED,
                record_count=1,
                complete=True,
            ),
        ),
    )


def _recommendation() -> Recommendation:
    return Recommendation(
        recommendation_id="rec-m4-1",
        symbol=_SYMBOL,
        strategy="opening_range_breakout",
        created_at=_EVALUATED + dt.timedelta(seconds=1),
        entry=10.5,
        stop=10.0,
        target_1r=11.0,
        target_2r=11.5,
        state=RecommendationState.SETUP,
        rationale="ORB fixture",
    )


def _signal_request() -> EvidenceGatedSignalRequest:
    return EvidenceGatedSignalRequest(
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        strategy_version="orb-v1",
        published_at=_EVALUATED + dt.timedelta(seconds=2),
        created_after=_EVALUATED,
    )
