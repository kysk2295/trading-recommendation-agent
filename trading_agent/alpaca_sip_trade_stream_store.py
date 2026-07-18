from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from pathlib import Path
from typing import final

from trading_agent.alpaca_sip_trade_store import StoredAlpacaSipTradeFrame
from trading_agent.alpaca_sip_trade_stream_audit import (
    TerminalRow,
    load_control_sequences,
    load_data_links,
    load_validated_control_times,
    terminal_content_hash,
    terminal_record_from_row,
)
from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipBoundedTradeHistoryAttestation,
    AlpacaSipRawControlFrame,
    AlpacaSipStreamTerminalRecord,
    AlpacaSipStreamTerminalStatus,
    AlpacaSipTradeStreamProtocolError,
)
from trading_agent.alpaca_sip_trade_stream_sqlite import (
    AlpacaSipStreamWriter,
    require_alpaca_sip_stream_schema,
    require_private_alpaca_sip_stream_file,
)


@final
class AlpacaSipTradeStreamStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def append_control(self, frame: AlpacaSipRawControlFrame) -> int:
        try:
            payload_hash = hashlib.sha256(frame.payload).hexdigest()
            row = (
                frame.connection_epoch,
                frame.sequence,
                frame.received_at.astimezone(dt.UTC).isoformat(),
                payload_hash,
                frame.payload,
            )
            with AlpacaSipStreamWriter(self.path) as connection:
                existing = connection.execute(
                    "SELECT generation,connection_epoch,sequence,received_at,payload_sha256,payload "
                    "FROM control_frames WHERE connection_epoch=? AND sequence=?",
                    (frame.connection_epoch, frame.sequence),
                ).fetchone()
                if existing is not None:
                    if tuple(existing[1:]) != row:
                        raise AlpacaSipTradeStreamProtocolError
                    return existing[0]
                cursor = connection.execute(
                    "INSERT INTO control_frames "
                    "(connection_epoch,sequence,received_at,payload_sha256,payload) VALUES (?,?,?,?,?)",
                    row,
                )
                connection.commit()
                generation = cursor.lastrowid
                if type(generation) is not int:
                    raise AlpacaSipTradeStreamProtocolError
                return generation
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipTradeStreamProtocolError from None

    def append_data_link(
        self,
        connection_epoch: str,
        sequence: int,
        frame: StoredAlpacaSipTradeFrame,
    ) -> None:
        try:
            row = (
                connection_epoch,
                sequence,
                frame.receipt_id,
                frame.generation,
                frame.received_at.isoformat(),
            )
            with AlpacaSipStreamWriter(self.path) as connection:
                existing = connection.execute(
                    "SELECT connection_epoch,sequence,receipt_id,generation,received_at FROM data_links "
                    "WHERE connection_epoch=? AND sequence=?",
                    (connection_epoch, sequence),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise AlpacaSipTradeStreamProtocolError
                    return
                _ = connection.execute("INSERT INTO data_links VALUES (?,?,?,?,?)", row)
                connection.commit()
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipTradeStreamProtocolError from None

    def append_terminal(self, record: AlpacaSipStreamTerminalRecord) -> None:
        try:
            with AlpacaSipStreamWriter(self.path) as connection:
                links = load_data_links(connection, record.connection_epoch)
                controls = load_control_sequences(connection, record.connection_epoch)
                if record.status is AlpacaSipStreamTerminalStatus.BOUNDED_COMPLETE and (
                    not links or controls != (1, 2, 3)
                ):
                    raise AlpacaSipTradeStreamProtocolError
                content_hash = terminal_content_hash(record, len(links))
                row = (
                    record.connection_epoch,
                    record.config.symbol,
                    record.config.market_date.isoformat(),
                    record.authorized_at.isoformat(),
                    record.subscribed_at.isoformat(),
                    record.terminal_at.isoformat(),
                    record.status.value,
                    len(links),
                    content_hash,
                )
                existing = connection.execute(
                    "SELECT connection_epoch,symbol,market_date,authorized_at,subscribed_at,terminal_at,"
                    "status,data_count,content_sha256 FROM terminal_sessions WHERE connection_epoch=?",
                    (record.connection_epoch,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise AlpacaSipTradeStreamProtocolError
                    return
                _ = connection.execute("INSERT INTO terminal_sessions VALUES (?,?,?,?,?,?,?,?,?)", row)
                connection.commit()
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipTradeStreamProtocolError from None

    def load_attestation(self, connection_epoch: str) -> AlpacaSipBoundedTradeHistoryAttestation | None:
        try:
            require_private_alpaca_sip_stream_file(self.path)
            if not self.path.exists():
                return None
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                require_alpaca_sip_stream_schema(connection)
                row: TerminalRow | None = connection.execute(
                    "SELECT connection_epoch,symbol,market_date,authorized_at,subscribed_at,terminal_at,"
                    "status,data_count,content_sha256 FROM terminal_sessions WHERE connection_epoch=?",
                    (connection_epoch,),
                ).fetchone()
                if row is None or row[6] != AlpacaSipStreamTerminalStatus.BOUNDED_COMPLETE.value:
                    return None
                links = load_data_links(connection, connection_epoch)
                control_times = load_validated_control_times(connection, connection_epoch, row[1])
            record = terminal_record_from_row(row)
            if (
                control_times[1] != record.authorized_at
                or control_times[2] != record.subscribed_at
                or row[7] != len(links)
                or row[8] != terminal_content_hash(record, len(links))
            ):
                raise AlpacaSipTradeStreamProtocolError
            return AlpacaSipBoundedTradeHistoryAttestation(
                record.connection_epoch,
                record.config,
                record.authorized_at,
                record.subscribed_at,
                record.terminal_at,
                tuple(item[2] for item in links),
            )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipTradeStreamProtocolError from None

    def load_terminal_status(self, connection_epoch: str) -> AlpacaSipStreamTerminalStatus | None:
        try:
            require_private_alpaca_sip_stream_file(self.path)
            if not self.path.exists():
                return None
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                require_alpaca_sip_stream_schema(connection)
                row: tuple[str] | None = connection.execute(
                    "SELECT status FROM terminal_sessions WHERE connection_epoch=?",
                    (connection_epoch,),
                ).fetchone()
            return None if row is None else AlpacaSipStreamTerminalStatus(row[0])
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipTradeStreamProtocolError from None

    def control_count(self) -> int:
        return self._count("control_frames")

    def control_count_for_epoch(self, connection_epoch: str) -> int:
        return self._epoch_count("control_frames", connection_epoch)

    def data_link_count(self, connection_epoch: str) -> int:
        return self._epoch_count("data_links", connection_epoch)

    def _epoch_count(self, table: str, connection_epoch: str) -> int:
        try:
            require_private_alpaca_sip_stream_file(self.path)
            if not self.path.exists():
                return 0
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                require_alpaca_sip_stream_schema(connection)
                row: tuple[int] = connection.execute(
                    f"SELECT count(*) FROM {table} WHERE connection_epoch=?",
                    (connection_epoch,),
                ).fetchone()
            return row[0]
        except sqlite3.Error:
            raise AlpacaSipTradeStreamProtocolError from None

    def _count(self, table: str) -> int:
        try:
            require_private_alpaca_sip_stream_file(self.path)
            if not self.path.exists():
                return 0
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                require_alpaca_sip_stream_schema(connection)
                row: tuple[int] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
            return row[0]
        except sqlite3.Error:
            raise AlpacaSipTradeStreamProtocolError from None


__all__ = ("AlpacaSipTradeStreamStore",)
