from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3

from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipControlStage,
    AlpacaSipStreamTerminalRecord,
    AlpacaSipStreamTerminalStatus,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamProtocolError,
    parse_alpaca_sip_control_frame,
)

type DataLinkRow = tuple[str, int, str, int, str]
type TerminalRow = tuple[str, str, str, str, str, str, str, int, str]


def load_data_links(connection: sqlite3.Connection, epoch: str) -> tuple[DataLinkRow, ...]:
    rows: list[DataLinkRow] = connection.execute(
        "SELECT connection_epoch,sequence,receipt_id,generation,received_at FROM data_links "
        "WHERE connection_epoch=? ORDER BY sequence",
        (epoch,),
    ).fetchall()
    if tuple(row[1] for row in rows) != tuple(range(1, len(rows) + 1)):
        raise AlpacaSipTradeStreamProtocolError
    return tuple(rows)


def load_control_sequences(connection: sqlite3.Connection, epoch: str) -> tuple[int, ...]:
    rows: list[tuple[int]] = connection.execute(
        "SELECT sequence FROM control_frames WHERE connection_epoch=? ORDER BY sequence",
        (epoch,),
    ).fetchall()
    return tuple(row[0] for row in rows)


def load_validated_control_times(
    connection: sqlite3.Connection,
    epoch: str,
    symbol: str,
) -> tuple[dt.datetime, dt.datetime, dt.datetime]:
    rows: list[tuple[int, str, str, bytes]] = connection.execute(
        "SELECT sequence,received_at,payload_sha256,payload FROM control_frames "
        "WHERE connection_epoch=? ORDER BY sequence",
        (epoch,),
    ).fetchall()
    stages = (
        AlpacaSipControlStage.CONNECTED,
        AlpacaSipControlStage.AUTHENTICATED,
        AlpacaSipControlStage.SUBSCRIBED,
    )
    if tuple(row[0] for row in rows) != (1, 2, 3):
        raise AlpacaSipTradeStreamProtocolError
    times: list[dt.datetime] = []
    for row, stage in zip(rows, stages, strict=True):
        if row[2] != hashlib.sha256(row[3]).hexdigest():
            raise AlpacaSipTradeStreamProtocolError
        parse_alpaca_sip_control_frame(row[3], stage, symbol)
        times.append(dt.datetime.fromisoformat(row[1]))
    if len(times) != 3 or not times[0] <= times[1] <= times[2]:
        raise AlpacaSipTradeStreamProtocolError
    return times[0], times[1], times[2]


def terminal_record_from_row(row: TerminalRow) -> AlpacaSipStreamTerminalRecord:
    return AlpacaSipStreamTerminalRecord(
        row[0],
        AlpacaSipTradeStreamConfig(dt.date.fromisoformat(row[2]), row[1]),
        dt.datetime.fromisoformat(row[3]),
        dt.datetime.fromisoformat(row[4]),
        dt.datetime.fromisoformat(row[5]),
        AlpacaSipStreamTerminalStatus(row[6]),
    )


def terminal_content_hash(record: AlpacaSipStreamTerminalRecord, data_count: int) -> str:
    content = {
        "authorized_at": record.authorized_at.isoformat(),
        "connection_epoch": record.connection_epoch,
        "data_count": data_count,
        "market_date": record.config.market_date.isoformat(),
        "status": record.status.value,
        "subscribed_at": record.subscribed_at.isoformat(),
        "symbol": record.config.symbol,
        "terminal_at": record.terminal_at.isoformat(),
    }
    encoded = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = (
    "DataLinkRow",
    "TerminalRow",
    "load_control_sequences",
    "load_data_links",
    "load_validated_control_times",
    "terminal_content_hash",
    "terminal_record_from_row",
)
