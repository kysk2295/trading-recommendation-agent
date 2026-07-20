from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import assert_never

from trading_agent.sec_edgar_models import (
    SecCollectionStatus,
    SecEdgarResponseError,
    SecSubmissionRun,
    SecSubmissionSourceKind,
    sec_additional_history_collection_id,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store_types import InvalidSecEdgarStoreError, SecStoredReceipt


@dataclass(frozen=True, slots=True)
class SecHistoryBindingReaders:
    run_by_id: Callable[[sqlite3.Connection, str], SecSubmissionRun | None]
    receipt_by_id: Callable[[sqlite3.Connection, str], SecStoredReceipt | None]


def require_history_parent_binding(
    connection: sqlite3.Connection,
    run: SecSubmissionRun,
    readers: SecHistoryBindingReaders,
) -> None:
    match run.source_kind:
        case SecSubmissionSourceKind.RECENT:
            return
        case SecSubmissionSourceKind.ADDITIONAL_HISTORY:
            _require_additional_history_parent(connection, run, readers)
        case unreachable:
            assert_never(unreachable)


def _require_additional_history_parent(
    connection: sqlite3.Connection,
    run: SecSubmissionRun,
    readers: SecHistoryBindingReaders,
) -> None:
    if (
        run.parent_receipt_id is None
        or run.history_file is None
        or run.parent_receipt_id == run.receipt_id
    ):
        raise InvalidSecEdgarStoreError
    parent_row = connection.execute(
        "SELECT run_id,payload_json FROM sec_submission_runs WHERE receipt_id=?",
        (run.parent_receipt_id,),
    ).fetchone()
    if parent_row is None:
        raise InvalidSecEdgarStoreError
    parent_candidate = SecSubmissionRun.model_validate_json(parent_row[1])
    match parent_candidate.source_kind:
        case SecSubmissionSourceKind.RECENT:
            pass
        case SecSubmissionSourceKind.ADDITIONAL_HISTORY:
            raise InvalidSecEdgarStoreError
        case unreachable:
            assert_never(unreachable)
    parent = readers.run_by_id(connection, parent_row[0])
    parent_receipt = readers.receipt_by_id(connection, run.parent_receipt_id)
    child_receipt = (
        None if run.receipt_id is None else readers.receipt_by_id(connection, run.receipt_id)
    )
    if parent is None:
        raise InvalidSecEdgarStoreError
    match parent.status:
        case SecCollectionStatus.SUCCESS:
            pass
        case SecCollectionStatus.FAILED:
            raise InvalidSecEdgarStoreError
        case unreachable:
            assert_never(unreachable)
    if (
        parent.receipt_id != run.parent_receipt_id
        or parent_receipt is None
        or run.completed_at < parent.completed_at
        or (
            child_receipt is not None
            and child_receipt.response.received_at < parent_receipt.response.received_at
        )
        or run.collection_id
        != sec_additional_history_collection_id(run.parent_receipt_id, run.history_file)
    ):
        raise InvalidSecEdgarStoreError
    try:
        parent_snapshot = parse_sec_submission_snapshot(parent_receipt.response)
    except SecEdgarResponseError:
        raise InvalidSecEdgarStoreError from None
    if run.history_file not in parent_snapshot.additional_history_files:
        raise InvalidSecEdgarStoreError


__all__ = (
    "SecHistoryBindingReaders",
    "require_history_parent_binding",
)
