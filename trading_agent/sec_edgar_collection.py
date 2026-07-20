from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, override

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


class InvalidSecEdgarCollectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR collection state is invalid"


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
        raise InvalidSecEdgarCollectionError
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
    orphan = store.receipt_for_collection(collection_id, cik)
    if orphan is not None:
        return _finish_response(
            store,
            orphan.response,
            receipt_created=False,
            started_at=orphan.response.received_at,
            clock=_clock,
            parser=_parser,
        )
    store.preflight_write()
    started_at = _clock()
    try:
        response = fetcher.fetch_submissions(collection_id, cik)
    except SecEdgarTransportError:
        run = _failed_run(collection_id, cik, started_at, _clock(), "transport", None)
        _ = store.append_failed_run(run)
        return SecCollectionResult(run, False, 0, 0, False)
    if response.collection_id != collection_id or response.cik != cik:
        raise InvalidSecEdgarCollectionError
    receipt = store.append_receipt(response)
    return _finish_response(
        store,
        response,
        receipt_created=receipt.created,
        started_at=started_at,
        clock=_clock,
        parser=_parser,
    )


def _finish_response(
    store: SecEdgarStore,
    response: SecSubmissionRawResponse,
    *,
    receipt_created: bool,
    started_at: dt.datetime,
    clock: Callable[[], dt.datetime],
    parser: Callable[[SecSubmissionRawResponse], SecSubmissionSnapshot],
) -> SecCollectionResult:
    completed_at = max(started_at, response.received_at, clock())
    try:
        snapshot = parser(response)
    except SecEdgarResponseError as error:
        run = _failed_run(
            response.collection_id,
            response.cik,
            min(started_at, response.received_at),
            completed_at,
            error.failure_code,
            response,
        )
        _ = store.append_failed_run(run)
        return SecCollectionResult(run, receipt_created, 0, 0, False)
    run = SecSubmissionRun(
        collection_id=response.collection_id,
        cik=response.cik,
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
        receipt_created,
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
