from __future__ import annotations

import contextlib
import ctypes
import datetime as dt
import errno
import hashlib
import json
import os
import secrets
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, override

import pyarrow as pa
import pyarrow.parquet as pq

from trading_agent.canonical_dataset_models import CanonicalDatasetBatch

_CANONICAL_EVENT_SCHEMA_VERSION = 1
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_EVENTS_NAME = "events.parquet"
_MANIFEST_NAME = "dataset_manifest.json"
_STAGING_PREFIX = ".canonical-parquet-stage-"
_STAGING_ATTEMPTS = 16
_DARWIN_RENAME_EXCL = 0x00000004
_LINUX_RENAME_NOREPLACE = 0x00000001
_AT_FDCWD = -100
_WRITER_ERROR_MESSAGE = "canonical dataset parquet output could not be written"

_EVENT_SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.int8(), nullable=False),
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("source_provider", pa.string(), nullable=False),
        pa.field("source_feed", pa.string(), nullable=False),
        pa.field("provider_event_id", pa.string()),
        pa.field(
            "entity_refs",
            pa.list_(
                pa.struct(
                    [
                        pa.field("entity_type", pa.string(), nullable=False),
                        pa.field("entity_id", pa.string(), nullable=False),
                    ]
                )
            ),
            nullable=False,
        ),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC")),
        pa.field("published_at", pa.timestamp("us", tz="UTC")),
        pa.field("provider_time", pa.timestamp("us", tz="UTC")),
        pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("normalized_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("effective_from", pa.timestamp("us", tz="UTC")),
        pa.field("effective_to", pa.timestamp("us", tz="UTC")),
        pa.field("sequence_or_offset", pa.string()),
        pa.field("operation", pa.string(), nullable=False),
        pa.field("correction_of", pa.string()),
        pa.field("raw_receipt_ref", pa.string(), nullable=False),
        pa.field("content_hash", pa.string(), nullable=False),
        pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
    ]
)


