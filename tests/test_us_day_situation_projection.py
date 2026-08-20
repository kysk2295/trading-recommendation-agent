from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, replace
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
from trading_agent.us_day_situation_models import (
    CatalystClaimEvent,
    EvidenceBoundClaim,
    FlowInference,
    FlowInferenceKind,
    FlowObservationKind,
    ObservableFlow,
    SituationClaimKind,
    ThemeMap,
    ThemeState,
    UsDaySituationMap,
)
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


@dataclass(frozen=True)
class SituationInputs:
    scanner: UsOpportunityScannerBundle
    articles: tuple[AlpacaNewsArticle, ...]
    news_evidence: AlpacaNewsOpportunityEvidenceBundle
    market_context: MarketContextSnapshot
    quotes: tuple[UsQuotePolicyEvidence, ...]
    completed_bars: tuple[UsForwardShadowTick, ...]
    evaluated_at: dt.datetime


def _project(inputs: SituationInputs) -> UsDaySituationMap:
    return project_us_day_situation(
        scanner=inputs.scanner,
        articles=inputs.articles,
        news_evidence=inputs.news_evidence,
        market_context=inputs.market_context,
        quotes=inputs.quotes,
        completed_bars=inputs.completed_bars,
        evaluated_at=inputs.evaluated_at,
    )


def test_situation_map_links_theme_catalyst_flow_and_leader_evidence() -> None:
    inputs = _inputs()

    situation = _project(inputs)

    theme = situation.themes[0]
    assert theme.state is ThemeState.EMERGING
    assert theme.catalysts[0].headline == HEADLINE
    assert theme.symbols == ("AMD", "NVDA")
    assert theme.keywords == ("accelerates", "demand", "equipment", "semiconductor")
    assert theme.leaders[0].symbol == "NVDA"
    assert theme.leaders[0].flow.observation_kind is FlowObservationKind.OBSERVED
    assert theme.leaders[0].flow.relative_volume == Decimal("3.90078")
    assert theme.leaders[0].flow.dollar_volume == Decimal("6000600")
    assert theme.leaders[0].flow.vwap_relation == "crossing"
    assert set(ObservableFlow.model_fields) == {
        "observation_kind",
        "relative_volume",
        "dollar_volume",
        "spread_bps",
        "bid_size",
        "ask_size",
        "vwap_relation",
        "evidence_refs",
    }
    absorption, cross_symbol = theme.leaders[0].inferences
    assert absorption.kind is FlowInferenceKind.BREAKOUT_ABSORPTION_PROXY
    assert absorption.rule == "bar_quote_absorption_proxy_v1"
    assert cross_symbol.kind is FlowInferenceKind.CROSS_SYMBOL_RELATIVE_STRENGTH
    assert cross_symbol.rule == "cross_symbol_relative_strength_v1"
    assert len({item.record_id for item in cross_symbol.evidence_refs}) == 2
    assert all(claim.evidence_refs for claim in theme.claims)


def test_projection_is_deterministic_under_article_and_tick_reordering() -> None:
    inputs = _inputs()
    first = _project(inputs)
    reordered = replace(
        inputs,
        articles=tuple(reversed(inputs.articles)),
        completed_bars=tuple(reversed(inputs.completed_bars)),
    )

    second = _project(reordered)

    assert second == first


def test_projection_fails_closed_for_stale_missing_or_future_inputs() -> None:
    inputs = _inputs()
    invalid = (
        replace(inputs, evaluated_at=inputs.evaluated_at + dt.timedelta(minutes=1)),
        replace(inputs, articles=(inputs.articles[0].model_copy(update={"created_at": EVALUATED_AT}),)),
        replace(inputs, quotes=inputs.quotes[:-1]),
        replace(
            inputs,
            market_context=inputs.market_context.model_copy(
                update={"valid_until": EVALUATED_AT - dt.timedelta(seconds=1)}
            ),
        ),
    )
    for value in invalid:
        with pytest.raises(UsDaySituationProjectionError, match="US day situation projection is invalid"):
            _project(value)


def test_projection_rejects_news_receipt_or_candidate_identity_mismatch() -> None:
    inputs = _inputs()
    article = inputs.articles[0]
    invalid = replace(inputs, articles=(article.model_copy(update={"provider_article_id": 999}),))

    with pytest.raises(UsDaySituationProjectionError):
        _project(invalid)


