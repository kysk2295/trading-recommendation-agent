from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Final, override

from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.data_capability_models import DataSourceId
from trading_agent.research_evidence_models import (
    ClaimStance,
    ExtractionMethod,
    JsonValue,
    ResearchClaimExtraction,
)
from trading_agent.sec_filing_document_models import (
    SecFilingDocumentRawResponse,
    SecFilingDocumentRun,
    SecFilingDocumentStatus,
    SecFilingDocumentTarget,
)
from trading_agent.us_news_research_extraction import us_symbol_attention_claim_key

_EXTRACTOR_VERSION: Final = "us-sec-filing-document-v1"
_SOURCE_ID: Final = DataSourceId(provider="sec", feed="edgar_documents")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


class UsSecFilingDocumentResearchExtractionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US SEC filing document research extraction is blocked"

    @override
    def __repr__(self) -> str:
        return "UsSecFilingDocumentResearchExtractionError()"


def extract_us_sec_filing_document_attention_claim(
    target: SecFilingDocumentTarget,
    run: SecFilingDocumentRun,
    response: SecFilingDocumentRawResponse,
    *,
    symbol: str,
    normalized_at: dt.datetime,
) -> tuple[CanonicalEventEnvelope, ResearchClaimExtraction]:
    """Bind a successful raw document receipt to symbol attention without body NLP."""
    try:
        if (
            type(target) is not SecFilingDocumentTarget
            or type(run) is not SecFilingDocumentRun
            or type(response) is not SecFilingDocumentRawResponse
            or type(symbol) is not str
            or _SYMBOL.fullmatch(symbol) is None
            or type(normalized_at) is not dt.datetime
            or normalized_at.tzinfo is None
            or normalized_at.utcoffset() is None
            or run.status is not SecFilingDocumentStatus.SUCCESS
            or run.failure_code is not None
            or run.receipt_id is None
            or run.target.target_id != target.target_id
            or response.target_id != target.target_id
            or response.receipt_id != run.receipt_id
            or response.status_code != 200
            or run.byte_count <= 0
            or run.byte_count != len(response.raw_payload)
            or normalized_at < run.completed_at
            or normalized_at < response.received_at
            or response.received_at < target.accepted_at
        ):
            raise UsSecFilingDocumentResearchExtractionError
        entity = CanonicalEntityRef(
            entity_type=CanonicalEntityType.INSTRUMENT,
            entity_id=f"us:{symbol.casefold()}",
        )
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "accession_number": target.accession_number,
                    "byte_count": run.byte_count,
                    "cik": target.cik,
                    "receipt_id": run.receipt_id,
                    "symbol": symbol,
                    "target_id": target.target_id,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        event = CanonicalEventEnvelope(
            event_id=f"us-sec-doc-{content_hash[:20]}",
            source_id=_SOURCE_ID,
            provider_event_id=target.accession_number.lower(),
            entity_refs=(entity,),
            event_type="regulatory_document",
            event_time=target.accepted_at,
            published_at=target.accepted_at,
            received_at=response.received_at,
            normalized_at=normalized_at,
            operation=CanonicalEventOperation.ORIGINAL,
            raw_receipt_ref=run.receipt_id,
            content_hash=content_hash,
            quality_flags=tuple(
                sorted(
                    (
                        "document_receipt",
                        "no_body_nlp",
                        "no_sentiment",
                        "symbol_bound",
                    )
                )
            ),
        )
        extraction = ResearchClaimExtraction(
            event_id=event.event_id,
            event_content_hash=event.content_hash,
            source_id=event.source_id,
            raw_receipt_ref=event.raw_receipt_ref,
            entity_refs=event.entity_refs,
            claim_key=us_symbol_attention_claim_key(symbol),
            claim_kind="symbol.attention",
            stance=ClaimStance.REPORTS,
            confidence_bps=5_500,
            extracted_at=normalized_at,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            extractor_version=_EXTRACTOR_VERSION,
            model_version=None,
            prompt_version=None,
            output_sha256=_output_hash(target, run, symbol, event),
        )
        return event, extraction
    except (AttributeError, TypeError, ValueError):
        raise UsSecFilingDocumentResearchExtractionError from None


def _output_hash(
    target: SecFilingDocumentTarget,
    run: SecFilingDocumentRun,
    symbol: str,
    event: CanonicalEventEnvelope,
) -> str:
    if run.receipt_id is None:
        raise UsSecFilingDocumentResearchExtractionError
    payload: dict[str, JsonValue] = {
        "accession_number": target.accession_number,
        "byte_count": run.byte_count,
        "cik": target.cik,
        "event_id": event.event_id,
        "extractor_version": _EXTRACTOR_VERSION,
        "receipt_id": run.receipt_id,
        "symbol": symbol,
        "target_id": target.target_id,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = (
    "UsSecFilingDocumentResearchExtractionError",
    "extract_us_sec_filing_document_attention_claim",
)
