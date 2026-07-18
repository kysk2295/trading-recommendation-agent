from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.data_capability_models import DataSourceId
from trading_agent.research_evidence_models import (
    ClaimCorroborationStatus,
    ClaimNoveltyStatus,
    ClaimStance,
    ExtractionMethod,
    ResearchClaimExtraction,
)
from trading_agent.research_evidence_read_model import (
    ResearchEvidenceReadModelError,
    build_research_evidence_read_model,
)

AS_OF = dt.datetime(2026, 7, 17, 15, 0, tzinfo=dt.UTC)
ENTITY = CanonicalEntityRef(
    entity_type=CanonicalEntityType.INSTRUMENT,
    entity_id="us-eq-fixture-0001",
)


def test_independent_sources_build_corroborated_novel_claim_without_raw_content() -> None:
    events = (
        _event("event-news-1", "fixture", "news", minutes_ago=10, digest="a" * 64),
        _event("event-filing-1", "fixture", "filing", minutes_ago=5, digest="b" * 64),
    )
    extractions = tuple(_extraction(event, stance=ClaimStance.REPORTS) for event in events)

    model = build_research_evidence_read_model(
        events,
        extractions,
        as_of=AS_OF,
        current_window=dt.timedelta(hours=1),
        baseline_window=dt.timedelta(hours=6),
        burst_threshold_bps=20_000,
    )

    assert len(model.claims) == 1
    claim = model.claims[0]
    assert claim.corroboration_status is ClaimCorroborationStatus.CORROBORATED
    assert claim.novelty_status is ClaimNoveltyStatus.NOVEL
    assert claim.current_evidence_count == 2
    assert claim.baseline_evidence_count == 0
    assert claim.independent_source_count == 2
    assert claim.source_ids == ("fixture/filing", "fixture/news")
    assert all("receipt" not in key for key in claim.model_dump(mode="json"))


def test_support_and_dispute_are_conflicted_and_rate_spike_is_burst() -> None:
    events = (
        _event("event-old-1", "fixture", "news", minutes_ago=180, digest="a" * 64),
        _event("event-new-1", "fixture", "news", minutes_ago=30, digest="b" * 64),
        _event("event-new-2", "fixture", "filing", minutes_ago=20, digest="c" * 64),
        _event("event-new-3", "fixture", "social", minutes_ago=10, digest="d" * 64),
    )
    extractions = tuple(
        _extraction(
            event,
            stance=ClaimStance.DISPUTES if event.event_id == "event-new-3" else ClaimStance.SUPPORTS,
        )
        for event in events
    )

    model = build_research_evidence_read_model(
        events,
        extractions,
        as_of=AS_OF,
        current_window=dt.timedelta(hours=1),
        baseline_window=dt.timedelta(hours=3),
        burst_threshold_bps=20_000,
    )

    claim = model.claims[0]
    assert claim.corroboration_status is ClaimCorroborationStatus.CONFLICTED
    assert claim.novelty_status is ClaimNoveltyStatus.BURST
    assert claim.current_evidence_count == 3
    assert claim.baseline_evidence_count == 1
    assert len(claim.baseline_evidence_ids) == 1
    assert claim.supporting_evidence_count == 2
    assert claim.disputing_evidence_count == 1


def test_independent_speculation_remains_unconfirmed() -> None:
    events = (
        _event("event-news-1", "fixture", "news", minutes_ago=10, digest="a" * 64),
        _event("event-social-1", "fixture", "social", minutes_ago=5, digest="b" * 64),
    )
    extractions = tuple(_extraction(event, stance=ClaimStance.SPECULATIVE) for event in events)

    model = build_research_evidence_read_model(
        events,
        extractions,
        as_of=AS_OF,
        current_window=dt.timedelta(hours=1),
        baseline_window=dt.timedelta(hours=6),
        burst_threshold_bps=20_000,
    )

    assert model.claims[0].corroboration_status is ClaimCorroborationStatus.UNCONFIRMED