def test_caller_candidate_metrics_cannot_change_flow_or_leader_ranking() -> None:
    inputs = _inputs()
    baseline = _project(inputs)
    changed_ticks = tuple(_tick_with_adversarial_candidate(item) for item in inputs.completed_bars)

    adversarial = _project(replace(inputs, completed_bars=changed_ticks))

    assert adversarial == baseline


def test_projection_rejects_forged_quote_and_completed_bar_references() -> None:
    inputs = _inputs()
    quote = inputs.quotes[0]
    forged_quote = quote.model_copy(
        update={
            "evidence_ref": _ref("caller/forged", quote.quote_id, quote.provider_observed_at),
        }
    )
    with pytest.raises(UsDaySituationProjectionError):
        _project(replace(inputs, quotes=(forged_quote, inputs.quotes[1])))

    tick = inputs.completed_bars[0]
    forged_tick = tick.model_copy(
        update={
            "evidence_refs": (_ref("caller/forged", tick.completed_bar_id, tick.bars[-1].timestamp),),
        }
    )
    with pytest.raises(UsDaySituationProjectionError):
        _project(replace(inputs, completed_bars=(forged_tick, inputs.completed_bars[1])))


def test_inferred_flow_requires_rule_while_observed_flow_forbids_one() -> None:
    observed = _project(_inputs()).themes[0].leaders[0].flow
    legacy_payload = observed.model_dump(mode="python") | {
        "observation_kind": FlowObservationKind.INFERRED,
        "breakout_absorption_proxy": Decimal("5"),
        "inference_rule": "bar_quote_absorption_proxy_v1",
    }
    with pytest.raises(ValidationError):
        ObservableFlow.model_validate(legacy_payload)


def test_claim_is_closed_typed_catalyst_structure_with_deterministic_text() -> None:
    claim = _shared_catalyst_claim()
    assert claim.kind is SituationClaimKind.SHARED_CURRENT_SESSION_CATALYST
    assert claim.text == "Shared current-session catalyst links AMD, NVDA from 1 verified event."


def test_claim_rejects_hostile_prose_mismatched_symbols_and_wrong_evidence() -> None:
    payload = _shared_catalyst_claim().model_dump(mode="python")
    hostile_prose = (
        "Buy NVDA.",
        "Risk-free NVDA.",
        "Manipulation confirmed.",
        "NVDA recommendation.",
        "NVDA prediction.",
    )
    for text in hostile_prose:
        with pytest.raises(ValidationError):
            EvidenceBoundClaim.model_validate(payload | {"text": text})
    with pytest.raises(ValidationError):
        EvidenceBoundClaim.model_validate(payload | {"symbols": ("AMD",)})
    with pytest.raises(ValidationError):
        EvidenceBoundClaim.model_validate(payload | {"evidence_refs": (_valid_quote_ref(),)})

    theme = _project(_inputs()).themes[0]
    claim_payload = theme.claims[0].model_dump(mode="python")
    event_payload = theme.claims[0].events[0].model_dump(mode="python") | {"symbols": ("AMD", "MSFT")}
    claim_payload["events"] = (event_payload,)
    with pytest.raises(ValidationError):
        ThemeMap.model_validate(theme.model_dump(mode="python") | {"claims": (claim_payload,)})


def test_absorption_inference_rejects_missing_proxy_value() -> None:
    evidence = _canonical_refs((_valid_bar_ref("a"), _valid_quote_ref()))
    with pytest.raises(ValidationError):
        FlowInference.model_validate(
            {
                "kind": FlowInferenceKind.BREAKOUT_ABSORPTION_PROXY,
                "rule": "bar_quote_absorption_proxy_v1",
                "evidence_refs": evidence,
            }
        )


def test_absorption_inference_requires_bar_and_quote_evidence() -> None:
    with pytest.raises(ValidationError):
        FlowInference(
            kind=FlowInferenceKind.BREAKOUT_ABSORPTION_PROXY,
            value=Decimal("5"),
            rule="bar_quote_absorption_proxy_v1",
            evidence_refs=(_valid_bar_ref("a"),),
        )
    with pytest.raises(ValidationError):
        FlowInference(
            kind=FlowInferenceKind.BREAKOUT_ABSORPTION_PROXY,
            value=Decimal("5"),
            rule="bar_quote_absorption_proxy_v1",
            evidence_refs=(_valid_quote_ref(),),
        )

    evidence = _canonical_refs((_valid_bar_ref("a"), _valid_quote_ref()))
    inference = FlowInference(
        kind=FlowInferenceKind.BREAKOUT_ABSORPTION_PROXY,
        value=Decimal("5"),
        rule="bar_quote_absorption_proxy_v1",
        evidence_refs=evidence,
    )
    assert inference.value == Decimal("5")
    assert inference.evidence_refs == evidence


