from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from dataclasses import dataclass
from typing import assert_never

from trading_agent.sec_edgar_models import (
    SecCollectionStatus,
    SecEdgarResponseError,
    SecSubmissionSourceKind,
    sec_additional_history_collection_id,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store_receipts import receipt_from_connection
from trading_agent.sec_edgar_store_support import filings_from_connection, run_from_connection
from trading_agent.sec_edgar_store_types import InvalidSecEdgarStoreError


@dataclass(frozen=True, slots=True)
class SecCapabilityEvidence:
    parent_run_id: str
    parent_status: SecCollectionStatus
    assessed_at: dt.datetime
    latest_event_received_at: dt.datetime | None
    latest_source_heartbeat_at: dt.datetime
    historical_from: dt.date | None
    declared_slice_count: int
    successful_slice_count: int
    failed_slice_count: int
    missing_slice_count: int
    filing_count: int


def sec_capability_evidence_from_connection(
    connection: sqlite3.Connection,
    collection_id: str,
    cik: str,
) -> SecCapabilityEvidence | None:
    run_id = hashlib.sha256(f"{collection_id}|{cik}".encode()).hexdigest()
    parent = run_from_connection(connection, run_id)
    if parent is None:
        return None
    match parent.source_kind:
        case SecSubmissionSourceKind.RECENT:
            pass
        case SecSubmissionSourceKind.ADDITIONAL_HISTORY:
            raise InvalidSecEdgarStoreError
        case unreachable:
            assert_never(unreachable)
    match parent.status:
        case SecCollectionStatus.FAILED:
            return SecCapabilityEvidence(
                parent_run_id=parent.run_id,
                parent_status=parent.status,
                assessed_at=parent.completed_at,
                latest_event_received_at=None,
                latest_source_heartbeat_at=parent.completed_at,
                historical_from=None,
                declared_slice_count=1,
                successful_slice_count=0,
                failed_slice_count=1,
                missing_slice_count=0,
                filing_count=0,
            )
        case SecCollectionStatus.SUCCESS:
            return _successful_parent_evidence(connection, parent.run_id)
        case unreachable:
            assert_never(unreachable)


def _successful_parent_evidence(
    connection: sqlite3.Connection,
    parent_run_id: str,
) -> SecCapabilityEvidence:
    parent = run_from_connection(connection, parent_run_id)
    if parent is None or parent.receipt_id is None:
        raise InvalidSecEdgarStoreError
    receipt = receipt_from_connection(connection, parent.collection_id, parent.cik)
    if receipt is None or receipt.response.receipt_id != parent.receipt_id:
        raise InvalidSecEdgarStoreError
    try:
        snapshot = parse_sec_submission_snapshot(receipt.response)
    except SecEdgarResponseError:
        raise InvalidSecEdgarStoreError from None
    parent_filings = filings_from_connection(connection, parent.run_id)
    successful = 1
    failed = 0
    terminal_times = [parent.completed_at]
    observed_times = [item.observed_at for item in parent_filings]
    filing_dates = [item.event.filing_date for item in parent_filings]
    filing_count = len(parent_filings)
    for history_file in snapshot.additional_history_files:
        child_id = sec_additional_history_collection_id(parent.receipt_id, history_file)
        child_run_id = hashlib.sha256(f"{child_id}|{parent.cik}".encode()).hexdigest()
        child = run_from_connection(connection, child_run_id)
        if child is None:
            continue
        terminal_times.append(child.completed_at)
        match child.status:
            case SecCollectionStatus.SUCCESS:
                successful += 1
                child_filings = filings_from_connection(connection, child.run_id)
                observed_times.extend(item.observed_at for item in child_filings)
                filing_dates.extend(item.event.filing_date for item in child_filings)
                filing_count += len(child_filings)
            case SecCollectionStatus.FAILED:
                failed += 1
            case unreachable:
                assert_never(unreachable)
    declared = 1 + len(snapshot.additional_history_files)
    return SecCapabilityEvidence(
        parent_run_id=parent.run_id,
        parent_status=parent.status,
        assessed_at=max(terminal_times),
        latest_event_received_at=max(observed_times) if observed_times else None,
        latest_source_heartbeat_at=max(terminal_times),
        historical_from=min(filing_dates) if filing_dates else None,
        declared_slice_count=declared,
        successful_slice_count=successful,
        failed_slice_count=failed,
        missing_slice_count=declared - successful - failed,
        filing_count=filing_count,
    )


__all__ = ("SecCapabilityEvidence", "sec_capability_evidence_from_connection")
