from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import final

from trading_agent.alpaca_option_contract_models import (
    OptionContractCatalogRequest,
    OptionContractCatalogRun,
    OptionContractRawResponse,
)
from trading_agent.alpaca_option_contract_schema import (
    ALPACA_OPTION_CONTRACT_SCHEMA_VERSION,
    CREATE_ALPACA_OPTION_CONTRACT_SCHEMA,
)
from trading_agent.alpaca_option_contract_store_codec import (
    AlpacaOptionContractStoreError,
    option_contract_database_exists,
    option_contract_response_from_row,
    option_contract_run_from_row,
    option_contract_run_row,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory_query_only,
)


@final
class AlpacaOptionContractStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def preflight_write(self) -> None:
        with closing(self._connection(write=True)):
            pass

    def append_receipt(
        self,
        request: OptionContractCatalogRequest,
        response: OptionContractRawResponse,
    ) -> bool:
        if response.request_id != request.request_id:
            raise AlpacaOptionContractStoreError
        request_payload = canonical_experiment_ledger_json(request).encode()
        row = (
            response.receipt_id,
            request.request_id,
            request_payload,
            response.page_index,
            response.page_token,
            response.received_at.isoformat(),
            response.status_code,
            response.content_type,
            hashlib.sha256(response.raw_payload).hexdigest(),
            response.raw_payload,
        )
        try:
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if option_contract_run_row(
                    connection,
                    request.request_id,
                ) is not None:
                    raise AlpacaOptionContractStoreError
                existing = connection.execute(
                    "SELECT receipt_id,request_id,request_payload,page_index,"
                    "page_token,received_at,status_code,content_type,payload_sha256,"
                    "raw_payload FROM alpaca_option_contract_receipts "
                    "WHERE request_id=? AND page_index=?",
                    (request.request_id, response.page_index),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise AlpacaOptionContractStoreError
                    connection.rollback()
                    return False
                connection.execute(
                    "INSERT INTO alpaca_option_contract_receipts "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaOptionContractStoreError from None

    def append_run(self, run: OptionContractCatalogRun) -> bool:
        payload = canonical_experiment_ledger_json(run).encode()
        row = (
            run.request.request_id,
            run.run_id,
            hashlib.sha256(payload).hexdigest(),
            payload,
        )
        try:
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                receipt_ids = tuple(
                    item[0]
                    for item in connection.execute(
                        "SELECT receipt_id FROM alpaca_option_contract_receipts "
                        "WHERE request_id=? ORDER BY page_index",
                        (run.request.request_id,),
                    ).fetchall()
                )
                if receipt_ids != run.receipt_ids:
                    raise AlpacaOptionContractStoreError
                existing = option_contract_run_row(
                    connection,
                    run.request.request_id,
                )
                if existing is not None:
                    if tuple(existing) != row:
                        raise AlpacaOptionContractStoreError
                    connection.rollback()
                    return False
                connection.execute(
                    "INSERT INTO alpaca_option_contract_runs VALUES (?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaOptionContractStoreError from None

    def receipts(
        self,
        request_id: str,
    ) -> tuple[OptionContractRawResponse, ...]:
        try:
            with closing(self._connection(write=False)) as connection:
                rows = connection.execute(
                    "SELECT request_id,page_index,page_token,received_at,"
                    "status_code,content_type,payload_sha256,raw_payload "
                    "FROM alpaca_option_contract_receipts "
                    "WHERE request_id=? ORDER BY page_index",
                    (request_id,),
                ).fetchall()
            return tuple(option_contract_response_from_row(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaOptionContractStoreError from None

    def run(self, request_id: str) -> OptionContractCatalogRun | None:
        if not option_contract_database_exists(self.path):
            return None
        try:
            with closing(self._connection(write=False)) as connection:
                row = option_contract_run_row(connection, request_id)
            if row is None:
                return None
            return option_contract_run_from_row(row, request_id)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaOptionContractStoreError from None

    def counts(self) -> tuple[int, int]:
        try:
            with closing(self._connection(write=False)) as connection:
                receipt = connection.execute(
                    "SELECT COUNT(*) FROM alpaca_option_contract_receipts"
                ).fetchone()
                run = connection.execute(
                    "SELECT COUNT(*) FROM alpaca_option_contract_runs"
                ).fetchone()
            if receipt is None or run is None:
                raise AlpacaOptionContractStoreError
            return int(receipt[0]), int(run[0])
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaOptionContractStoreError from None

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        try:
            if self.path.is_symlink():
                raise AlpacaOptionContractStoreError
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self.path.parent, 0o700)
                connection = sqlite3.connect(self.path)
                if self.path.stat().st_mode & 0o077:
                    os.chmod(self.path, 0o600)
                if connection.execute("PRAGMA user_version").fetchone() == (0,):
                    connection.executescript(
                        CREATE_ALPACA_OPTION_CONTRACT_SCHEMA
                    )
                    connection.execute(
                        "PRAGMA user_version="
                        f"{ALPACA_OPTION_CONTRACT_SCHEMA_VERSION}"
                    )
                    connection.commit()
            else:
                parent = open_private_parent(self.path.parent, create=False)
                try:
                    require_private_directory_query_only(parent)
                    connection = sqlite3.connect(
                        f"file:{self.path}?mode=ro",
                        uri=True,
                    )
                    connection.execute("PRAGMA query_only=ON")
                finally:
                    os.close(parent)
            if connection.execute("PRAGMA user_version").fetchone() != (
                ALPACA_OPTION_CONTRACT_SCHEMA_VERSION,
            ):
                connection.close()
                raise AlpacaOptionContractStoreError
            return connection
        except (OSError, sqlite3.Error):
            raise AlpacaOptionContractStoreError from None


__all__ = ("AlpacaOptionContractStore", "AlpacaOptionContractStoreError")
