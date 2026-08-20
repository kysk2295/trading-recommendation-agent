from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.alpaca_news_coverage_models import (
    AlpacaNewsCoverageAssessment,
    AlpacaNewsCoverageManifest,
    AlpacaNewsCoverageSlice,
    AlpacaNewsCoverageSliceStatus,
)
from trading_agent.alpaca_news_models import AlpacaNewsArticle, AlpacaNewsRequest
from trading_agent.alpaca_news_opportunity_evidence import (
    AlpacaNewsEvidenceObservation,
    AlpacaNewsOpportunityEvidenceBundle,
    AlpacaNewsOpportunityEvidenceSnapshot,
)
from trading_agent.data_foundation_manifest import load_data_foundation_manifest
from trading_agent.generated_strategy_protocol import BarFrame, CandidateFrame
from trading_agent.market_context_models import MarketContextSnapshot, MarketRegimeLabel
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    QuoteValidation,
    SourceCoverage,
)
from trading_agent.us_day_situation_models import FlowObservationKind, ObservableFlow, ThemeState
from trading_agent.us_day_situation_projection import (
    UsDaySituationProjectionError,
    project_us_day_situation,
)
from trading_agent.us_forward_shadow_models import UsForwardShadowTick, completed_bar_id
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerBundle
from trading_agent.us_quote_actionability_evidence import UsQuotePolicyEvidence
from trading_agent.us_quote_actionability_models import spread_bps
from trading_agent.us_subscription_models import BroadScannerCandidate, BroadScannerSnapshot

UTC = dt.UTC
EVALUATED_AT = dt.datetime(2026, 8, 20, 14, 6, 5, tzinfo=UTC)
LATEST_BAR_AT = dt.datetime(2026, 8, 20, 14, 5, tzinfo=UTC)
HEADLINE = "Semiconductor equipment demand accelerates"
FOUNDATION = Path(__file__).resolve().parents[1] / "examples/data/us-orb-data-foundation-v1.json"


def test_situation_map_links_theme_catalyst_flow_and_leader_evidence() -> None:
    inputs = _inputs()

    situation = project_us_day_situation(**inputs)

    theme = situation.themes[0]
    assert theme.state is ThemeState.EMERGING
    assert theme.catalysts[0].headline == HEADLINE
    assert theme.symbols == ("AMD", "NVDA")
    assert theme.keywords == ("accelerates", "demand", "equipment", "semiconductor")
    assert theme.leaders[0].symbol == "NVDA"
    assert theme.leaders[0].flow.observation_kind is FlowObservationKind.OBSERVED
    assert theme.leaders[0].flow.relative_volume == Decimal("3.2")
    assert theme.leaders[0].flow.vwap_relation == "unavailable"
    assert all(claim.evidence_refs for claim in theme.claims)


def test_projection_is_deterministic_under_article_and_tick_reordering() -> None:
    inputs = _inputs()
    first = project_us_day_situation(**inputs)
    reordered = dict(inputs)
    reordered["articles"] = tuple(reversed(inputs["articles"]))
    reordered["completed_bars"] = tuple(reversed(inputs["completed_bars"]))

    second = project_us_day_situation(**reordered)

    assert second == first


@pytest.mark.parametrize(
    ("field", "mutator"),
    (
        ("evaluated_at", lambda value: value + dt.timedelta(minutes=1)),
        ("articles", lambda value: (value[0].model_copy(update={"created_at": EVALUATED_AT}),)),
        ("quotes", lambda value: value[:-1]),
        (
            "market_context",
            lambda value: value.model_copy(update={"valid_until": EVALUATED_AT - dt.timedelta(seconds=1)}),
        ),
    ),
)
def test_projection_fails_closed_for_stale_missing_or_future_inputs(field: str, mutator: object) -> None:
    inputs = _inputs()
    inputs[field] = mutator(inputs[field])  # type: ignore[operator]

    with pytest.raises(UsDaySituationProjectionError, match="US day situation projection is invalid"):
        project_us_day_situation(**inputs)


def test_projection_rejects_news_receipt_or_candidate_identity_mismatch() -> None:
    inputs = _inputs()
    article = inputs["articles"][0]
    inputs["articles"] = (article.model_copy(update={"provider_article_id": 999}),)

    with pytest.raises(UsDaySituationProjectionError):
        project_us_day_situation(**inputs)


