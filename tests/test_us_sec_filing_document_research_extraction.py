from __future__ import annotations

import datetime as dt

import pytest

from trading_agent.research_evidence_models import ClaimCorroborationStatus
from trading_agent.research_evidence_read_model import build_research_evidence_read_model
from trading_agent.sec_edgar_models import SecFilingEvent
from trading_agent.sec_filing_document_models import (
    SecFilingDocumentRawResponse,
    SecFilingDocumentRun,
    SecFilingDocumentStatus,
    SecFilingDocumentTarget,
)
from trading_agent.us_sec_filing_document_research_extraction import (
    UsSecFilingDocumentResearchExtractionError,
    extract_us_sec_filing_document_attention_claim,
)
from trading_agent.us_sec_filing_research_extraction import extract_us_sec_filing_attention_claim

UTC = dt.UTC
ACCEPTED = dt.datetime(2026, 7, 21, 14, 0, tzinfo=UTC)
OBSERVED = dt.datetime(2026, 7, 21, 14, 5, tzinfo=UTC)
RECEIVED = dt.datetime(2026, 7, 21, 14, 6, tzinfo=UTC)
COMPLETED = dt.datetime(2026, 7, 21, 14, 7, tzinfo=UTC)
NORMALIZED = dt.datetime(2026, 7, 21, 14, 8, tzinfo=UTC)
META_RECEIPT = "c" * 64


def test_document_and_submission_claims_corroborate_on_symbol() -> None:
    target, run, response = _success_document()
    filing = _filing()
    doc_event, doc_claim = extract_us_sec_filing_document_attention_claim(
        target,
        run,
        response,
        symbol="AAPL",
        normalized_at=NORMALIZED,
    )
    meta_event, meta_claim = extract_us_sec_filing_attention_claim(
        filing,
        receipt_id=META_RECEIPT,
        symbol="AAPL",
        normalized_at=NORMALIZED + dt.timedelta(seconds=1),
    )
    model = build_research_evidence_read_model(
        (doc_event, meta_event),
        (doc_claim, meta_claim),
        as_of=NORMALIZED + dt.timedelta(minutes=1),
        current_window=dt.timedelta(hours=1),
        baseline_window=dt.timedelta(days=1),
        burst_threshold_bps=20_000,
    )

    assert doc_event.source_id.canonical_id == "sec/edgar_documents"
    assert meta_event.source_id.canonical_id == "sec/edgar_submissions"
    assert doc_claim.claim_key == meta_claim.claim_key
    assert "document_receipt" in doc_event.quality_flags
    assert "no_body_nlp" in doc_event.quality_flags
    assert model.claims[0].corroboration_status is ClaimCorroborationStatus.CORROBORATED
    assert model.claims[0].independent_source_count == 2


def test_document_extraction_rejects_failed_or_mismatched_runs() -> None:
    target, run, response = _success_document()
    failed = SecFilingDocumentRun(
        target=target,
        started_at=ACCEPTED,
        completed_at=COMPLETED,
        status=SecFilingDocumentStatus.FAILED,
        failure_code="http_status",
        receipt_id=response.receipt_id,
        byte_count=len(response.raw_payload),
    )
    with pytest.raises(UsSecFilingDocumentResearchExtractionError):
        extract_us_sec_filing_document_attention_claim(
            target,
            failed,
            response,
            symbol="AAPL",
            normalized_at=NORMALIZED,
        )
    with pytest.raises(UsSecFilingDocumentResearchExtractionError):
        extract_us_sec_filing_document_attention_claim(
            target,
            run,
            response,
            symbol="AAPL",
            normalized_at=RECEIVED - dt.timedelta(seconds=1),
        )


def _success_document() -> tuple[
    SecFilingDocumentTarget,
    SecFilingDocumentRun,
    SecFilingDocumentRawResponse,
]:
    target = SecFilingDocumentTarget(
        source_version_id="d" * 64,
        source_receipt_id="e" * 64,
        cik="0000320193",
        accession_number="0000320193-26-000100",
        primary_document="aapl-20260721.htm",
        accepted_at=ACCEPTED,
        observed_at=OBSERVED,
    )
    payload = b"<html>8-K body bytes</html>"
    response = SecFilingDocumentRawResponse(
        target_id=target.target_id,
        received_at=RECEIVED,
        status_code=200,
        content_type="text/html",
        raw_payload=payload,
    )
    run = SecFilingDocumentRun(
        target=target,
        started_at=ACCEPTED,
        completed_at=COMPLETED,
        status=SecFilingDocumentStatus.SUCCESS,
        failure_code=None,
        receipt_id=response.receipt_id,
        byte_count=len(payload),
    )
    return target, run, response


def _filing() -> SecFilingEvent:
    return SecFilingEvent(
        cik="0000320193",
        accession_number="0000320193-26-000100",
        form="8-K",
        filing_date=dt.date(2026, 7, 21),
        report_date=dt.date(2026, 7, 21),
        accepted_at=ACCEPTED,
        primary_document="aapl-20260721.htm",
        primary_document_description="8-K",
        items=("2.02",),
        size_bytes=1200,
        is_xbrl=False,
        is_inline_xbrl=False,
    )
