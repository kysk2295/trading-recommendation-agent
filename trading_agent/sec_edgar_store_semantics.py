from __future__ import annotations

import sqlite3

from trading_agent.sec_edgar_store_support import (
    filings_from_connection,
    receipt_from_connection,
    run_from_connection,
)
from trading_agent.sec_edgar_store_types import InvalidSecEdgarStoreError


def require_store_semantics(connection: sqlite3.Connection) -> None:
    receipt_keys = connection.execute(
        "SELECT collection_id,cik FROM sec_submission_receipts"
    ).fetchall()
    for collection_id, cik in receipt_keys:
        if receipt_from_connection(connection, collection_id, cik) is None:
            raise InvalidSecEdgarStoreError
    run_ids = connection.execute("SELECT run_id FROM sec_submission_runs").fetchall()
    for (run_id,) in run_ids:
        if run_from_connection(connection, run_id) is None:
            raise InvalidSecEdgarStoreError
        _ = filings_from_connection(connection, run_id)
