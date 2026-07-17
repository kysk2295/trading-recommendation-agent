"""Private local replay verification for canonical Parquet datasets."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, override

import duckdb
import pyarrow.parquet as pq

from trading_agent.canonical_event_models import CanonicalEntityRef, CanonicalEventEnvelope
from trading_agent.canonical_parquet_writer import _EVENT_SCHEMA
from trading_agent.data_capability_models import DataSourceId
from trading_agent.security_master_models import DataMarketDomain

_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_EVENTS_NAME = "events.parquet"
_MANIFEST_NAME = "dataset_manifest.json"
_MAX_MANIFEST_BYTES = 64 * 1024
_REPLAY_ERROR_MESSAGE = "canonical dataset replay could not be verified"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_EVENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_MANIFEST_KEYS = frozenset(
    {
        "canonical_event_content_sha256",
        "dataset_id",
        "event_count",
        "parquet_sha256",
        "partition",
        "raw_manifest_content_sha256",
        "raw_manifest_id",
        "schema_version",
    }
)
_PARTITION_KEYS = frozenset(
    {
        "canonical_event_schema_version",
        "event_type",
        "market_date",
        "market_domain",
        "schema_version",
        "source_id",
    }
)
_SOURCE_ID_KEYS = frozenset({"feed", "provider", "schema_version"})
_MARKET_DOMAINS = frozenset(domain.value for domain in DataMarketDomain)
_EVENT_COLUMNS = (
    "schema_version",
    "event_id",
    "source_provider",
    "source_feed",
    "provider_event_id",
    "entity_refs",
    "event_type",
    "event_time",
    "published_at",
    "provider_time",
    "received_at",
    "normalized_at",
    "effective_from",
    "effective_to",
    "sequence_or_offset",
    "operation",
    "correction_of",
    "raw_receipt_ref",
    "content_hash",
    "quality_flags",
)
_SELECT_EVENTS_SQL = """
SELECT
    schema_version,
    event_id,
    source_provider,
    source_feed,
    provider_event_id,
    entity_refs,
    event_type,
    event_time,
    published_at,
    provider_time,
    received_at,
    normalized_at,
    effective_from,
    effective_to,
    sequence_or_offset,
    operation,
    correction_of,
    raw_receipt_ref,
    content_hash,
    quality_flags
