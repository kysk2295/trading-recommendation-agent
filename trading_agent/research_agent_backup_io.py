from __future__ import annotations

import ctypes
import errno
import os
import shutil
import sqlite3
import stat
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
)
from trading_agent.private_query_bytes import read_private_bytes_query_only
from trading_agent.research_agent_backup_models import ArtifactKind, BackupError, BackupFailureReason
from trading_agent.sqlite_uri import sqlite_read_only_uri

_FILE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700


@dataclass(frozen=True, slots=True)
class ReceiptSource:
    source: Path
    relative: str
    kind: ArtifactKind
    identity: tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class ReceiptScan:
    prefix: str
    kind: ArtifactKind
    max_entries: int


@dataclass(frozen=True, slots=True)
class ReceiptInventory:
    records: tuple[ReceiptSource, ...]
    entries: tuple[tuple[str, tuple[int, int, int, int, int]], ...]
    scan: ReceiptScan


def new_stage(destination: Path) -> tuple[Path, Path]:
    target = destination.expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise BackupError(BackupFailureReason.DESTINATION_EXISTS)
    require_private_directory(target.parent, BackupFailureReason.DESTINATION_INVALID)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))
    stage.chmod(_DIRECTORY_MODE)
    return target, stage


def require_private_directory(path: Path, reason: BackupFailureReason) -> None:
    try:
        descriptor = open_private_parent(path, create=False)
        try:
            require_private_directory_query_only(descriptor)
            require_open_directory_path(path, descriptor)
        finally:
            os.close(descriptor)
    except (OSError, TypeError, ValueError):
        raise BackupError(reason) from None


def receipt_inventory(root: Path, scan: ReceiptScan) -> ReceiptInventory:
    try:
        _require_directory_metadata(root.lstat(), BackupFailureReason.RECEIPT_INVALID)
        records: list[ReceiptSource] = []
        entries: list[tuple[str, tuple[int, int, int, int, int]]] = []
        for candidate in root.rglob("*"):
            if len(entries) >= scan.max_entries:
                raise BackupError(BackupFailureReason.LIMIT_EXCEEDED)
            metadata = candidate.lstat()
            relative = candidate.relative_to(root)
            relative_text = str(PurePosixPath(*relative.parts))
            entries.append((relative_text, _identity(metadata)))
            if stat.S_ISDIR(metadata.st_mode):
                _require_directory_metadata(metadata, BackupFailureReason.RECEIPT_INVALID)
            elif _private_file(metadata):
                records.append(
                    ReceiptSource(
                        candidate,
                        str(PurePosixPath(scan.prefix, *relative.parts)),
                        scan.kind,
                        _identity(metadata),
                    )
                )
            else:
                raise BackupError(BackupFailureReason.RECEIPT_INVALID)
        return ReceiptInventory(tuple(sorted(records, key=lambda item: item.relative)), tuple(sorted(entries)), scan)
    except FileNotFoundError:
        raise BackupError(BackupFailureReason.RECEIPT_INVALID) from None


def require_inventory_unchanged(
    root: Path,
    inventory: ReceiptInventory,
) -> None:
    scan = ReceiptScan(inventory.scan.prefix, inventory.scan.kind, len(inventory.entries))
    confirmation = receipt_inventory(root, scan)
    if confirmation.records != inventory.records or confirmation.entries != inventory.entries:
        raise BackupError(BackupFailureReason.SOURCE_DRIFT)


def snapshot_sqlite(source: Path, target: Path, max_bytes: int) -> tuple[bytes, int]:
    before = database_state(source)
    copied_bytes = sum(identity[2] for _, identity in before[:2] if identity is not None)
    if copied_bytes > max_bytes:
        raise BackupError(BackupFailureReason.LIMIT_EXCEEDED)
    _prepare_parent(target)
    copy_root = Path(tempfile.mkdtemp(prefix=".sqlite-source-", dir=target.parent))
    try:
        copied_source = copy_root / source.name
        for suffix, identity in before[:2]:
            if identity is not None:
                payload = read_private_bytes_query_only(Path(f"{source}{suffix}"), max_bytes=identity[2] + 1)
                write_private(Path(f"{copied_source}{suffix}"), payload)
        if database_state(source) != before:
            raise BackupError(BackupFailureReason.SOURCE_DRIFT)
        source_uri = sqlite_read_only_uri(copied_source)
        if before[1][1] is None:
            source_uri += "&immutable=1"
        with (
            closing(sqlite3.connect(source_uri, uri=True)) as source_connection,
            closing(sqlite3.connect(target)) as destination_connection,
        ):
            source_connection.backup(destination_connection)
    finally:
        shutil.rmtree(copy_root)
    target.chmod(_FILE_MODE)
    if database_state(source) != before:
        raise BackupError(BackupFailureReason.SOURCE_DRIFT)
    payload = read_private_bytes_query_only(target, max_bytes=target.stat().st_size + 1)
    if len(payload) > max_bytes:
        raise BackupError(BackupFailureReason.LIMIT_EXCEEDED)
    return payload, copied_bytes


