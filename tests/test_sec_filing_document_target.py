from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from trading_agent.sec_edgar_collection import collect_sec_submissions
from trading_agent.sec_edgar_models import SecSubmissionRawResponse
from trading_agent.sec_edgar_store import SecEdgarStore
from trading_agent.sec_filing_document_target import (
    InvalidSecFilingDocumentTargetError,
    SecFilingDocumentTarget,
    read_sec_filing_document_targets,
)

FIXTURE = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
CIK = "0000320193"
COLLECTION_ID = "sec-document-target-001"
RECEIVED_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)
COMPLETED_AT = RECEIVED_AT + dt.timedelta(seconds=1)


class _Fetcher:
    def fetch_submissions(self, collection_id: str, cik: str) -> SecSubmissionRawResponse:
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=RECEIVED_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=FIXTURE.read_bytes(),
        )


def test_target_uses_issuer_cik_folder_with_filing_agent_accession() -> None:
    target = SecFilingDocumentTarget(
        source_version_id="1" * 64,
        source_receipt_id="2" * 64,
        cik=CIK,
        accession_number="0000000001-26-000101",
        primary_document="exm-20260719.htm",
        accepted_at=dt.datetime(2026, 7, 20, 13, 31, 2, tzinfo=dt.UTC),
        observed_at=RECEIVED_AT,
    )

    assert target.archive_path == ("/Archives/edgar/data/320193/000000000126000101/exm-20260719.htm")
    assert len(target.target_id) == 64
    assert "exm-20260719.htm" not in repr(target)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_version_id", "not-a-hash"),
        ("primary_document", "../private.txt"),
        ("accession_number", "0000320193/26/000101"),
    ),
)
def test_target_rejects_unbound_identity_material(field: str, value: str) -> None:
    values: dict[str, object] = {
        "source_version_id": "1" * 64,
        "source_receipt_id": "2" * 64,
        "cik": CIK,
        "accession_number": "0000320193-26-000101",
        "primary_document": "exm-20260719.htm",
        "accepted_at": dt.datetime(2026, 7, 20, 13, 31, 2, tzinfo=dt.UTC),
        "observed_at": RECEIVED_AT,
    }
    values[field] = value

    with pytest.raises(InvalidSecFilingDocumentTargetError):
        _ = SecFilingDocumentTarget(**values)  # type: ignore[arg-type]


def test_reader_projects_newest_bounded_targets_from_validated_store(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "ledger" / "sec.sqlite3")
    _ = collect_sec_submissions(
        _Fetcher(),
        store,
        COLLECTION_ID,
        CIK,
        _clock=lambda: COMPLETED_AT,
    )

    targets = read_sec_filing_document_targets(
        store.path,
        COLLECTION_ID,
        CIK,
        limit=1,
    )

    assert len(targets) == 1
    assert targets[0].accession_number == "0000320193-26-000101"
    assert targets[0].primary_document == "exm-20260719.htm"
    receipt = store.receipt_for_collection(
        COLLECTION_ID,
        CIK,
    )
    assert receipt is not None
    assert targets[0].source_receipt_id == receipt.response.receipt_id


@pytest.mark.parametrize("limit", (0, 9))
def test_reader_rejects_unbounded_limit_before_store_access(tmp_path: Path, limit: int) -> None:
    with pytest.raises(InvalidSecFilingDocumentTargetError):
        _ = read_sec_filing_document_targets(
            tmp_path / "missing.sqlite3",
            COLLECTION_ID,
            CIK,
            limit=limit,
        )