def test_cross_symbol_inference_requires_value_and_two_distinct_bar_records() -> None:
    one_bar = (_valid_bar_ref("a"),)
    with pytest.raises(ValidationError):
        FlowInference(
            kind=FlowInferenceKind.CROSS_SYMBOL_RELATIVE_STRENGTH,
            value=Decimal("2"),
            rule="cross_symbol_relative_strength_v1",
            evidence_refs=one_bar,
        )
    two_bars = (_valid_bar_ref("a"), _valid_bar_ref("b"))
    with pytest.raises(ValidationError):
        FlowInference.model_validate(
            {
                "kind": FlowInferenceKind.CROSS_SYMBOL_RELATIVE_STRENGTH,
                "rule": "cross_symbol_relative_strength_v1",
                "evidence_refs": two_bars,
            }
        )
    with pytest.raises(ValidationError):
        FlowInference(
            kind=FlowInferenceKind.CROSS_SYMBOL_RELATIVE_STRENGTH,
            value=Decimal("2"),
            rule="cross_symbol_relative_strength_v1",
            evidence_refs=one_bar,
        )

    inference = FlowInference(
        kind=FlowInferenceKind.CROSS_SYMBOL_RELATIVE_STRENGTH,
        value=Decimal("2"),
        rule="cross_symbol_relative_strength_v1",
        evidence_refs=two_bars,
    )
    assert inference.value == Decimal("2")
    assert inference.evidence_refs == two_bars


def _inputs() -> SituationInputs:
    article = AlpacaNewsArticle(
        provider_article_id=77,
        headline=HEADLINE,
        source="benzinga",
        symbols=("NVDA", "AMD"),
        created_at=dt.datetime(2026, 8, 20, 13, 58, tzinfo=UTC),
        updated_at=dt.datetime(2026, 8, 20, 13, 59, tzinfo=UTC),
        url="https://example.invalid/news/77",
    )
    return SituationInputs(
        scanner=_scanner(),
        articles=(article,),
        news_evidence=_news_evidence(article),
        market_context=_context(),
        quotes=(_quote("AMD", "179.95", "180.05"), _quote("NVDA", "199.95", "200.05")),
        completed_bars=(_tick("AMD", 180.0, 2.1, 9_000_000.0), _tick("NVDA", 200.0, 3.2, 15_000_000.0)),
        evaluated_at=EVALUATED_AT,
    )


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


def _tick_with_adversarial_candidate(tick: UsForwardShadowTick) -> UsForwardShadowTick:
    payload = tick.model_dump(mode="python")
    candidate = tick.candidate
    assert candidate is not None
    payload["candidate"] = candidate.model_dump(mode="python") | {
        "change_pct": 9999,
        "relative_volume": 1,
        "cumulative_dollar_volume": 777,
    }
    return UsForwardShadowTick.model_validate(payload)


def _valid_quote_ref() -> EvidenceRef:
    quote_id = f"us-quote:{hashlib.sha256(b'model-test').hexdigest()}"
    return _ref("quote/snapshot", quote_id, LATEST_BAR_AT)


def _valid_bar_ref(character: str) -> EvidenceRef:
    return _ref("research/current_bar", character * 64, LATEST_BAR_AT)


def _canonical_refs(refs: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    return tuple(sorted(refs, key=lambda item: item.canonical_id))


def _shared_catalyst_claim() -> EvidenceBoundClaim:
    evidence = (_ref("alpaca/news/article", "f" * 64, LATEST_BAR_AT),)
    return EvidenceBoundClaim(
        kind=SituationClaimKind.SHARED_CURRENT_SESSION_CATALYST,
        events=(
            CatalystClaimEvent(
                event_id="e" * 64,
                symbols=("AMD", "NVDA"),
                evidence_refs=evidence,
            ),
        ),
        observation_kind=FlowObservationKind.OBSERVED,
        inference_rule=None,
        evidence_refs=evidence,
    )


def _ref(namespace: str, record_id: str, observed_at: dt.datetime) -> EvidenceRef:
    return EvidenceRef(namespace=namespace, record_id=record_id, observed_at=observed_at)
