from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from trading_agent.sec_edgar_client import SecEdgarTransportError
from trading_agent.sec_edgar_collection import SecCollectionResult
from trading_agent.sec_edgar_history_collection_types import (
    InvalidSecEdgarHistoryCollectionError,
    SecAdditionalHistoryFetcher,
)
from trading_agent.sec_edgar_models import (
    SecAdditionalHistoryFile,
    SecCollectionStatus,
    SecEdgarResponseError,
    SecSubmissionRawResponse,
    SecSubmissionRun,
    SecSubmissionSourceKind,
    sec_additional_history_collection_id,
)
from trading_agent.sec_edgar_parser import parse_sec_additional_history_snapshot
from trading_agent.sec_edgar_store import SecEdgarStore


@dataclass(frozen=True, slots=True)
class SecHistoryFileContext:
    store: SecEdgarStore
    parent: SecSubmissionRun
    history_file: SecAdditionalHistoryFile
    collection_id: str


@dataclass(frozen=True, slots=True)
class _ObservedResponse:
    response: SecSubmissionRawResponse
    receipt_created: bool
    started_at: dt.datetime


@dataclass(frozen=True, slots=True)
class _RunOutcome:
    started_at: dt.datetime
    completed_at: dt.datetime
    failure_code: str | None
    response: SecSubmissionRawResponse | None
    filing_count: int


def sec_history_file_context(
    store: SecEdgarStore,
    parent: SecSubmissionRun,
    history_file: SecAdditionalHistoryFile,
) -> SecHistoryFileContext:
    if parent.receipt_id is None:
        raise InvalidSecEdgarHistoryCollectionError
    return SecHistoryFileContext(
        store,
        parent,
        history_file,
        sec_additional_history_collection_id(parent.receipt_id, history_file),
    )


def collect_sec_history_file(
    fetcher: SecAdditionalHistoryFetcher,
    context: SecHistoryFileContext,
    clock: Callable[[], dt.datetime],
) -> SecCollectionResult:
    resumed = resume_sec_history_file(context)
    if resumed is not None:
        return resumed
    context.store.preflight_write()
    started_at = clock()
    try:
        response = fetcher.fetch_additional_history(
            context.collection_id,
            context.parent.cik,
            context.history_file.name,
        )
    except SecEdgarTransportError:
        run = _history_run(
            context,
            _RunOutcome(started_at, clock(), "transport", None, 0),
        )
        _ = context.store.append_failed_run(run)
        return SecCollectionResult(run, False, 0, 0, False)
    if response.collection_id != context.collection_id or response.cik != context.parent.cik:
        raise InvalidSecEdgarHistoryCollectionError
    receipt = context.store.append_receipt(response)
    return _finish_file(
        context,
        _ObservedResponse(response, receipt.created, started_at),
        clock,
    )


def resume_sec_history_file(context: SecHistoryFileContext) -> SecCollectionResult | None:
    existing = context.store.collection_run(context.collection_id, context.parent.cik)
    if existing is not None:
        filings = context.store.filings_for_run(existing.run_id)
        if len(filings) != existing.filing_count:
            raise InvalidSecEdgarHistoryCollectionError
        return SecCollectionResult(existing, False, existing.filing_count, 0, True)
    orphan = context.store.receipt_for_collection(context.collection_id, context.parent.cik)
    if orphan is None:
        return None
    observed_at = orphan.response.received_at
    recovered_at = max(observed_at, context.parent.completed_at)
    return _finish_file(
        context,
        _ObservedResponse(orphan.response, False, observed_at),
        lambda: recovered_at,
    )


def _finish_file(
    context: SecHistoryFileContext,
    observed: _ObservedResponse,
    clock: Callable[[], dt.datetime],
) -> SecCollectionResult:
    completed_at = max(observed.started_at, observed.response.received_at, clock())
    try:
        snapshot = parse_sec_additional_history_snapshot(
            observed.response,
            context.history_file,
        )
    except SecEdgarResponseError as error:
        run = _history_run(
            context,
            _RunOutcome(
                min(observed.started_at, observed.response.received_at),
                completed_at,
                error.failure_code,
                observed.response,
                0,
            ),
        )
        _ = context.store.append_failed_run(run)
        return SecCollectionResult(run, observed.receipt_created, 0, 0, False)
    run = _history_run(
        context,
        _RunOutcome(
            min(observed.started_at, observed.response.received_at),
            completed_at,
            None,
            observed.response,
            len(snapshot.filings),
        ),
    )
    appended = context.store.append_collection(run, snapshot)
    return SecCollectionResult(
        appended.run,
        observed.receipt_created,
        len(appended.filings),
        appended.new_filing_version_count,
        False,
    )


def _history_run(
    context: SecHistoryFileContext,
    outcome: _RunOutcome,
) -> SecSubmissionRun:
    if context.parent.receipt_id is None:
        raise InvalidSecEdgarHistoryCollectionError
    return SecSubmissionRun(
        collection_id=context.collection_id,
        cik=context.parent.cik,
        started_at=min(outcome.started_at, outcome.completed_at),
        completed_at=max(outcome.started_at, outcome.completed_at),
        status=(
            SecCollectionStatus.SUCCESS
            if outcome.failure_code is None
            else SecCollectionStatus.FAILED
        ),
        failure_code=outcome.failure_code,
        receipt_id=None if outcome.response is None else outcome.response.receipt_id,
        filing_count=outcome.filing_count,
        additional_history_file_count=0,
        source_kind=SecSubmissionSourceKind.ADDITIONAL_HISTORY,
        parent_receipt_id=context.parent.receipt_id,
        history_file=context.history_file,
    )


__all__ = (
    "SecHistoryFileContext",
    "collect_sec_history_file",
    "resume_sec_history_file",
    "sec_history_file_context",
)
