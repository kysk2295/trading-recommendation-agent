from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from trading_agent.us_market_data_runtime_codec import receipt_from_row
from trading_agent.us_market_data_runtime_models import (
    MarketDataRuntimeError,
    MarketDataRuntimeReceipt,
)
from trading_agent.us_market_data_runtime_schema import (
    MARKET_DATA_RUNTIME_SCHEMA_VERSION,
)


def read_market_data_runtime_receipts(
    path: Path,
    source_id: str,
    instrument_id: str,
) -> tuple[MarketDataRuntimeReceipt, ...]:
    if not path.is_file():
        return ()
    with closing(
        sqlite3.connect(
            f"file:{path.resolve(strict=False)}?mode=ro",
            uri=True,
        )
    ) as connection:
        _ = connection.execute("PRAGMA query_only = ON")
        if connection.execute("PRAGMA user_version").fetchone() != (MARKET_DATA_RUNTIME_SCHEMA_VERSION,):
            raise MarketDataRuntimeError
        rows = connection.execute(
            """SELECT source_id, connection_epoch, sequence, receipt_id,
            received_at, payload_sha256, raw_payload, instrument_id, symbol,
            bar_start_at, bar_end_at, open, high, low, close, volume
            FROM market_data_runtime_receipts
            WHERE source_id = ? AND instrument_id = ?
            ORDER BY bar_end_at, receipt_id""",
            (source_id, instrument_id),
        ).fetchall()
    return tuple(receipt_from_row(row) for row in rows)


__all__ = ("read_market_data_runtime_receipts",)