def test_inferred_flow_requires_rule_while_observed_flow_forbids_one() -> None:
    payload = {
        "observation_kind": FlowObservationKind.INFERRED,
        "relative_volume": Decimal("1"),
        "dollar_volume": Decimal("100"),
        "spread_bps": Decimal("2"),
        "bid_size": 10,
        "ask_size": 12,
        "vwap_relation": "unavailable",
        "breakout_absorption_proxy": None,
        "cross_symbol_relative_strength": Decimal("0"),
        "evidence_refs": (_ref("flow/test", "observed", LATEST_BAR_AT),),
    }
    with pytest.raises(ValidationError):
        ObservableFlow(**payload)
    with pytest.raises(ValidationError):
        ObservableFlow(**(payload | {"observation_kind": FlowObservationKind.OBSERVED, "inference_rule": "invented"}))


def _inputs() -> dict[str, object]:
    article = AlpacaNewsArticle(
        provider_article_id=77,
        headline=HEADLINE,
        source="benzinga",
        symbols=("NVDA", "AMD"),
        created_at=dt.datetime(2026, 8, 20, 13, 58, tzinfo=UTC),
        updated_at=dt.datetime(2026, 8, 20, 13, 59, tzinfo=UTC),
        url="https://example.invalid/news/77",
    )
    return {
        "scanner": _scanner(),
        "articles": (article,),
        "news_evidence": _news_evidence(article),
        "market_context": _context(),
        "quotes": (_quote("AMD", "179.95", "180.05"), _quote("NVDA", "199.95", "200.05")),
        "completed_bars": (_tick("AMD", 180.0, 2.1, 9_000_000.0), _tick("NVDA", 200.0, 3.2, 15_000_000.0)),
        "evaluated_at": EVALUATED_AT,
    }


def _scanner() -> UsOpportunityScannerBundle:
    opportunity = OpportunitySnapshot(
        opportunity_id="us-opportunity-situation-20260820t140540z",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="fixture-v1",
        observed_at=EVALUATED_AT - dt.timedelta(seconds=25),
        valid_until=EVALUATED_AT + dt.timedelta(seconds=35),
        candidates=(
            OpportunityCandidate(
                symbol="NVDA", rank=1, score=Decimal("20"), features=(FeatureValue(name="change_pct", value="5.2"),)
            ),
            OpportunityCandidate(
                symbol="AMD", rank=2, score=Decimal("18"), features=(FeatureValue(name="change_pct", value="4.1"),)
            ),
        ),
        evidence_refs=(_ref("scanner/ranking", "current", EVALUATED_AT - dt.timedelta(seconds=25)),),
        source_coverage=(
            SourceCoverage(
                source_id="alpaca_sip",
                observed_at=EVALUATED_AT - dt.timedelta(seconds=25),
                record_count=2,
                complete=True,
            ),
        ),
    )
    identity = ResearchInputIdentity("us_equities.broad_scanner", "d" * 64, "a" * 64, "raw", "b" * 64, "c" * 64)
    snapshot = BroadScannerSnapshot(
        identity=identity,
        observed_at=opportunity.observed_at,
        candidates=(
            BroadScannerCandidate("alpaca:amd", "AMD", Decimal("18"), 2),
            BroadScannerCandidate("alpaca:nvda", "NVDA", Decimal("20"), 1),
        ),
    )
    return UsOpportunityScannerBundle(opportunity, snapshot, load_data_foundation_manifest(FOUNDATION))


def _news_evidence(article: AlpacaNewsArticle) -> AlpacaNewsOpportunityEvidenceBundle:
    request = AlpacaNewsRequest(
        collection_id="situation",
        symbols=("AMD", "NVDA"),
        start_at=EVALUATED_AT - dt.timedelta(hours=1),
        end_at=EVALUATED_AT - dt.timedelta(seconds=30),
        limit=50,
        max_pages=2,
    )
    manifest = AlpacaNewsCoverageManifest(
        universe_id="situation", cutoff_at=EVALUATED_AT - dt.timedelta(seconds=20), requests=(request,)
    )
    receipt_id = "e" * 64
    received = EVALUATED_AT - dt.timedelta(seconds=25)
    slice_ = AlpacaNewsCoverageSlice(
        request_id=request.request_id,
        status=AlpacaNewsCoverageSliceStatus.SUCCESS,
        run_id="f" * 64,
        completed_at=received,
        page_count=1,
        article_count=1,
        latest_event_at=article.updated_at,
        failure_code=None,
    )
    assessment = AlpacaNewsCoverageAssessment(
        manifest_id=manifest.manifest_id,
        universe_id=manifest.universe_id,
        assessed_at=manifest.cutoff_at,
        slices=(slice_,),
        declared_symbol_count=2,
        successful_symbol_count=2,
        completeness_bps=10_000,
        accepted_article_count=1,
        latest_event_at=article.updated_at,
    )
    snapshots = []
    for symbol in manifest.symbols:
        observation = AlpacaNewsEvidenceObservation(
            event_id=article.event_id,
            receipt_id=receipt_id,
            symbol=symbol,
            source=article.source,
            provider_created_at=article.created_at,
            provider_updated_at=article.updated_at,
            received_at=received,
        )
        refs = tuple(
            sorted(
                (
                    _ref("alpaca/news/coverage", assessment.assessment_id, assessment.assessed_at),
                    _ref("alpaca/news/article", observation.observation_id, received),
                ),
                key=lambda item: item.canonical_id,
            )
        )
        snapshots.append(
            AlpacaNewsOpportunityEvidenceSnapshot(
                manifest_id=manifest.manifest_id,
                assessment_id=assessment.assessment_id,
                universe_id=manifest.universe_id,
                symbol=symbol,
                observed_at=assessment.assessed_at,
                observations=(observation,),
                evidence_refs=refs,
                coverage=SourceCoverage(
                    source_id="alpaca_news", observed_at=assessment.assessed_at, record_count=1, complete=True
                ),
            )
        )
    return AlpacaNewsOpportunityEvidenceBundle(manifest=manifest, assessment=assessment, snapshots=tuple(snapshots))