@pytest.mark.parametrize("mutation", ("source", "hash", "receipt", "entity", "future", "tombstone"))
def test_rejects_extraction_not_bound_to_exact_active_event(mutation: str) -> None:
    event = _event("event-news-1", "fixture", "news", minutes_ago=10, digest="a" * 64)
    extraction = _extraction(event, stance=ClaimStance.REPORTS)
    event_payload = event.model_dump(mode="python")
    extraction_payload = extraction.model_dump(mode="python")
    if mutation == "source":
        extraction_payload["source_id"] = DataSourceId(provider="other", feed="news")
    elif mutation == "hash":
        extraction_payload["event_content_hash"] = "f" * 64
    elif mutation == "receipt":
        extraction_payload["raw_receipt_ref"] = "receipt:other"
    elif mutation == "entity":
        extraction_payload["entity_refs"] = (
            CanonicalEntityRef(entity_type=CanonicalEntityType.TOPIC, entity_id="other-topic"),
        )
    elif mutation == "future":
        extraction_payload["extracted_at"] = AS_OF + dt.timedelta(seconds=1)
    else:
        event_payload["operation"] = CanonicalEventOperation.TOMBSTONE
        event_payload["correction_of"] = "event-old"

    with pytest.raises(ResearchEvidenceReadModelError):
        build_research_evidence_read_model(
            (CanonicalEventEnvelope.model_validate(event_payload),),
            (ResearchClaimExtraction.model_validate(extraction_payload),),
            as_of=AS_OF,
            current_window=dt.timedelta(hours=1),
            baseline_window=dt.timedelta(hours=6),
            burst_threshold_bps=20_000,
        )


def test_llm_extraction_requires_model_prompt_and_output_hash() -> None:
    event = _event("event-news-1", "fixture", "news", minutes_ago=10, digest="a" * 64)
    payload = _extraction(event, stance=ClaimStance.REPORTS).model_dump(mode="python")
    payload["extraction_method"] = ExtractionMethod.LLM

    with pytest.raises(ValidationError):
        ResearchClaimExtraction.model_validate(payload)

    payload["model_version"] = "model-v1"
    payload["prompt_version"] = "claim-prompt-v1"
    assert ResearchClaimExtraction.model_validate(payload).model_version == "model-v1"


def _event(
    event_id: str,
    provider: str,
    feed: str,
    *,
    minutes_ago: int,
    digest: str,
) -> CanonicalEventEnvelope:
    observed_at = AS_OF - dt.timedelta(minutes=minutes_ago)
    return CanonicalEventEnvelope(
        event_id=event_id,
        source_id=DataSourceId(provider=provider, feed=feed),
        provider_event_id=f"provider:{event_id}",
        entity_refs=(ENTITY,),
        event_type="market_claim_input",
        event_time=observed_at,
        published_at=observed_at,
        provider_time=observed_at,
        received_at=observed_at,
        normalized_at=observed_at,
        sequence_or_offset=None,
        operation=CanonicalEventOperation.ORIGINAL,
        raw_receipt_ref=f"receipt:{event_id}",
        content_hash=digest,
        quality_flags=("complete",),
    )


def _extraction(
    event: CanonicalEventEnvelope,
    *,
    stance: ClaimStance,
) -> ResearchClaimExtraction:
    return ResearchClaimExtraction(
        event_id=event.event_id,
        event_content_hash=event.content_hash,
        source_id=event.source_id,
        raw_receipt_ref=event.raw_receipt_ref,
        entity_refs=event.entity_refs,
        claim_key="issuer.product_catalyst",
        claim_kind="corporate_catalyst",
        stance=stance,
        confidence_bps=8_000,
        extracted_at=event.normalized_at,
        extraction_method=ExtractionMethod.DETERMINISTIC,
        extractor_version="fixture-extractor-v1",
        model_version=None,
        prompt_version=None,
        output_sha256=event.content_hash,
    )
