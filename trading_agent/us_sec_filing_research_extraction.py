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
from trading_agent.sec_edgar_models import SecFilingEvent
from trading_agent.us_news_research_extraction import us_symbol_attention_claim_key

_EXTRACTOR_VERSION: Final = "us-sec-filing-attention-v1"
_SOURCE_ID: Final = DataSourceId(provider="sec", feed="edgar_submissions")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_CIK = re.compile(r"^[0-9]{10}$")
_FORM_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")


class UsSecFilingResearchExtractionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US SEC filing research extraction is blocked"

    @override
    def __repr__(self) -> str:
        return "UsSecFilingResearchExtractionError()"


def extract_us_sec_filing_attention_claim(
    filing: SecFilingEvent,
    *,
    receipt_id: str,
    symbol: str,
    normalized_at: dt.datetime,
) -> tuple[CanonicalEventEnvelope, ResearchClaimExtraction]:
    try:
        if (
            type(filing) is not SecFilingEvent
            or type(receipt_id) is not str
            or _HEX64.fullmatch(receipt_id) is None
            or type(symbol) is not str
            or _SYMBOL.fullmatch(symbol) is None
            or _CIK.fullmatch(filing.cik) is None
            or type(normalized_at) is not dt.datetime
            or normalized_at.tzinfo is None
            or normalized_at.utcoffset() is None
            or normalized_at < filing.accepted_at
        ):
            raise UsSecFilingResearchExtractionError
        form_slug = _form_slug(filing.form)
        # Keep instrument-only entity refs so news+SEC can share claim_key grouping.
        entity = CanonicalEntityRef(
            entity_type=CanonicalEntityType.INSTRUMENT,
            entity_id=f"us:{symbol.casefold()}",
        )
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "accession_number": filing.accession_number,
                    "cik": filing.cik,
                    "form": filing.form,
                    "receipt_id": receipt_id,
                    "symbol": symbol,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        event = CanonicalEventEnvelope(
            event_id=f"us-sec-{content_hash[:24]}",
            source_id=_SOURCE_ID,
            provider_event_id=filing.accession_number.lower(),
            entity_refs=(entity,),
            event_type="regulatory_filing",
            event_time=filing.accepted_at,
            published_at=filing.accepted_at,
            received_at=normalized_at,
            normalized_at=normalized_at,
            operation=CanonicalEventOperation.ORIGINAL,
            raw_receipt_ref=receipt_id,
            content_hash=content_hash,
            quality_flags=tuple(sorted(("no_sentiment", f"form_{form_slug}", "symbol_bound"))),
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
            confidence_bps=6_000,
            extracted_at=normalized_at,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            extractor_version=_EXTRACTOR_VERSION,
            model_version=None,
            prompt_version=None,
            output_sha256=_output_hash(filing, receipt_id, symbol, event),
        )
        return event, extraction
    except (AttributeError, TypeError, ValueError):
        raise UsSecFilingResearchExtractionError from None


def _form_slug(form: str) -> str:
    # Used only inside quality flag "form_{slug}", which must start with a letter.
    slug = "".join(ch for ch in form.casefold() if ch.isalnum())
    if not slug:
        slug = "unknown"
    if _FORM_SLUG.fullmatch(slug) is None:
        raise UsSecFilingResearchExtractionError
    return slug


def _output_hash(
    filing: SecFilingEvent,
    receipt_id: str,
    symbol: str,
    event: CanonicalEventEnvelope,
) -> str:
    payload: dict[str, JsonValue] = {
        "accession_number": filing.accession_number,
        "cik": filing.cik,
        "event_id": event.event_id,
        "extractor_version": _EXTRACTOR_VERSION,
        "form": filing.form,
        "receipt_id": receipt_id,
        "symbol": symbol,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = (
    "UsSecFilingResearchExtractionError",
    "extract_us_sec_filing_attention_claim",
)