def copy_receipt(receipt: ReceiptSource, stage: Path, max_bytes: int) -> bytes:
    if receipt.identity[2] > max_bytes:
        raise BackupError(BackupFailureReason.LIMIT_EXCEEDED)
    payload = read_private_bytes_query_only(receipt.source, max_bytes=receipt.identity[2] + 1)
    if _identity(receipt.source.lstat()) != receipt.identity:
        raise BackupError(BackupFailureReason.SOURCE_DRIFT)
    write_private(stage / receipt.relative, payload)
    return payload


def require_bundle_shape(bundle: Path, expected: set[str], max_files: int) -> None:
    expected_directories: set[str] = set()
    for item in expected:
        parent = PurePosixPath(item).parent
        while str(parent) != ".":
            expected_directories.add(str(parent))
            parent = parent.parent
    if len(expected) - 1 > max_files:
        raise BackupError(BackupFailureReason.LIMIT_EXCEEDED)
    entry_limit = len(expected) + len(expected_directories)
    entries: list[Path] = []
    for path in bundle.rglob("*"):
        if len(entries) >= entry_limit:
            raise BackupError(BackupFailureReason.MANIFEST_INVALID)
        entries.append(path)
    found = {str(PurePosixPath(*path.relative_to(bundle).parts)) for path in entries if path.is_file()}
    found_directories = {str(PurePosixPath(*path.relative_to(bundle).parts)) for path in entries if path.is_dir()}
    if found != expected or found_directories != expected_directories or any(path.is_symlink() for path in entries):
        raise BackupError(BackupFailureReason.MANIFEST_INVALID)
    for path in entries:
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            _require_directory_metadata(metadata, BackupFailureReason.MANIFEST_INVALID)


def write_private(path: Path, payload: bytes) -> None:
    _prepare_parent(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, _FILE_MODE)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def publish(stage: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise BackupError(BackupFailureReason.DESTINATION_EXISTS)
    library = ctypes.CDLL(None, use_errno=True)
    try:
        if os.uname().sysname == "Darwin":
            result = library.renamex_np(os.fsencode(stage), os.fsencode(destination), 4)
        else:
            result = library.renameat2(-100, os.fsencode(stage), -100, os.fsencode(destination), 1)
    except AttributeError:
        raise BackupError(BackupFailureReason.PUBLICATION_FAILED) from None
    if result == 0:
        return
    if ctypes.get_errno() in {errno.EEXIST, errno.ENOTEMPTY}:
        raise BackupError(BackupFailureReason.DESTINATION_EXISTS)
    raise BackupError(BackupFailureReason.PUBLICATION_FAILED)


def clean_stage(stage: Path) -> None:
    if stage.exists():
        shutil.rmtree(stage)


def database_state(path: Path) -> tuple[tuple[str, tuple[int, int, int, int, int] | None], ...]:
    states: list[tuple[str, tuple[int, int, int, int, int] | None]] = []
    for suffix in ("", "-wal", "-shm"):
        try:
            metadata = Path(f"{path}{suffix}").lstat()
        except FileNotFoundError:
            states.append((suffix, None))
            continue
        if not _private_file(metadata):
            raise BackupError(BackupFailureReason.SOURCE_INVALID)
        states.append((suffix, _identity(metadata)))
    if states[0][1] is None:
        raise BackupError(BackupFailureReason.SOURCE_INVALID)
    return tuple(states)


def _require_directory_metadata(metadata: os.stat_result, reason: BackupFailureReason) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != _DIRECTORY_MODE
    ):
        raise BackupError(reason)


def _prepare_parent(path: Path) -> None:
    missing: list[Path] = []
    candidate = path.parent
    while not candidate.exists():
        missing.append(candidate)
        candidate = candidate.parent
    path.parent.mkdir(parents=True, mode=_DIRECTORY_MODE, exist_ok=True)
    for directory in missing:
        directory.chmod(_DIRECTORY_MODE)


def _private_file(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == _FILE_MODE
        and metadata.st_nlink == 1
    )


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


__all__ = (
    "ReceiptInventory",
    "ReceiptScan",
    "ReceiptSource",
    "clean_stage",
    "copy_receipt",
    "new_stage",
    "publish",
    "receipt_inventory",
    "require_bundle_shape",
    "require_inventory_unchanged",
    "require_private_directory",
    "snapshot_sqlite",
    "write_private",
)