def _context() -> MarketContextSnapshot:
    observed = EVALUATED_AT - dt.timedelta(seconds=40)
    return MarketContextSnapshot(
        context_id="us-context-current",
        market_id=MarketId.US_EQUITIES,
        observed_at=observed,
        valid_until=EVALUATED_AT + dt.timedelta(minutes=1),
        regime_labels=(MarketRegimeLabel.TRENDING,),
        breadth_and_volatility_features=(FeatureValue(name="advance_decline", value="1.2"),),
        macro_and_flow_refs=(),
        coverage=(SourceCoverage(source_id="internal_breadth", observed_at=observed, record_count=1, complete=True),),
        producer_version="market-context-v1",
    )


def _quote(symbol: str, bid: str, ask: str) -> UsQuotePolicyEvidence:
    observed = EVALUATED_AT - dt.timedelta(seconds=2)
    quote_id = f"us-quote:{hashlib.sha256(symbol.encode()).hexdigest()}"
    ref = _ref("quote/snapshot", quote_id, observed)
    return UsQuotePolicyEvidence(
        quote_id=quote_id,
        evidence_ref=ref,
        symbol=symbol,
        provider_observed_at=observed,
        received_at=observed + dt.timedelta(milliseconds=100),
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=1_000 if symbol == "NVDA" else 800,
        ask_size=900,
        spread_bps=spread_bps(Decimal(bid), Decimal(ask)),
    )


def _tick(symbol: str, close: float, relative_volume: float, dollar_volume: float) -> UsForwardShadowTick:
    bars = tuple(
        BarFrame(
            symbol=symbol,
            timestamp=LATEST_BAR_AT - dt.timedelta(minutes=2 - index),
            open=close - 0.4,
            high=close + 0.5,
            low=close - 0.5,
            close=close,
            volume=10_000 + index,
            prior_close=close - 5,
            average_daily_volume=1_000_000,
            spread_bps=5,
            catalyst="news",
        )
        for index in range(3)
    )
    latest = bars[-1]
    observed = EVALUATED_AT
    return UsForwardShadowTick(
        market_id=MarketId.US_EQUITIES,
        policy_id="a" * 64,
        session_id="XNYS-2026-08-20",
        session_date=dt.date(2026, 8, 20),
        calendar_snapshot_id="calendar://official/XNYS/2026-v1",
        completed_bar_id=completed_bar_id(latest),
        completed_bar_sequence=6,
        bars=bars,
        candidate=CandidateFrame(
            symbol=symbol,
            timestamp=latest.timestamp,
            price=close,
            gap_pct=2,
            change_pct=5.2 if symbol == "NVDA" else 4.1,
            relative_volume=relative_volume,
            cumulative_dollar_volume=dollar_volume,
            spread_bps=5,
            catalyst="news",
        ),
        quote=QuoteValidation(
            bid=Decimal(str(close - 0.05)),
            ask=Decimal(str(close + 0.05)),
            observed_at=LATEST_BAR_AT + dt.timedelta(minutes=1),
            valid_until=EVALUATED_AT + dt.timedelta(seconds=25),
            spread_bps=Decimal(str(0.1 / close * 10_000)),
            max_slippage_bps=Decimal("20"),
        ),
        evidence_refs=(_ref("research/current_bar", completed_bar_id(latest), latest.timestamp),),
        observed_at=observed,
    )


def _ref(namespace: str, record_id: str, observed_at: dt.datetime) -> EvidenceRef:
    return EvidenceRef(namespace=namespace, record_id=record_id, observed_at=observed_at)
