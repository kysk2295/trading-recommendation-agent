from __future__ import annotations

import datetime as dt

import pytest

from trading_agent.alpaca_news_models import AlpacaNewsArticle
from trading_agent.research_evidence_models import ClaimCorroborationStatus, ClaimStance
from trading_agent.research_evidence_read_model import build_research_evidence_read_model
from trading_agent.sec_edgar_models import SecFilingEvent
from trading_agent.us_news_research_extraction import (
    UsNewsResearchExtractionError,
    extract_us_news_attention_claim,
    us_symbol_attention_claim_key,
)
from trading_agent.us_sec_filing_research_extraction import (
    UsSecFilingResearchExtractionError,
    extract_us_sec_filing_attention_claim,
)

UTC = dt.UTC
CREATED = dt.datetime(2026, 7, 21, 14, 0, tzinfo=UTC)
UPDATED = dt.datetime(2026, 7, 21, 14, 1, tzinfo=UTC)
RECEIVED = dt.datetime(2026, 7, 21, 14, 2, tzinfo=UTC)
NORMALIZED = dt.datetime(2026, 7, 21, 14, 3, tzinfo=UTC)
RECEIPT_NEWS = "a" * 64
RECEIPT_SEC = "b" * 64


def test_alpaca_news_and_sec_filing_corroborate_on_shared_symbol_attention() -> None:
    article = _article()
    filing = _filing()

    news_event, news_claim = extract_us_news_attention_claim(
        article,
        receipt_id=RECEIPT_NEWS,
        symbol="AAPL",
        normalized_at=NORMALIZED,
    )
    sec_event, sec_claim = extract_us_sec_filing_attention_claim(
        filing,
        receipt_id=RECEIPT_SEC,
        symbol="AAPL",
        normalized_at=NORMALIZED + dt.timedelta(seconds=30),
    )
    model = build_research_evidence_read_model(
        (news_event, sec_event),
        (news_claim, sec_claim),
        as_of=NORMALIZED + dt.timedelta(minutes=1),
        current_window=dt.timedelta(hours=1),
        baseline_window=dt.timedelta(days=1),
        burst_threshold_bps=20_000,
    )

    assert news_event.source_id.canonical_id == "alpaca/news"
    assert sec_event.source_id.canonical_id == "sec/edgar_submissions"
    assert news_claim.claim_key == sec_claim.claim_key == us_symbol_attention_claim_key("AAPL")
    assert news_claim.claim_kind == sec_claim.claim_kind == "symbol.attention"
    assert news_claim.stance is ClaimStance.REPORTS
    assert news_claim.entity_refs == sec_claim.entity_refs
    assert news_event.entity_refs == sec_event.entity_refs
    assert "no_sentiment" in news_event.quality_flags
    assert "form_8k" in sec_event.quality_flags
    assert len(model.claims) == 1
    assert model.claims[0].corroboration_status is ClaimCorroborationStatus.CORROBORATED
    assert model.claims[0].independent_source_count == 2


def test_news_extraction_rejects_unknown_symbol_and_stale_normalization() -> None:
    article = _article()
    with pytest.raises(UsNewsResearchExtractionError):
        extract_us_news_attention_claim(
            article,
            receipt_id=RECEIPT_NEWS,
            symbol="MSFT",
            normalized_at=NORMALIZED,
        )
    with pytest.raises(UsNewsResearchExtractionError):
        extract_us_news_attention_claim(
            article,
            receipt_id=RECEIPT_NEWS,
            symbol="AAPL",
            normalized_at=CREATED - dt.timedelta(seconds=1),
        )


def test_sec_extraction_rejects_bad_receipt_and_pre_accept_normalization() -> None:
    filing = _filing()
    with pytest.raises(UsSecFilingResearchExtractionError):
        extract_us_sec_filing_attention_claim(
            filing,
            receipt_id="not-a-hash",
            symbol="AAPL",
            normalized_at=NORMALIZED,
        )
    with pytest.raises(UsSecFilingResearchExtractionError):
        extract_us_sec_filing_attention_claim(
            filing,
            receipt_id=RECEIPT_SEC,
            symbol="AAPL",
            normalized_at=filing.accepted_at - dt.timedelta(seconds=1),
        )


def _article() -> AlpacaNewsArticle:
    return AlpacaNewsArticle(
        provider_article_id=42,
        headline="Apple announces product event",
        source="benzinga",
        symbols=("AAPL",),
        created_at=CREATED,
        updated_at=UPDATED,
        url="https://example.com/news/aapl-event",
    )


def _filing() -> SecFilingEvent:
    return SecFilingEvent(
        cik="0000320193",
        accession_number="0000320193-26-000100",
        form="8-K",
        filing_date=dt.date(2026, 7, 21),
        report_date=dt.date(2026, 7, 21),
        accepted_at=RECEIVED,
        primary_document="aapl-20260721.htm",
        primary_document_description="8-K",
        items=("2.02",),
        size_bytes=1200,
        is_xbrl=False,
        is_inline_xbrl=False,
    )
