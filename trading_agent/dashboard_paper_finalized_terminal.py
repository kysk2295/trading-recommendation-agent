from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.execution_store import ExecutionStore
from trading_agent.lane_contract_keys import lane_daily_snapshot_key
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_identity_models import LaneId
from trading_agent.us_equity_calendar import NEW_YORK

TERMINAL_FILENAME = "paper-finalized-terminal.v1.jsonl"


class InvalidFinalizedPaperTerminalError(ValueError):
    pass


class FinalizedPaperTerminalReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    lane_id: LaneId
    session_date: dt.date
    manifest_key: str
    snapshot_key: str
    source_ledger_generation: int
    source_ledger_sha256: str
    strategy_versions: tuple[str, ...]
    recovery_snapshot_sha256: str
    observed_at: dt.datetime
    terminal_state: Literal["finalized"] = "finalized"

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        hashes = (
            self.manifest_key,
            self.snapshot_key,
            self.source_ledger_sha256,
            self.recovery_snapshot_sha256,
        )
        if (
            any(len(value) != 64 or any(character not in "0123456789abcdef" for character in value) for value in hashes)
            or self.source_ledger_generation < 0
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
            or self.observed_at.astimezone(NEW_YORK).date() != self.session_date
            or self.strategy_versions != tuple(sorted(set(self.strategy_versions)))
        ):
            raise InvalidFinalizedPaperTerminalError
        return self


@dataclass(frozen=True, slots=True)
class FinalizedPaperAuthority:
    receipt: FinalizedPaperTerminalReceipt
    safe_ref: str


@dataclass(frozen=True, slots=True)
class FinalizedPaperAuthorityFailure:
    state: Literal["unavailable", "blocked", "corrupt"]
    blocker_code: str


def read_finalized_paper_authority(
    outputs: Path,
    snapshot: LaneDailySnapshot,
    now: dt.datetime,
) -> FinalizedPaperAuthority | FinalizedPaperAuthorityFailure:
    execution_path = outputs / "paper" / "execution.sqlite3"
    terminal_path = outputs / "paper" / TERMINAL_FILENAME
    if not execution_path.is_file():
        return FinalizedPaperAuthorityFailure("unavailable", "paper_finalized_execution_missing")
    if not terminal_path.exists():
        return FinalizedPaperAuthorityFailure("blocked", "paper_finalized_terminal_missing")
    try:
        metadata = terminal_path.lstat()
        if (
            terminal_path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise InvalidFinalizedPaperTerminalError
        payload = terminal_path.read_bytes()
        if not payload or len(payload) > 128 * 1024:
            raise InvalidFinalizedPaperTerminalError
        receipts = tuple(FinalizedPaperTerminalReceipt.model_validate_json(line) for line in payload.splitlines())
        snapshot_key = str(lane_daily_snapshot_key(snapshot))
        matches = tuple(receipt for receipt in receipts if receipt.snapshot_key == snapshot_key)
        if len(matches) != 1:
            raise InvalidFinalizedPaperTerminalError
        receipt = matches[0]
        if (
            receipt.lane_id is not snapshot.lane_id
            or receipt.session_date != snapshot.session_date
            or receipt.manifest_key != snapshot.manifest_key
            or receipt.source_ledger_generation != snapshot.source_ledger_generation
            or receipt.source_ledger_sha256 != snapshot.source_ledger_sha256
            or receipt.strategy_versions != snapshot.champion_strategy_versions
            or receipt.observed_at != snapshot.finalized_at
            or receipt.observed_at > now + dt.timedelta(minutes=5)
        ):
            raise InvalidFinalizedPaperTerminalError
        store = ExecutionStore(execution_path)
        if not store.is_initialized():
            raise InvalidFinalizedPaperTerminalError
        identity = store.ledger_snapshot_identity()
        if identity.generation != receipt.source_ledger_generation or identity.sha256 != receipt.source_ledger_sha256:
            raise InvalidFinalizedPaperTerminalError
        binding = store.account_binding()
        if binding is None:
            raise InvalidFinalizedPaperTerminalError
        recoveries = tuple(
            recovery
            for recovery in store.paper_stream_recoveries()
            if recovery.account_fingerprint == binding.account_fingerprint
            and _instant(recovery.completed_at).astimezone(NEW_YORK).date() == snapshot.session_date
            and recovery.execution_detail_complete
            and _instant(recovery.completed_at) <= receipt.observed_at
        )
        if not recoveries:
            raise InvalidFinalizedPaperTerminalError
        recovery = max(recoveries, key=lambda item: _instant(item.completed_at))
        recovery_snapshot = json.loads(recovery.snapshot_json)
        if (
            recovery.snapshot_sha256 != receipt.recovery_snapshot_sha256
            or not isinstance(recovery_snapshot, dict)
            or not isinstance(recovery_snapshot.get("orders"), list)
            or not isinstance(recovery_snapshot.get("positions"), list)
            or len(recovery_snapshot["orders"]) != snapshot.open_order_count
            or len(recovery_snapshot["positions"]) != snapshot.open_position_count
            or store.ledger_snapshot_identity() != identity
        ):
            raise InvalidFinalizedPaperTerminalError
    except (OSError, RuntimeError, sqlite3.Error, ValidationError, ValueError):
        return FinalizedPaperAuthorityFailure("corrupt", "paper_finalized_terminal_invalid")
    canonical = receipt.model_dump_json(exclude_none=False)
    return FinalizedPaperAuthority(
        receipt,
        hashlib.sha256(canonical.encode()).hexdigest(),
    )


def _instant(value: str) -> dt.datetime:
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise InvalidFinalizedPaperTerminalError
    return instant


__all__ = (
    "TERMINAL_FILENAME",
    "FinalizedPaperAuthority",
    "FinalizedPaperAuthorityFailure",
    "FinalizedPaperTerminalReceipt",
    "InvalidFinalizedPaperTerminalError",
    "read_finalized_paper_authority",
)