class CanonicalDatasetParquetWriterError(ValueError):
    def __init__(self, *_args: object) -> None:
        super().__init__(_WRITER_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _WRITER_ERROR_MESSAGE

    @override
    def __repr__(self) -> str:
        return "CanonicalDatasetParquetWriterError()"


@dataclass(frozen=True, slots=True)
class CanonicalDatasetPublication:
    dataset_id: str
    dataset_directory: Path = field(repr=False)
    partition_relative_path: Path
    event_count: int
    canonical_event_content_sha256: str
    parquet_sha256: str


def write_canonical_dataset_parquet(
    batch: CanonicalDatasetBatch,
    *,
    output_root: Path,
) -> CanonicalDatasetPublication:
    """Publish a revalidated canonical batch as one private, immutable dataset directory."""
    try:
        validated = _revalidate_batch(batch)
        rows = _event_rows(validated)
        event_ids = tuple(row["event_id"] for row in rows)
        if event_ids != tuple(sorted(set(event_ids))):
            raise ValueError

        canonical_rows = _json_compatible(rows)
        canonical_event_content_sha256 = _sha256_bytes(_canonical_json_bytes(canonical_rows))
        partition = _partition_payload(validated)
        dataset_id = _sha256_bytes(
            _canonical_json_bytes(
                {
                    "canonical_event_rows": canonical_rows,
                    "partition": partition,
                    "raw_manifest": {
                        "content_sha256": validated.raw_manifest.content_sha256,
                        "manifest_id": validated.raw_manifest.manifest_id,
                    },
                }
            )
        )
        relative_path = _partition_relative_path(validated, dataset_id)
        dataset_directory, parquet_sha256 = _write_dataset(
            output_root=output_root,
            relative_path=relative_path,
            table=pa.Table.from_pylist(rows, schema=_EVENT_SCHEMA),
            sidecar=_sidecar_payload(
                batch=validated,
                dataset_id=dataset_id,
                partition=partition,
                canonical_event_content_sha256=canonical_event_content_sha256,
            ),
        )
        return CanonicalDatasetPublication(
            dataset_id=dataset_id,
            dataset_directory=dataset_directory,
            partition_relative_path=relative_path,
            event_count=len(rows),
            canonical_event_content_sha256=canonical_event_content_sha256,
            parquet_sha256=parquet_sha256,
        )
    except Exception:
        pass
    raise CanonicalDatasetParquetWriterError


def _revalidate_batch(batch: CanonicalDatasetBatch) -> CanonicalDatasetBatch:
    if type(batch) is not CanonicalDatasetBatch:
        raise ValueError
    return CanonicalDatasetBatch.model_validate(dict(batch.__dict__))


def _event_rows(batch: CanonicalDatasetBatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in batch.events:
        rows.append(
            {
                "schema_version": event.schema_version,
                "event_id": event.event_id,
                "source_provider": event.source_id.provider,
                "source_feed": event.source_id.feed,
                "provider_event_id": event.provider_event_id,
                "entity_refs": [
                    {"entity_type": entity.entity_type.value, "entity_id": entity.entity_id}
                    for entity in event.entity_refs
                ],
                "event_type": event.event_type,
                "event_time": _utc(event.event_time),
                "published_at": _utc(event.published_at),
                "provider_time": _utc(event.provider_time),
                "received_at": _utc(event.received_at),
                "normalized_at": _utc(event.normalized_at),
                "effective_from": _utc(event.effective_from),
                "effective_to": _utc(event.effective_to),
                "sequence_or_offset": event.sequence_or_offset,
                "operation": event.operation.value,
                "correction_of": event.correction_of,
                "raw_receipt_ref": event.raw_receipt_ref,
                "content_hash": event.content_hash,
                "quality_flags": list(event.quality_flags),
            }
        )
    return rows


def _utc(value: dt.datetime | None) -> dt.datetime | None:
    return None if value is None else value.astimezone(dt.UTC)


def _partition_payload(batch: CanonicalDatasetBatch) -> dict[str, Any]:
    partition = batch.partition
    return {
        "canonical_event_schema_version": partition.canonical_event_schema_version,
        "event_type": partition.event_type,
        "market_date": partition.market_date.isoformat(),
        "market_domain": partition.market_domain.value,
        "schema_version": partition.schema_version,
        "source_id": {
            "feed": partition.source_id.feed,
            "provider": partition.source_id.provider,
            "schema_version": partition.source_id.schema_version,
        },
    }


def _partition_relative_path(batch: CanonicalDatasetBatch, dataset_id: str) -> Path:
    partition = batch.partition
    return Path(
        f"source_provider={partition.source_id.provider}",
        f"source_feed={partition.source_id.feed}",
        f"market_domain={partition.market_domain.value}",
        f"event_type={partition.event_type}",
        f"market_date={partition.market_date.isoformat()}",
        f"canonical_event_schema_version={_CANONICAL_EVENT_SCHEMA_VERSION}",
        f"dataset_id={dataset_id}",
    )


def _sidecar_payload(
    *,
    batch: CanonicalDatasetBatch,
    dataset_id: str,
    partition: dict[str, Any],
    canonical_event_content_sha256: str,
) -> dict[str, Any]:
    return {
        "canonical_event_content_sha256": canonical_event_content_sha256,
        "dataset_id": dataset_id,
        "event_count": len(batch.events),
        "partition": partition,
        "raw_manifest_content_sha256": batch.raw_manifest.content_sha256,
        "raw_manifest_id": batch.raw_manifest.manifest_id,
        "schema_version": 1,
    }


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_dataset(
    *,
    output_root: Path,
    relative_path: Path,
    table: pa.Table,
    sidecar: dict[str, Any],
) -> tuple[Path, str]:
    _require_descriptor_operations()
    root_fd, root_path = _open_or_create_output_root(output_root)
    parent_fd: int | None = None
    stage_fd: int | None = None
    stage_name: str | None = None
    published = False
    try:
        parent_fd = _open_or_create_relative_directory(root_fd, relative_path.parts[:-1])
        dataset_name = relative_path.name
        _require_absent(parent_fd, dataset_name)
        stage_name, stage_fd = _create_staging_directory(parent_fd)
        stage_identity = _directory_identity(os.fstat(stage_fd))
        try:
            _write_parquet_file(stage_fd, table)
            parquet_sha256 = _sha256_file(stage_fd, _EVENTS_NAME)
            sidecar["parquet_sha256"] = parquet_sha256
            _write_private_file(stage_fd, _MANIFEST_NAME, _canonical_json_bytes(sidecar) + b"\n")
            _verify_directory_contents(stage_fd, {_EVENTS_NAME, _MANIFEST_NAME})
            os.fsync(stage_fd)
        finally:
            os.close(stage_fd)
            stage_fd = None

        dataset_directory = root_path / relative_path
        _rename_directory_exclusively(root_path / relative_path.parent / stage_name, dataset_directory)
        published = True
        _verify_published_directory(parent_fd, dataset_name, stage_identity)
        os.fsync(parent_fd)
        return dataset_directory, parquet_sha256
    except Exception:
        if stage_fd is not None:
            os.close(stage_fd)
            stage_fd = None
        if stage_name is not None and not published and parent_fd is not None:
            _remove_staging_directory(parent_fd, stage_name)
        raise
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        os.close(root_fd)


def _open_or_create_output_root(output_root: Path) -> tuple[int, Path]:
    if not isinstance(output_root, Path):
        raise OSError
    supplied_root = Path(os.path.abspath(output_root))
    if not supplied_root.name:
        raise OSError
    try:
        supplied_metadata = supplied_root.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(supplied_metadata.st_mode):
            raise OSError
    root_path = supplied_root.parent.resolve(strict=True) / supplied_root.name

    parent_fd = _open_directory_path(root_path.parent)
    try:
        root_fd = _open_or_create_private_directory(parent_fd, root_path.name)
    finally:
        os.close(parent_fd)
    return root_fd, root_path


def _open_directory_path(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    descriptor = os.open(absolute.anchor, _directory_open_flags())
    try:
        for component in absolute.parts[1:]:
            next_descriptor = _open_directory_at(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_or_create_relative_directory(root_fd: int, components: tuple[str, ...]) -> int:
    descriptor = os.dup(root_fd)
    try:
        for component in components:
            next_descriptor = _open_or_create_private_directory(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_or_create_private_directory(parent_fd: int, name: str) -> int:
    created = False
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        try:
            os.mkdir(name, mode=_DIRECTORY_MODE, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode):
        raise OSError

    descriptor = _open_directory_at(parent_fd, name)
    try:
        if created:
            os.fchmod(descriptor, _DIRECTORY_MODE)
        _assert_private_directory(descriptor)
        if created:
            os.fsync(parent_fd)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _create_staging_directory(parent_fd: int) -> tuple[str, int]:
    for _ in range(_STAGING_ATTEMPTS):
        stage_name = f"{_STAGING_PREFIX}{secrets.token_hex(16)}"
        try:
            os.mkdir(stage_name, mode=_DIRECTORY_MODE, dir_fd=parent_fd)
        except FileExistsError:
            continue
        try:
            stage_fd = _open_directory_at(parent_fd, stage_name)
            os.fchmod(stage_fd, _DIRECTORY_MODE)
            _assert_private_directory(stage_fd)
            os.fsync(parent_fd)
            return stage_name, stage_fd
        except Exception:
            with contextlib.suppress(Exception):
                _remove_staging_directory(parent_fd, stage_name)
            raise
    raise OSError


def _write_parquet_file(directory_fd: int, table: pa.Table) -> None:
    descriptor = _create_private_file(directory_fd, _EVENTS_NAME)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            pq.write_table(
                table,
                handle,
                compression="zstd",
                use_dictionary=False,
                write_statistics=False,
                version="2.6",
                data_page_version="1.0",
                use_compliant_nested_type=True,
                write_page_index=False,
                write_page_checksum=False,
                row_group_size=table.num_rows,
            )
            handle.flush()
        os.fsync(descriptor)
        _assert_private_file(directory_fd, _EVENTS_NAME)
    finally:
        os.close(descriptor)


def _write_private_file(directory_fd: int, name: str, content: bytes) -> None:
    descriptor = _create_private_file(directory_fd, name)
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
        _assert_private_file(directory_fd, name)
    finally:
        os.close(descriptor)


def _create_private_file(directory_fd: int, name: str) -> int:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        _FILE_MODE,
        dir_fd=directory_fd,
    )
    try:
        os.fchmod(descriptor, _FILE_MODE)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _sha256_file(directory_fd: int, name: str) -> str:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
        ):
            raise OSError
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError
        offset += written


def _require_absent(parent_fd: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise FileExistsError


def _rename_directory_exclusively(stage_path: Path, output_path: Path) -> None:
    if sys.platform == "darwin":
        _rename_directory_darwin(stage_path, output_path)
        return
    if sys.platform.startswith("linux"):
        _rename_directory_linux(stage_path, output_path)
        return
    raise OSError


def _rename_directory_darwin(stage_path: Path, output_path: Path) -> None:
    try:
        renamex_np = ctypes.CDLL("libc.dylib", use_errno=True).renamex_np
    except (AttributeError, OSError):
        raise OSError from None
    renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    renamex_np.restype = ctypes.c_int
    ctypes.set_errno(0)
    if renamex_np(os.fsencode(stage_path), os.fsencode(output_path), _DARWIN_RENAME_EXCL) != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, "exclusive canonical parquet publication failed")


def _rename_directory_linux(stage_path: Path, output_path: Path) -> None:
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError):
        raise OSError from None
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    if (
        renameat2(
            _AT_FDCWD,
            os.fsencode(stage_path),
            _AT_FDCWD,
            os.fsencode(output_path),
            _LINUX_RENAME_NOREPLACE,
        )
        != 0
    ):
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, "exclusive canonical parquet publication failed")


def _verify_published_directory(
    parent_fd: int,
    name: str,
    expected_identity: tuple[int, int],
) -> None:
    metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if _directory_identity(metadata) != expected_identity:
        raise OSError
    descriptor = _open_directory_at(parent_fd, name)
    try:
        _assert_private_directory(descriptor)
        _verify_directory_contents(descriptor, {_EVENTS_NAME, _MANIFEST_NAME})
    finally:
        os.close(descriptor)


def _verify_directory_contents(directory_fd: int, expected_names: set[str]) -> None:
    if set(os.listdir(directory_fd)) != expected_names:
        raise OSError
    for name in expected_names:
        _assert_private_file(directory_fd, name)


def _assert_private_file(directory_fd: int, name: str) -> None:
    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
    ):
        raise OSError


def _remove_staging_directory(parent_fd: int, stage_name: str) -> None:
    stage_fd = _open_directory_at(parent_fd, stage_name)
    try:
        for name in (_EVENTS_NAME, _MANIFEST_NAME):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=stage_fd)
        if os.listdir(stage_fd):
            raise OSError
    finally:
        os.close(stage_fd)
    os.rmdir(stage_name, dir_fd=parent_fd)


def _open_directory_at(parent_fd: int, name: str) -> int:
    return os.open(name, _directory_open_flags(), dir_fd=parent_fd)


def _directory_open_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _assert_private_directory(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != _DIRECTORY_MODE
    ):
        raise OSError


def _directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _require_descriptor_operations() -> None:
    required = (os.open, os.mkdir, os.rmdir, os.stat, os.unlink)
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "getuid")
        or not hasattr(os, "fchmod")
        or not hasattr(os, "fsync")
        or any(operation not in os.supports_dir_fd for operation in required)
        or os.stat not in os.supports_follow_symlinks
    ):
        raise OSError


__all__ = (
    "CanonicalDatasetParquetWriterError",
    "CanonicalDatasetPublication",
    "write_canonical_dataset_parquet",
)
