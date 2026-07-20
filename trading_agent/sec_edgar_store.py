from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import final

from pydantic import ValidationError

from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.sec_edgar_models import (
    SecCollectionStatus,
    SecSubmissionRawResponse,
    SecSubmissionRun,
    SecSubmissionSnapshot,
)
from trading_agent.sec_edgar_store_sql import sec_reader as _reader
from trading_agent.sec_edgar_store_sql import sec_writer as _writer
from trading_agent.sec_edgar_store_support import (
    append_filings as _append_filings,
)
from trading_agent.sec_edgar_store_support import (
    filings_from_connection as _filings_from_connection,
)
from trading_agent.sec_edgar_store_support import (
    insert_run as _insert_run,
)
from trading_agent.sec_edgar_store_support import (
    receipt_from_connection as _receipt_from_connection,
)
from trading_agent.sec_edgar_store_support import (
    receipt_row as _receipt_row,
)
from trading_agent.sec_edgar_store_support import (
    require_receipt as _require_receipt,
)
from trading_agent.sec_edgar_store_support import (
    run_from_connection as _run_from_connection,
)
from trading_agent.sec_edgar_store_support import (
    validated_response as _validated_response,
)
from trading_agent.sec_edgar_store_support import (
    validated_run as _validated_run,
)
from trading_agent.sec_edgar_store_types import (
    InvalidSecEdgarStoreError,
    SecCollectionAppendResult,
    SecReceiptAppendResult,
    SecStoredFilingVersion,
    SecStoredReceipt,
)


@final
class SecEdgarStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def preflight_write(self) -> None:
        try:
            with _writer(self.path) as connection:
                connection.rollback()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def append_receipt(self, response: SecSubmissionRawResponse) -> SecReceiptAppendResult:
        try:
            response = _validated_response(response)
            row = _receipt_row(response)
            with _writer(self.path) as connection:
                existing = connection.execute(
                    "SELECT receipt_id,collection_id,cik,received_at,status_code,content_type,"
                    "content_encoding,payload_sha256,raw_payload FROM sec_submission_receipts "
                    "WHERE collection_id=? AND cik=?",
                    (response.collection_id, response.cik),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise InvalidSecEdgarStoreError
                    return SecReceiptAppendResult(SecStoredReceipt(response), False)
                _ = connection.execute(
                    "INSERT INTO sec_submission_receipts VALUES (?,?,?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
            return SecReceiptAppendResult(SecStoredReceipt(response), True)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def append_collection(
        self,
        run: SecSubmissionRun,
        snapshot: SecSubmissionSnapshot,
    ) -> SecCollectionAppendResult:
        try:
            run = _validated_run(run)
            snapshot = SecSubmissionSnapshot.model_validate(snapshot.model_dump(mode="json"))
            if (
                run.status is not SecCollectionStatus.SUCCESS
                or run.cik != snapshot.cik
                or run.filing_count != len(snapshot.filings)
                or run.additional_history_file_count != snapshot.additional_history_file_count
                or run.receipt_id is None
            ):
                raise InvalidSecEdgarStoreError
            with _writer(self.path) as connection:
                _require_receipt(connection, run)
                existing = _run_from_connection(connection, run.run_id)
                if existing is not None:
                    filings = _filings_from_connection(connection, run.run_id)
                    if existing != run or tuple(item.event for item in filings) != snapshot.filings:
                        raise InvalidSecEdgarStoreError
                    return SecCollectionAppendResult(existing, filings, False, 0)
                filings, new_count = _append_filings(connection, run, snapshot)
                _insert_run(connection, run)
                for item in filings:
                    _ = connection.execute(
                        "INSERT INTO sec_filing_observations VALUES (?,?,?,?,?)",
                        (
                            run.run_id,
                            item.receipt_id,
                            item.version_id,
                            item.item_index,
                            item.observed_at.isoformat(),
                        ),
                    )
                connection.commit()
            return SecCollectionAppendResult(run, filings, True, new_count)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def append_failed_run(self, run: SecSubmissionRun) -> bool:
        try:
            run = _validated_run(run)
            if run.status is not SecCollectionStatus.FAILED:
                raise InvalidSecEdgarStoreError
            with _writer(self.path) as connection:
                if run.receipt_id is not None:
                    _require_receipt(connection, run)
                existing = _run_from_connection(connection, run.run_id)
                if existing is not None:
                    if existing != run or _filings_from_connection(connection, run.run_id):
                        raise InvalidSecEdgarStoreError
                    return False
                _insert_run(connection, run)
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def collection_run(self, collection_id: str, cik: str) -> SecSubmissionRun | None:
        if not self.path.exists():
            return None
        try:
            run_id = hashlib.sha256(f"{collection_id}|{cik}".encode()).hexdigest()
            with _reader(self.path) as connection:
                return _run_from_connection(connection, run_id)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def receipt_for_collection(self, collection_id: str, cik: str) -> SecStoredReceipt | None:
        if not self.path.exists():
            return None
        try:
            with _reader(self.path) as connection:
                return _receipt_from_connection(connection, collection_id, cik)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidSecEdgarStoreError from None

    def filings_for_run(self, run_id: str) -> tuple[SecStoredFilingVersion, ...]:
        if not self.path.exists():
            return ()
        try:
            with _reader(self.path) as connection:
                return _filings_from_connection(connection, run_id)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidSecEdgarStoreError from None
