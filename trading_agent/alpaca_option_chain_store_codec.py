from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
import stat
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.alpaca_option_chain_models import (
    AlpacaOptionChainError,
    OptionChainRawResponse,
    OptionChainRun,
)
from trading_agent.private_directory_identity import (
    open_private_parent,
    require_private_directory_query_only,
)

type OptionChainResponseRow = tuple[
    str,
    int,
    str | None,
    str,
    int,
    str,
    str,
    bytes,
]
type OptionChainRunRow = tuple[str, str, str, bytes]


class AlpacaOptionChainStoreError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca option chain store is invalid"


def option_chain_database_exists(path: Path) -> bool:
    try:
        parent = open_private_parent(path.parent, create=False)
        try:
            require_private_directory_query_only(parent)
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            try:
                metadata = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
    except FileNotFoundError:
        return False
    except (OSError, TypeError, ValueError):
        raise AlpacaOptionChainStoreError from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AlpacaOptionChainStoreError
    return True


def option_chain_run_row(
    connection: sqlite3.Connection,
    request_id: str,
) -> OptionChainRunRow | None:
    return connection.execute(
        "SELECT request_id,run_id,payload_sha256,run_payload "
        "FROM alpaca_option_chain_runs WHERE request_id=?",
        (request_id,),
    ).fetchone()


def option_chain_response_from_row(
    row: OptionChainResponseRow,
) -> OptionChainRawResponse:
    raw_payload = bytes(row[7])
    if hashlib.sha256(raw_payload).hexdigest() != row[6]:
        raise AlpacaOptionChainStoreError
    return OptionChainRawResponse(
        request_id=str(row[0]),
        page_index=int(row[1]),
        page_token=None if row[2] is None else str(row[2]),
        received_at=dt.datetime.fromisoformat(str(row[3])),
        status_code=int(row[4]),
        content_type=str(row[5]),
        raw_payload=raw_payload,
    )


def option_chain_run_from_row(
    row: OptionChainRunRow,
    request_id: str,
) -> OptionChainRun:
    try:
        payload = bytes(row[3])
        if hashlib.sha256(payload).hexdigest() != row[2]:
            raise AlpacaOptionChainStoreError
        run = OptionChainRun.model_validate_json(payload)
        if run.request.request_id != request_id or run.run_id != row[1]:
            raise AlpacaOptionChainStoreError
        return run
    except (
        AlpacaOptionChainError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise AlpacaOptionChainStoreError from None


__all__ = (
    "AlpacaOptionChainStoreError",
    "OptionChainResponseRow",
    "OptionChainRunRow",
    "option_chain_database_exists",
    "option_chain_response_from_row",
    "option_chain_run_from_row",
    "option_chain_run_row",
)
