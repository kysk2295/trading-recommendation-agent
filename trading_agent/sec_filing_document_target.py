from __future__ import annotations

import hashlib
from pathlib import Path

from trading_agent.sec_edgar_capability_evidence import sec_capability_evidence_from_connection
from trading_agent.sec_edgar_models import (
    SecCollectionStatus,
    SecSubmissionSourceKind,
    normalize_sec_cik,
    sec_additional_history_collection_id,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store_receipts import receipt_from_connection
from trading_agent.sec_edgar_store_sql import sec_reader
from trading_agent.sec_edgar_store_support import filings_from_connection, run_from_connection
from trading_agent.sec_edgar_store_types import SecStoredFilingVersion
from trading_agent.sec_filing_document_models import (
    InvalidSecFilingDocumentTargetError,
    SecFilingDocumentTarget,
)


def read_sec_filing_document_targets(
    database: Path,
    parent_collection_id: str,
    cik: str,
    *,
    limit: int,
) -> tuple[SecFilingDocumentTarget, ...]:
    if not 1 <= limit <= 8:
        raise InvalidSecFilingDocumentTargetError
    try:
        cik = normalize_sec_cik(cik)
        with sec_reader(database) as connection:
            evidence = sec_capability_evidence_from_connection(
                connection,
                parent_collection_id,
                cik,
            )
            if evidence is None or evidence.parent_status is SecCollectionStatus.FAILED:
                return ()
            parent = run_from_connection(connection, evidence.parent_run_id)
            if parent is None or parent.receipt_id is None:
                raise InvalidSecFilingDocumentTargetError
            receipt = receipt_from_connection(connection, parent.collection_id, parent.cik)
            if receipt is None:
                raise InvalidSecFilingDocumentTargetError
            snapshot = parse_sec_submission_snapshot(receipt.response)
            run_ids = [parent.run_id]
            for history_file in snapshot.additional_history_files:
                child_id = sec_additional_history_collection_id(parent.receipt_id, history_file)
                child_run_id = hashlib.sha256(f"{child_id}|{cik}".encode()).hexdigest()
                child = run_from_connection(connection, child_run_id)
                if child is not None and child.status is SecCollectionStatus.SUCCESS:
                    if child.source_kind is not SecSubmissionSourceKind.ADDITIONAL_HISTORY:
                        raise InvalidSecFilingDocumentTargetError
                    run_ids.append(child.run_id)
            versions = tuple(version for run_id in run_ids for version in filings_from_connection(connection, run_id))
    except (TypeError, ValueError):
        raise InvalidSecFilingDocumentTargetError from None
    latest = _latest_accession_versions(versions)
    ordered = sorted(
        latest,
        key=lambda item: (item.event.accepted_at, item.observed_at, item.version_id),
        reverse=True,
    )
    return tuple(_target(item) for item in ordered[:limit])


def _latest_accession_versions(
    versions: tuple[SecStoredFilingVersion, ...],
) -> tuple[SecStoredFilingVersion, ...]:
    latest: dict[str, SecStoredFilingVersion] = {}
    for item in versions:
        existing = latest.get(item.event.accession_number)
        if existing is None or (item.observed_at, item.version_id) > (
            existing.observed_at,
            existing.version_id,
        ):
            latest[item.event.accession_number] = item
    return tuple(latest.values())


def _target(version: SecStoredFilingVersion) -> SecFilingDocumentTarget:
    return SecFilingDocumentTarget(
        source_version_id=version.version_id,
        source_receipt_id=version.receipt_id,
        cik=version.event.cik,
        accession_number=version.event.accession_number,
        primary_document=version.event.primary_document,
        accepted_at=version.event.accepted_at,
        observed_at=version.observed_at,
    )


__all__ = (
    "InvalidSecFilingDocumentTargetError",
    "SecFilingDocumentTarget",
    "read_sec_filing_document_targets",
)
