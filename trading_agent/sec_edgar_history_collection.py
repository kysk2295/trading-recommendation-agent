from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from trading_agent.sec_edgar_collection import SecCollectionResult
from trading_agent.sec_edgar_history_collection_file import (
    collect_sec_history_file,
    resume_sec_history_file,
    sec_history_file_context,
)
from trading_agent.sec_edgar_history_collection_types import (
    MAX_SEC_HISTORY_FILES_PER_COLLECTION,
    InvalidSecEdgarHistoryCollectionError,
    SecAdditionalHistoryCollectionResult,
    SecAdditionalHistoryFetcher,
)
from trading_agent.sec_edgar_models import (
    SecAdditionalHistoryFile,
    SecCollectionStatus,
    SecEdgarResponseError,
    SecSubmissionRun,
    SecSubmissionSourceKind,
    normalize_sec_cik,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store import SecEdgarStore


def collect_sec_additional_history(
    fetcher: SecAdditionalHistoryFetcher,
    store: SecEdgarStore,
    parent_collection_id: str,
    cik: str,
    *,
    max_files: int = 1,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> SecAdditionalHistoryCollectionResult:
    resumed = resume_sec_additional_history(
        store,
        parent_collection_id,
        cik,
        max_files=max_files,
    )
    if resumed is not None:
        return resumed
    parent, selected, discovered_count = _collection_context(
        store,
        parent_collection_id,
        cik,
        max_files,
    )
    files: list[SecCollectionResult] = []
    for history_file in selected:
        result = collect_sec_history_file(
            fetcher,
            sec_history_file_context(store, parent, history_file),
            _clock,
        )
        files.append(result)
        if result.run.status is SecCollectionStatus.FAILED:
            break
    return _collection_result(parent, discovered_count, selected, tuple(files))


def resume_sec_additional_history(
    store: SecEdgarStore,
    parent_collection_id: str,
    cik: str,
    *,
    max_files: int = 1,
) -> SecAdditionalHistoryCollectionResult | None:
    parent, selected, discovered_count = _collection_context(
        store,
        parent_collection_id,
        cik,
        max_files,
    )
    files: list[SecCollectionResult] = []
    for history_file in selected:
        result = resume_sec_history_file(sec_history_file_context(store, parent, history_file))
        if result is None:
            return None
        files.append(result)
        if result.run.status is SecCollectionStatus.FAILED:
            break
    return _collection_result(parent, discovered_count, selected, tuple(files))


def _collection_context(
    store: SecEdgarStore,
    parent_collection_id: str,
    cik: str,
    max_files: int,
) -> tuple[SecSubmissionRun, tuple[SecAdditionalHistoryFile, ...], int]:
    if not 1 <= max_files <= MAX_SEC_HISTORY_FILES_PER_COLLECTION:
        raise InvalidSecEdgarHistoryCollectionError
    cik = normalize_sec_cik(cik)
    parent = store.collection_run(parent_collection_id, cik)
    if (
        parent is None
        or parent.status is not SecCollectionStatus.SUCCESS
        or parent.source_kind is not SecSubmissionSourceKind.RECENT
        or parent.receipt_id is None
    ):
        raise InvalidSecEdgarHistoryCollectionError
    parent_receipt = store.receipt_for_collection(parent_collection_id, cik)
    if parent_receipt is None or parent_receipt.response.receipt_id != parent.receipt_id:
        raise InvalidSecEdgarHistoryCollectionError
    try:
        parent_snapshot = parse_sec_submission_snapshot(parent_receipt.response)
    except SecEdgarResponseError:
        raise InvalidSecEdgarHistoryCollectionError from None
    selected = parent_snapshot.additional_history_files[:max_files]
    return parent, selected, parent_snapshot.additional_history_file_count


def _collection_result(
    parent: SecSubmissionRun,
    discovered_count: int,
    selected: tuple[SecAdditionalHistoryFile, ...],
    files: tuple[SecCollectionResult, ...],
) -> SecAdditionalHistoryCollectionResult:
    return SecAdditionalHistoryCollectionResult(
        parent_run_id=parent.run_id,
        discovered_file_count=discovered_count,
        selected_file_count=len(selected),
        completed_file_count=sum(
            item.run.status is SecCollectionStatus.SUCCESS for item in files
        ),
        filing_count=sum(item.filing_count for item in files),
        new_filing_version_count=sum(item.new_filing_version_count for item in files),
        replayed_file_count=sum(item.replayed for item in files),
        files=files,
    )


__all__ = (
    "MAX_SEC_HISTORY_FILES_PER_COLLECTION",
    "InvalidSecEdgarHistoryCollectionError",
    "SecAdditionalHistoryCollectionResult",
    "SecAdditionalHistoryFetcher",
    "collect_sec_additional_history",
    "resume_sec_additional_history",
)
