from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from trading_agent.sec_edgar_client import SecEdgarTransportError
from trading_agent.sec_edgar_models import (
    SecCollectionStatus,
    SecEdgarResponseError,
    SecSubmissionRawResponse,
    SecSubmissionRun,
    SecSubmissionSnapshot,
    normalize_sec_cik,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store import SecEdgarStore


class SecSubmissionFetcher(Protocol):
    def fetch_submissions(self, collection_id: str, cik: str) -> SecSubmissionRawResponse: ...


@dataclass(frozen=True, slots=True)
class SecCollectionResult:
    run: SecSubmissionRun
    receipt_created: bool
    filing_count: int
    new_filing_version_count: int
    replayed: bool


def resume_sec_collection(store: SecEdgarStore, collection_id: str, cik: str) -> SecCollectionResult | None:
    cik = normalize_sec_cik(cik)
    existing = store.collection_run(collection_id, cik)
    if existing is None:
        return None
    filings = store.filings_for_run(existing.run_id)
    if len(filings) != existing.filing_count:
        raise ValueError("incomplete SEC EDGAR terminal run")
    return SecCollectionResult(existing, False, existing.filing_count, 0, True)


def collect_sec_submissions(
    fetcher: SecSubmissionFetcher,
    store: SecEdgarStore,
    collection_id: str,
    cik: str,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    _parser: Callable[[SecSubmissionRawResponse], SecSubmissionSnapshot] = parse_sec_submission_snapshot,
) -> SecCollectionResult:
    resumed = resume_sec_collection(store, collection_id, cik)
    if resumed is not None:
        return resumed
    cik = normalize_sec_cik(cik)
    started_at = _clock()
    try:
        response = fetcher.fetch_submissions(collection_id, cik)
    except SecEdgarTransportError:
        run = _failed_run(collection_id, cik, started_at, _clock(), "transport", None)
        _ = store.append_failed_run(run)
        return SecCollectionResult(run, False, 0, 0, False)
    receipt = store.append_receipt(response)
    completed_at = max(started_at, response.received_at, _clock())
    try:
        snapshot = _parser(response)
    except SecEdgarResponseError as error:
        run = _failed_run(
            collection_id,
            cik,
            min(started_at, response.received_at),
            completed_at,
            error.failure_code,
            response,
        )
        _ = store.append_failed_run(run)
        return SecCollectionResult(run, receipt.created, 0, 0, False)
    run = SecSubmissionRun(
        collection_id=collection_id,
        cik=cik,
        started_at=min(started_at, response.received_at),
        completed_at=completed_at,
        status=SecCollectionStatus.SUCCESS,
        failure_code=None,
        receipt_id=response.receipt_id,
        filing_count=len(snapshot.filings),
        additional_history_file_count=snapshot.additional_history_file_count,
    )
    appended = store.append_collection(run, snapshot)
    return SecCollectionResult(
        appended.run,
        receipt.created,
        len(appended.filings),
        appended.new_filing_version_count,
        False,
    )


def _failed_run(
    collection_id: str,
    cik: str,
    started_at: dt.datetime,
    completed_at: dt.datetime,
    failure_code: str,
    response: SecSubmissionRawResponse | None,
) -> SecSubmissionRun:
    return SecSubmissionRun(
        collection_id=collection_id,
        cik=cik,
        started_at=min(started_at, completed_at),
        completed_at=max(started_at, completed_at),
        status=SecCollectionStatus.FAILED,
        failure_code=failure_code,
        receipt_id=None if response is None else response.receipt_id,
        filing_count=0,
        additional_history_file_count=0,
    )
