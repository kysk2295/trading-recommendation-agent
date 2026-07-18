from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import override

from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.kr_normalized_catalyst_validation import (
    KrNormalizedCatalystValidationError,
    validate_kr_keyword_research_input,
)
from trading_agent.kr_source_collection_models import (
    KrCatalystObservationReceipt,
    KrSourceCollectionRun,
)
from trading_agent.kr_theme_models import KrThemeClassification
from trading_agent.kr_theme_store import StoredKrCatalyst
from trading_agent.research_evidence_models import (
    ClaimStance,
    ExtractionMethod,
    JsonValue,
    ResearchClaimExtraction,
)


class KrKeywordResearchExtractionError(ValueError):
    def __init__(self) -> None:
        super().__init__("KR keyword research extraction is blocked")

    @override
    def __str__(self) -> str:
        return "KR keyword research extraction is blocked"

    @override
    def __repr__(self) -> str:
        return "KrKeywordResearchExtractionError()"


def extract_kr_keyword_research_claim(
    catalyst: StoredKrCatalyst,
    link: KrCatalystObservationReceipt,
    classification: KrThemeClassification,
    run: KrSourceCollectionRun,
) -> tuple[CanonicalEventEnvelope, ResearchClaimExtraction]:
    try:
        validated = validate_kr_keyword_research_input(catalyst, link, classification, run)
        entity_refs = tuple(
            CanonicalEntityRef(
                entity_type=CanonicalEntityType.INSTRUMENT,
                entity_id=f"krx:{item.symbol}",
            )
            for item in classification.related_symbols
        )
        event = CanonicalEventEnvelope(
            event_id=f"kr-theme-{classification.classification_id}",
            source_id=validated.source_id,
            provider_event_id=catalyst.record.source_record_id,
            entity_refs=entity_refs,
            event_type="theme_catalyst",
            event_time=catalyst.record.published_at,
            published_at=catalyst.record.published_at,
            received_at=catalyst.record.first_observed_at,
            normalized_at=classification.classified_at,
            operation=CanonicalEventOperation.ORIGINAL,
            raw_receipt_ref=link.receipt_id,
            content_hash=catalyst.record.payload_sha256,
            quality_flags=("keyword_classified", "normalized"),
        )
        claim_key = f"kr.theme.{_text_hash(classification.theme_name)[:24]}"
        confidence_bps = _confidence_bps(classification.confidence)
        extraction = ResearchClaimExtraction(
            event_id=event.event_id,
            event_content_hash=event.content_hash,
            source_id=event.source_id,
            raw_receipt_ref=event.raw_receipt_ref,
            entity_refs=event.entity_refs,
            claim_key=claim_key,
            claim_kind="theme.catalyst",
            stance=ClaimStance.SUPPORTS,
            confidence_bps=confidence_bps,
            extracted_at=classification.classified_at,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            extractor_version=classification.classifier_version,
            model_version=None,
            prompt_version=None,
            output_sha256=_output_hash(catalyst, link, classification, event),
        )
        return event, extraction
    except (
        AttributeError,
        KrNormalizedCatalystValidationError,
        TypeError,
        ValueError,
    ):
        raise KrKeywordResearchExtractionError from None


def _confidence_bps(confidence: Decimal) -> int:
    scaled = confidence * Decimal(10_000)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise KrKeywordResearchExtractionError
    return int(integral)


def _output_hash(
    catalyst: StoredKrCatalyst,
    link: KrCatalystObservationReceipt,
    classification: KrThemeClassification,
    event: CanonicalEventEnvelope,
) -> str:
    payload: dict[str, JsonValue] = {
        "catalyst_content_hash": catalyst.record.payload_sha256,
        "classification": classification.model_dump(mode="json"),
        "event_id": event.event_id,
        "item_index": link.item_index,
        "receipt_id": link.receipt_id,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _text_hash(value: str | None) -> str:
    if type(value) is not str or not value:
        raise KrKeywordResearchExtractionError
    return hashlib.sha256(value.casefold().encode()).hexdigest()


__all__ = (
    "KrKeywordResearchExtractionError",
    "extract_kr_keyword_research_claim",
)
