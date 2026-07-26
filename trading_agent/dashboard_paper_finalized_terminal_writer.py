from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path
from typing import override

from trading_agent.dashboard_paper_finalized_terminal import (
    TERMINAL_FILENAME,
    FinalizedPaperTerminalReceipt,
)
from trading_agent.dashboard_paper_lifecycle import project_paper_lifecycle
from trading_agent.execution_store_reader import ExecutionStoreReader
from trading_agent.lane_contract_keys import lane_daily_snapshot_key
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
)
from trading_agent.us_equity_calendar import NEW_YORK


class FinalizedPaperTerminalConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "같은 finalized Paper snapshot의 terminal receipt가 충돌합니다"


class InvalidFinalizedPaperTerminalPublicationError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "finalized Paper terminal 발행 근거가 완전하지 않습니다"


def publish_finalized_paper_terminal(
    outputs: Path,
    snapshot: LaneDailySnapshot,
    execution: ExecutionStoreReader,
) -> bool:
    try:
        expected_execution = (outputs / "paper" / "execution.sqlite3").resolve(strict=False)
        if execution.path != expected_execution:
            raise InvalidFinalizedPaperTerminalPublicationError
        identity = execution.ledger_snapshot_identity()
        if (
            identity.generation != snapshot.source_ledger_generation
            or identity.sha256 != snapshot.source_ledger_sha256
        ):
            raise InvalidFinalizedPaperTerminalPublicationError
        lifecycle = project_paper_lifecycle(outputs, snapshot)
        if lifecycle.state != "populated" or lifecycle.blocker_code is not None:
            raise InvalidFinalizedPaperTerminalPublicationError
        binding = execution.account_binding()
        if binding is None:
            raise InvalidFinalizedPaperTerminalPublicationError
        recoveries = tuple(
            recovery
            for recovery in execution.paper_stream_recoveries()
            if recovery.account_fingerprint == binding.account_fingerprint
            and recovery.execution_detail_complete
            and _instant(recovery.completed_at).astimezone(NEW_YORK).date()
            == snapshot.session_date
            and _instant(recovery.completed_at) <= snapshot.finalized_at
        )
        if not recoveries:
            raise InvalidFinalizedPaperTerminalPublicationError
        recovery = max(recoveries, key=lambda item: _instant(item.completed_at))
        recovered = json.loads(recovery.snapshot_json)
        if (
            not isinstance(recovered, dict)
            or not isinstance(recovered.get("orders"), list)
            or not isinstance(recovered.get("positions"), list)
            or len(recovered["orders"]) != snapshot.open_order_count
            or len(recovered["positions"]) != snapshot.open_position_count
        ):
            raise InvalidFinalizedPaperTerminalPublicationError
        receipt = FinalizedPaperTerminalReceipt(
            lane_id=snapshot.lane_id,
            session_date=snapshot.session_date,
            manifest_key=snapshot.manifest_key,
            snapshot_key=str(lane_daily_snapshot_key(snapshot)),
            source_ledger_generation=identity.generation,
            source_ledger_sha256=identity.sha256,
            strategy_versions=snapshot.champion_strategy_versions,
            recovery_snapshot_sha256=recovery.snapshot_sha256,
            observed_at=snapshot.finalized_at,
        )
        if execution.ledger_snapshot_identity() != identity:
            raise InvalidFinalizedPaperTerminalPublicationError
        return _append_receipt(outputs / "paper" / TERMINAL_FILENAME, receipt)
    except FinalizedPaperTerminalConflictError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise InvalidFinalizedPaperTerminalPublicationError from None


def _append_receipt(
    path: Path,
    receipt: FinalizedPaperTerminalReceipt,
) -> bool:
    parent = open_private_parent(path.parent, create=True)
    try:
        require_private_directory(parent)
        lock = _open_lock(parent, path.name)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = _read_existing(parent, path.name)
            receipts = tuple(
                FinalizedPaperTerminalReceipt.model_validate_json(line)
                for line in existing.splitlines()
            )
            matches = tuple(item for item in receipts if item.snapshot_key == receipt.snapshot_key)
            if matches:
                if len(matches) == 1 and matches[0] == receipt:
                    return False
                raise FinalizedPaperTerminalConflictError
            content = f"{existing}{receipt.model_dump_json()}\n"
            _replace(parent, path.name, content)
            require_open_directory_path(path.parent, parent)
            return True
        finally:
            os.close(lock)
    finally:
        os.close(parent)


def _open_lock(parent: int, name: str) -> int:
    lock_name = f".{name}.lock"
    try:
        descriptor = os.open(
            lock_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
    except FileExistsError:
        descriptor = os.open(
            lock_name,
            os.O_RDWR | os.O_NOFOLLOW,
            dir_fd=parent,
        )
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
    ):
        os.close(descriptor)
        raise InvalidFinalizedPaperTerminalPublicationError
    os.fchmod(descriptor, 0o600)
    return descriptor


def _read_existing(parent: int, name: str) -> str:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
    except FileNotFoundError:
        return ""
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size > 128 * 1024
        ):
            raise InvalidFinalizedPaperTerminalPublicationError
        return os.read(descriptor, 128 * 1024 + 1).decode()
    finally:
        os.close(descriptor)


def _replace(parent: int, name: str, content: str) -> None:
    stage = f".{name}.{secrets.token_hex(12)}.writing"
    descriptor = os.open(
        stage,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent,
    )
    try:
        with os.fdopen(os.dup(descriptor), "w", encoding="utf-8") as handle:
            _ = handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(stage, dir_fd=parent)
        os.fsync(parent)


def _instant(value: str) -> dt.datetime:
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise InvalidFinalizedPaperTerminalPublicationError
    return instant


__all__ = (
    "FinalizedPaperTerminalConflictError",
    "InvalidFinalizedPaperTerminalPublicationError",
    "publish_finalized_paper_terminal",
)