FROM read_parquet(?, hive_partitioning = false)
ORDER BY event_id
"""


class CanonicalDatasetReplayError(ValueError):
    def __init__(self, *_args: object) -> None:
        super().__init__(_REPLAY_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _REPLAY_ERROR_MESSAGE

    @override
    def __repr__(self) -> str:
        return "CanonicalDatasetReplayError()"


@dataclass(frozen=True, slots=True)
class CanonicalDatasetReplay:
    dataset_id: str
    event_count: int
    canonical_event_content_sha256: str
    parquet_sha256: str
    raw_manifest_id: str
    raw_manifest_content_sha256: str


@dataclass(frozen=True, slots=True)
class _Sidecar:
    dataset_id: str
    event_count: int
    canonical_event_content_sha256: str
    parquet_sha256: str
    raw_manifest_id: str
    raw_manifest_content_sha256: str
    partition: dict[str, object]
    source_provider: str
    source_feed: str
    event_type: str
    canonical_event_schema_version: int
    expected_path_components: tuple[str, ...]


def replay_canonical_dataset(dataset_directory: Path) -> CanonicalDatasetReplay:
    """Verify one completed private dataset through an in-memory DuckDB replay."""
    result: CanonicalDatasetReplay | None = None
    try:
        result = _replay(dataset_directory)
    except Exception:
        result = None
    if result is None:
        raise CanonicalDatasetReplayError
    return result


def _replay(dataset_directory: Path) -> CanonicalDatasetReplay:
    if not isinstance(dataset_directory, Path):
        raise ValueError
    partition_directories = _partition_directories(dataset_directory)
    directory_fd = _open_private_dataset_directory(partition_directories)
    sidecar: _Sidecar | None = None
    parquet_fd: int | None = None
    try:
        if set(os.listdir(directory_fd)) != {_EVENTS_NAME, _MANIFEST_NAME}:
            raise ValueError
        sidecar = _validate_sidecar(_read_private_file(directory_fd, _MANIFEST_NAME))
        if tuple(path.name for path in reversed(partition_directories)) != sidecar.expected_path_components:
            raise ValueError
        parquet_fd = _open_private_file(directory_fd, _EVENTS_NAME)
    finally:
        os.close(directory_fd)

    if sidecar is None or parquet_fd is None:
        raise ValueError
    try:
        if _sha256_file_descriptor(parquet_fd) != sidecar.parquet_sha256:
            raise ValueError
        _validate_parquet_schema(parquet_fd)
        physical_rows = _canonical_rows_from_duckdb(_stable_fd_path(parquet_fd))
        if _sha256_file_descriptor(parquet_fd) != sidecar.parquet_sha256:
            raise ValueError
    finally:
        os.close(parquet_fd)

    canonical_rows = _revalidate_canonical_rows(physical_rows, sidecar)
    event_ids = _event_ids(canonical_rows)
    if (
        len(canonical_rows) != sidecar.event_count
        or event_ids != tuple(sorted(set(event_ids)))
    ):
        raise ValueError

    canonical_event_content_sha256 = _sha256_bytes(_canonical_json_bytes(canonical_rows))
    if canonical_event_content_sha256 != sidecar.canonical_event_content_sha256:
        raise ValueError
    if _dataset_id(canonical_rows, sidecar) != sidecar.dataset_id:
        raise ValueError
    return CanonicalDatasetReplay(
        dataset_id=sidecar.dataset_id,
        event_count=sidecar.event_count,
        canonical_event_content_sha256=sidecar.canonical_event_content_sha256,
        parquet_sha256=sidecar.parquet_sha256,
        raw_manifest_id=sidecar.raw_manifest_id,
        raw_manifest_content_sha256=sidecar.raw_manifest_content_sha256,
    )


def _partition_directories(dataset_directory: Path) -> tuple[Path, ...]:
    directories = [dataset_directory]
    for _ in range(6):
        parent = directories[-1].parent
        if parent == directories[-1]:
            raise ValueError
        directories.append(parent)
    return tuple(directories)


def _open_private_dataset_directory(partition_directories: tuple[Path, ...]) -> int:
    for directory in partition_directories:
        _assert_private_directory_metadata(directory.lstat())

    output_root = partition_directories[-1].parent
    _assert_private_directory_metadata(output_root.lstat())
    descriptor = os.open(
        output_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    failed = False
    try:
        _assert_private_directory_metadata(os.fstat(descriptor))
        for directory in reversed(partition_directories):
            next_descriptor = os.open(
                directory.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
            _assert_private_directory_metadata(os.fstat(descriptor))
    except Exception:
        failed = True
    if failed:
        os.close(descriptor)
        raise ValueError
    return descriptor


def _assert_private_directory_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != _DIRECTORY_MODE
    ):
        raise ValueError


def _open_private_file(directory_fd: int, name: str) -> int:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    failed = False
    try:
        _assert_private_file_metadata(os.fstat(descriptor))
    except Exception:
        failed = True
    if failed:
        os.close(descriptor)
        raise ValueError
    return descriptor


def _assert_private_file_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
    ):
        raise ValueError


def _read_private_file(directory_fd: int, name: str) -> bytes:
    descriptor = _open_private_file(directory_fd, name)
    try:
        size = os.fstat(descriptor).st_size
        if size < 0 or size > _MAX_MANIFEST_BYTES:
            raise ValueError
        content = bytearray()
        while chunk := os.read(descriptor, 16 * 1024):
            content.extend(chunk)
        if len(content) != size:
            raise ValueError
        return bytes(content)
    finally:
        os.close(descriptor)


def _validate_sidecar(content: bytes) -> _Sidecar:
    if not content.endswith(b"\n") or any(byte > 0x7F for byte in content):
        raise ValueError
    parsed: object = json.loads(content[:-1].decode("ascii"))
    if type(parsed) is not dict:
        raise ValueError
    manifest = cast(dict[str, object], parsed)
    if _canonical_json_bytes(manifest) + b"\n" != content or set(manifest) != _MANIFEST_KEYS:
        raise ValueError

    schema_version = manifest["schema_version"]
    event_count = manifest["event_count"]
    if type(schema_version) is not int or schema_version != 1 or type(event_count) is not int or event_count < 1:
        raise ValueError
    partition = _validate_partition(manifest["partition"])
    dataset_id = _require_sha256(manifest["dataset_id"])
    source_provider, source_feed = _validate_source_id(partition["source_id"])
    event_type = _require_text(partition["event_type"])
    return _Sidecar(
        dataset_id=dataset_id,
        event_count=event_count,
        canonical_event_content_sha256=_require_sha256(manifest["canonical_event_content_sha256"]),
        parquet_sha256=_require_sha256(manifest["parquet_sha256"]),
        raw_manifest_id=_require_sha256(manifest["raw_manifest_id"]),
        raw_manifest_content_sha256=_require_sha256(manifest["raw_manifest_content_sha256"]),
        partition=partition,
        source_provider=source_provider,
        source_feed=source_feed,
        event_type=event_type,
        canonical_event_schema_version=cast(int, partition["canonical_event_schema_version"]),
        expected_path_components=_expected_path_components(partition, dataset_id),
    )


def _validate_partition(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError
    partition = cast(dict[str, object], value)
    if set(partition) != _PARTITION_KEYS:
        raise ValueError
    if type(partition["schema_version"]) is not int or partition["schema_version"] != 1:
        raise ValueError
    if (
        type(partition["canonical_event_schema_version"]) is not int
        or partition["canonical_event_schema_version"] != 1
    ):
        raise ValueError
    provider, feed = _validate_source_id(partition["source_id"])
    market_domain = _require_text(partition["market_domain"])
    event_type = _require_text(partition["event_type"])
    _require_market_date(partition["market_date"])
    if (
        market_domain not in _MARKET_DOMAINS
        or _EVENT_TYPE.fullmatch(event_type) is None
        or _SLUG.fullmatch(provider) is None
        or _SLUG.fullmatch(feed) is None
    ):
        raise ValueError
    return partition


def _validate_source_id(value: object) -> tuple[str, str]:
    if type(value) is not dict:
        raise ValueError
    source_id = cast(dict[str, object], value)
    if set(source_id) != _SOURCE_ID_KEYS or type(source_id["schema_version"]) is not int:
        raise ValueError
    if source_id["schema_version"] != 1:
        raise ValueError
    return _require_text(source_id["provider"]), _require_text(source_id["feed"])


def _require_text(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    return value


def _require_sha256(value: object) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError
    return value


def _require_market_date(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    parsed: dt.date | None = None
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.isoformat() != value:
        raise ValueError
    return value


def _expected_path_components(partition: dict[str, object], dataset_id: str) -> tuple[str, ...]:
    source_id = cast(dict[str, object], partition["source_id"])
    return (
        f"source_provider={source_id['provider']}",
        f"source_feed={source_id['feed']}",
        f"market_domain={partition['market_domain']}",
        f"event_type={partition['event_type']}",
        f"market_date={partition['market_date']}",
        f"canonical_event_schema_version={partition['canonical_event_schema_version']}",
        f"dataset_id={dataset_id}",
    )


def _sha256_file_descriptor(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _validate_parquet_schema(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    schema: Any = None
    failed = False
    handle = os.fdopen(os.dup(descriptor), "rb")
    try:
        schema = pq.read_schema(handle)
    except Exception:
        failed = True
    finally:
        handle.close()
    if failed or schema is None or _schema_has_metadata(schema) or not schema.equals(_EVENT_SCHEMA):
        raise ValueError


def _stable_fd_path(descriptor: int) -> str:
    candidate = f"/dev/fd/{descriptor}"
    probe = os.open(candidate, os.O_RDONLY)
    try:
        verified_metadata = os.fstat(descriptor)
        probe_metadata = os.fstat(probe)
    finally:
        os.close(probe)
    if (
        not stat.S_ISREG(verified_metadata.st_mode)
        or (verified_metadata.st_dev, verified_metadata.st_ino)
        != (probe_metadata.st_dev, probe_metadata.st_ino)
    ):
        raise ValueError
    return candidate


def _schema_has_metadata(schema: Any) -> bool:
    return schema.metadata is not None or any(_field_has_metadata(field) for field in schema)


def _field_has_metadata(field: Any) -> bool:
    if field.metadata is not None:
        return True
    value_field = getattr(field.type, "value_field", None)
    if value_field is not None and _field_has_metadata(value_field):
        return True
    field_count = getattr(field.type, "num_fields", 0)
    return any(_field_has_metadata(field.type.field(index)) for index in range(field_count))


def _canonical_rows_from_duckdb(verified_parquet_path: str) -> list[dict[str, object]]:
    connection = duckdb.connect(database=":memory:")
    try:
        values = connection.execute(_SELECT_EVENTS_SQL, [verified_parquet_path]).to_arrow_table().to_pylist()
    finally:
        connection.close()
    rows: list[dict[str, object]] = []
    for value in values:
        if type(value) is not dict or set(value) != set(_EVENT_COLUMNS):
            raise ValueError
        rows.append(
            {
                column: value[column]
                for column in _EVENT_COLUMNS
            }
        )
    return rows


def _revalidate_canonical_rows(physical_rows: list[dict[str, object]], sidecar: _Sidecar) -> list[dict[str, object]]:
    canonical_rows: list[dict[str, object]] = []
    for physical_row in physical_rows:
        canonical_row = _json_compatible(_writer_compatible_event_row(_event_from_physical_row(physical_row, sidecar)))
        if type(canonical_row) is not dict:
            raise ValueError
        canonical_rows.append(cast(dict[str, object], canonical_row))
    return canonical_rows


def _event_from_physical_row(physical_row: dict[str, object], sidecar: _Sidecar) -> CanonicalEventEnvelope:
    if (
        physical_row["schema_version"] != sidecar.canonical_event_schema_version
        or physical_row["source_provider"] != sidecar.source_provider
        or physical_row["source_feed"] != sidecar.source_feed
        or physical_row["event_type"] != sidecar.event_type
    ):
        raise ValueError
    entity_refs = physical_row["entity_refs"]
    quality_flags = physical_row["quality_flags"]
    if type(entity_refs) is not list or type(quality_flags) is not list:
        raise ValueError
    event = CanonicalEventEnvelope.model_validate(
        {
            "schema_version": physical_row["schema_version"],
            "event_id": physical_row["event_id"],
            "source_id": DataSourceId(
                provider=_require_text(physical_row["source_provider"]),
                feed=_require_text(physical_row["source_feed"]),
            ),
            "provider_event_id": physical_row["provider_event_id"],
            "entity_refs": _entity_refs_from_physical_row(entity_refs),
            "event_type": physical_row["event_type"],
            "event_time": physical_row["event_time"],
            "published_at": physical_row["published_at"],
            "provider_time": physical_row["provider_time"],
            "received_at": physical_row["received_at"],
            "normalized_at": physical_row["normalized_at"],
            "effective_from": physical_row["effective_from"],
            "effective_to": physical_row["effective_to"],
            "sequence_or_offset": physical_row["sequence_or_offset"],
            "operation": physical_row["operation"],
            "correction_of": physical_row["correction_of"],
            "raw_receipt_ref": physical_row["raw_receipt_ref"],
            "content_hash": physical_row["content_hash"],
            "quality_flags": tuple(quality_flags),
        }
    )
    if (
        event.schema_version != sidecar.canonical_event_schema_version
        or event.source_id.provider != sidecar.source_provider
        or event.source_id.feed != sidecar.source_feed
        or event.event_type != sidecar.event_type
    ):
        raise ValueError
    return event


def _entity_refs_from_physical_row(value: list[object]) -> tuple[CanonicalEntityRef, ...]:
    entity_refs: list[CanonicalEntityRef] = []
    for item in value:
        if type(item) is not dict:
            raise ValueError
        entity_ref = cast(dict[str, object], item)
        entity_refs.append(
            CanonicalEntityRef.model_validate(
                {
                    "entity_type": entity_ref["entity_type"],
                    "entity_id": entity_ref["entity_id"],
                }
            )
        )
    return tuple(entity_refs)


def _writer_compatible_event_row(event: CanonicalEventEnvelope) -> dict[str, object]:
    return {
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "source_provider": event.source_id.provider,
        "source_feed": event.source_id.feed,
        "provider_event_id": event.provider_event_id,
        "entity_refs": [
            {"entity_type": entity_ref.entity_type.value, "entity_id": entity_ref.entity_id}
            for entity_ref in event.entity_refs
        ],
        "event_type": event.event_type,
        "event_time": event.event_time,
        "published_at": event.published_at,
        "provider_time": event.provider_time,
        "received_at": event.received_at,
        "normalized_at": event.normalized_at,
        "effective_from": event.effective_from,
        "effective_to": event.effective_to,
        "sequence_or_offset": event.sequence_or_offset,
        "operation": event.operation.value,
        "correction_of": event.correction_of,
        "raw_receipt_ref": event.raw_receipt_ref,
        "content_hash": event.content_hash,
        "quality_flags": list(event.quality_flags),
    }


def _event_ids(rows: list[dict[str, object]]) -> tuple[str, ...]:
    event_ids: list[str] = []
    for row in rows:
        event_id = row["event_id"]
        if type(event_id) is not str:
            raise ValueError
        event_ids.append(event_id)
    return tuple(event_ids)


def _json_compatible(value: object) -> object:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    return value


def _dataset_id(canonical_rows: list[dict[str, object]], sidecar: _Sidecar) -> str:
    return _sha256_bytes(
        _canonical_json_bytes(
            {
                "canonical_event_rows": canonical_rows,
                "partition": sidecar.partition,
                "raw_manifest": {
                    "content_sha256": sidecar.raw_manifest_content_sha256,
                    "manifest_id": sidecar.raw_manifest_id,
                },
            }
        )
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
