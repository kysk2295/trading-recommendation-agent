from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Final, override

from trading_agent.alpaca_news_models import AlpacaNewsArticle
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

_EXTRACTOR_VERSION: Final = "us-news-attention-v1"
_SOURCE_ID: Final = DataSourceId(provider="alpaca", feed="news")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


class UsNewsResearchExtractionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news research extraction is blocked"

    @override
    def __repr__(self) -> str:
        return "UsNewsResearchExtractionError()"


def extract_us_news_attention_claim(
    article: AlpacaNewsArticle,
    *,
    receipt_id: str,
    symbol: str,
    normalized_at: dt.datetime,
) -> tuple[CanonicalEventEnvelope, ResearchClaimExtraction]:
    try:
        if (
            type(article) is not AlpacaNewsArticle
            or type(receipt_id) is not str
            or _HEX64.fullmatch(receipt_id) is None
            or type(symbol) is not str
            or _SYMBOL.fullmatch(symbol) is None
            or symbol not in article.symbols
            or type(normalized_at) is not dt.datetime
            or normalized_at.tzinfo is None
            or normalized_at.utcoffset() is None
            or normalized_at < article.updated_at
            or normalized_at < article.created_at
        ):
            raise UsNewsResearchExtractionError
        entity = CanonicalEntityRef(
            entity_type=CanonicalEntityType.INSTRUMENT,
            entity_id=f"us:{symbol.casefold()}",
        )
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "event_id": article.event_id,
                    "receipt_id": receipt_id,
                    "symbol": symbol,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        event_id = f"us-news-{content_hash[:24]}"
        event = CanonicalEventEnvelope(
            event_id=event_id,
            source_id=_SOURCE_ID,
            provider_event_id=str(article.provider_article_id),
            entity_refs=(entity,),
            event_type="news_attention",
            event_time=article.created_at,
            published_at=article.created_at,
            provider_time=article.updated_at,
            received_at=normalized_at,
            normalized_at=normalized_at,
            operation=CanonicalEventOperation.ORIGINAL,
            raw_receipt_ref=receipt_id,
            content_hash=content_hash,
            quality_flags=("headline_present", "no_sentiment", "symbol_linked"),
        )
        claim_key = us_symbol_attention_claim_key(symbol)
        extraction = ResearchClaimExtraction(
            event_id=event.event_id,
            event_content_hash=event.content_hash,
            source_id=event.source_id,
            raw_receipt_ref=event.raw_receipt_ref,
            entity_refs=event.entity_refs,
            claim_key=claim_key,
            claim_kind="symbol.attention",
            stance=ClaimStance.REPORTS,
            confidence_bps=5_000,
            extracted_at=normalized_at,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            extractor_version=_EXTRACTOR_VERSION,
            model_version=None,
            prompt_version=None,
            output_sha256=_output_hash(article, receipt_id, symbol, event),
        )
        return event, extraction
    except (AttributeError, TypeError, ValueError):
        raise UsNewsResearchExtractionError from None


def us_symbol_attention_claim_key(symbol: str) -> str:
    if type(symbol) is not str or _SYMBOL.fullmatch(symbol) is None:
        raise UsNewsResearchExtractionError
    return f"us.symbol.attention.{symbol.casefold().replace('.', '_').replace('-', '_')}"


def _output_hash(
    article: AlpacaNewsArticle,
    receipt_id: str,
    symbol: str,
    event: CanonicalEventEnvelope,
) -> str:
    payload: dict[str, JsonValue] = {
        "article_event_id": article.event_id,
        "event_id": event.event_id,
        "extractor_version": _EXTRACTOR_VERSION,
        "receipt_id": receipt_id,
        "source": article.source,
        "symbol": symbol,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = (
    "UsNewsResearchExtractionError",
    "extract_us_news_attention_claim",
    "us_symbol_attention_claim_key",
)
