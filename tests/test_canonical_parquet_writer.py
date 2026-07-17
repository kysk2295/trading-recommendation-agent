from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import trading_agent.canonical_parquet_writer as parquet_writer
from trading_agent.canonical_dataset_models import CanonicalDatasetBatch, CanonicalDatasetPartition
from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.data_capability_models import DataSourceId
from trading_agent.raw_object_manifest_models import RawReceipt, RawReceiptPayload
from trading_agent.raw_receipt_projection import project_raw_receipt_partition
from trading_agent.security_master_models import DataMarketDomain

MARKET_DATE = dt.date(2026, 7, 17)
RECEIVED_AT = dt.datetime(2026, 7, 17, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=9)))
RAW_SECRET = b"canonical-parquet-private-raw-payload"
ACCOUNT_SECRET = "canonical-parquet-private-account-id"


def test_writes_deterministic_identity_and_bytes_to_independent_roots(tmp_path: Path) -> None:
    batch = _batch()

    first = parquet_writer.write_canonical_dataset_parquet(batch, output_root=tmp_path / "first")
    second = parquet_writer.write_canonical_dataset_parquet(batch, output_root=tmp_path / "second")

    assert first.dataset_id == second.dataset_id
    assert first.partition_relative_path == second.partition_relative_path
    assert first.parquet_sha256 == second.parquet_sha256
    assert first.dataset_directory / "events.parquet" != second.dataset_directory / "events.parquet"
    assert (first.dataset_directory / "events.parquet").read_bytes() == (
        second.dataset_directory / "events.parquet"
    ).read_bytes()
    assert (first.dataset_directory / "dataset_manifest.json").read_bytes() == (
        second.dataset_directory / "dataset_manifest.json"
    ).read_bytes()
    assert batch == _batch()


def test_writes_explicit_schema_rows_and_utc_timestamps(tmp_path: Path) -> None:
    batch = _batch()
    publication = parquet_writer.write_canonical_dataset_parquet(batch, output_root=tmp_path / "output")
    table = pq.read_table(publication.dataset_directory / "events.parquet")

    assert table.schema == pa.schema(
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
    assert table.to_pylist() == [
        {
            "schema_version": 1,
            "event_id": "event-0001",
            "source_provider": "synthetic",
            "source_feed": "market",
            "provider_event_id": None,
            "entity_refs": [{"entity_type": "instrument", "entity_id": "us-eq-fixture-0001"}],
            "event_type": "minute_bar",
            "event_time": dt.datetime(2026, 7, 17, 0, 30, tzinfo=dt.UTC),
            "published_at": None,
            "provider_time": None,
            "received_at": dt.datetime(2026, 7, 17, 0, 30, tzinfo=dt.UTC),
            "normalized_at": dt.datetime(2026, 7, 17, 0, 30, 1, tzinfo=dt.UTC),
            "effective_from": None,
            "effective_to": None,
            "sequence_or_offset": "42",
            "operation": "original",
            "correction_of": None,
            "raw_receipt_ref": "a" * 64,
            "content_hash": "c" * 64,
            "quality_flags": ["complete"],
        }
    ]


def test_outputs_expose_only_safe_public_data(tmp_path: Path) -> None:
    batch = _batch()
    publication = parquet_writer.write_canonical_dataset_parquet(batch, output_root=tmp_path / "output")
    sidecar = (publication.dataset_directory / "dataset_manifest.json").read_bytes()
    parquet_bytes = (publication.dataset_directory / "events.parquet").read_bytes()
    metadata = pq.ParquetFile(publication.dataset_directory / "events.parquet").metadata.metadata
    sidecar_data = json.loads(sidecar)

    assert RAW_SECRET not in sidecar
    assert RAW_SECRET not in parquet_bytes
    assert ACCOUNT_SECRET.encode() not in sidecar
    assert ACCOUNT_SECRET.encode() not in parquet_bytes
    assert all(RAW_SECRET not in key and RAW_SECRET not in value for key, value in (metadata or {}).items())
    assert set(sidecar_data) == {
        "canonical_event_content_sha256",
        "dataset_id",
        "event_count",
        "parquet_sha256",
        "partition",
        "raw_manifest_content_sha256",
        "raw_manifest_id",
        "schema_version",
    }
    assert sidecar == _canonical_json_bytes(sidecar_data)
    assert "receipts" not in sidecar_data
    assert "payload" not in sidecar_data
    assert RAW_SECRET.decode() not in repr(publication)
    assert ACCOUNT_SECRET not in repr(publication)


def test_publication_repr_omits_the_caller_supplied_output_path(tmp_path: Path) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(
        _batch(),
        output_root=tmp_path / ACCOUNT_SECRET,
    )

    assert publication.dataset_directory.name.startswith("dataset_id=")
    assert ACCOUNT_SECRET not in repr(publication)


def test_sanitized_writer_error_has_no_sensitive_exception_chain(tmp_path: Path) -> None:
    sensitive_parent = tmp_path / ACCOUNT_SECRET
    sensitive_parent.write_text("not a directory", encoding="utf-8")

    with pytest.raises(parquet_writer.CanonicalDatasetParquetWriterError) as captured:
        parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=sensitive_parent / "output")

    error = captured.value
    assert str(error) == "canonical dataset parquet output could not be written"
    assert repr(error) == "CanonicalDatasetParquetWriterError()"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert ACCOUNT_SECRET not in str(error)
    assert ACCOUNT_SECRET not in repr(error)
    assert ACCOUNT_SECRET not in str(error.__cause__)
    assert ACCOUNT_SECRET not in repr(error.__cause__)
    assert ACCOUNT_SECRET not in str(error.__context__)
    assert ACCOUNT_SECRET not in repr(error.__context__)


def test_revalidates_only_an_exact_untampered_batch_without_mutating_it(tmp_path: Path) -> None:
    batch = _batch()
    original = batch.model_dump(mode="python")
    invalid_constructed = CanonicalDatasetBatch.model_construct(
        schema_version=1,
        partition=batch.partition,
        raw_manifest=batch.raw_manifest,
        events=(),
    )
    tampered = _batch()
    vars(tampered)["account_id"] = ACCOUNT_SECRET

    subclass = _BatchSubclass.model_construct(**dict(batch.__dict__))
    for value in (subclass, invalid_constructed, tampered, object()):
        _assert_sanitized_writer_error(value, tmp_path / f"invalid-{id(value)}")

    parquet_writer.write_canonical_dataset_parquet(batch, output_root=tmp_path / "valid")
    assert batch.model_dump(mode="python") == original


def test_uses_hive_partition_path_and_private_modes(tmp_path: Path) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "output")

    assert publication.partition_relative_path == Path(
        "source_provider=synthetic",
        "source_feed=market",
        "market_domain=us_equities",
        "event_type=minute_bar",
        "market_date=2026-07-17",
        "canonical_event_schema_version=1",
        f"dataset_id={publication.dataset_id}",
    )
    assert publication.dataset_directory == tmp_path / "output" / publication.partition_relative_path
    assert {path.name for path in publication.dataset_directory.iterdir()} == {
        "dataset_manifest.json",
        "events.parquet",
    }
    for directory in (tmp_path / "output", *publication.dataset_directory.parents):
        if directory == tmp_path:
            break
        metadata = directory.lstat()
        assert stat.S_ISDIR(metadata.st_mode)
        assert metadata.st_uid == os.getuid()
        assert stat.S_IMODE(metadata.st_mode) == 0o700
    for path in publication.dataset_directory.iterdir():
        metadata = path.lstat()
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_uid == os.getuid()
        assert stat.S_IMODE(metadata.st_mode) == 0o600


def test_syncs_every_parent_after_creating_output_and_hive_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_fsync = parquet_writer.os.fsync
    synced_identities: set[tuple[int, int]] = set()

    def record_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        synced_identities.add((metadata.st_dev, metadata.st_ino))
        original_fsync(descriptor)

    monkeypatch.setattr(parquet_writer.os, "fsync", record_fsync)
    output_root = tmp_path / "output"
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=output_root)
    creation_parents = [tmp_path]
    directory = output_root
    for component in publication.partition_relative_path.parts[:-1]:
        creation_parents.append(directory)
        directory = directory / component

    assert {
        (directory.stat().st_dev, directory.stat().st_ino)
        for directory in creation_parents
    } <= synced_identities


def test_fails_closed_when_output_root_creation_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    original_fsync = parquet_writer.os.fsync

    def fail_root_parent_sync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == parent_identity:
            raise OSError("private root creation sync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(parquet_writer.os, "fsync", fail_root_parent_sync)
    output_root = tmp_path / "output"

    _assert_sanitized_writer_error(_batch(), output_root)

    assert output_root.is_dir()
    assert not tuple(output_root.rglob(".canonical-parquet-stage-*"))
    assert not tuple(output_root.rglob("dataset_id=*"))


def test_fails_closed_when_intermediate_directory_creation_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir(mode=0o700)
    output_root.chmod(0o700)
    root_identity = (output_root.stat().st_dev, output_root.stat().st_ino)
    original_fsync = parquet_writer.os.fsync

    def fail_intermediate_parent_sync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == root_identity:
            raise OSError("private intermediate creation sync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(parquet_writer.os, "fsync", fail_intermediate_parent_sync)

    _assert_sanitized_writer_error(_batch(), output_root)

    assert not tuple(output_root.rglob(".canonical-parquet-stage-*"))
    assert not tuple(output_root.rglob("dataset_id=*"))


def test_accepts_a_system_alias_ancestor_when_output_root_is_not_a_symlink(tmp_path: Path) -> None:
    canonical_tmp = tmp_path.resolve(strict=True)
    private_var = Path("/private/var")
    system_var = Path("/var")
    if not system_var.is_symlink() or not canonical_tmp.is_relative_to(private_var):
        pytest.skip("requires the macOS /var system alias")
    aliased_root = system_var / canonical_tmp.relative_to(private_var) / "aliased-output"

    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=aliased_root)

    assert publication.dataset_directory.is_dir()
    assert not publication.dataset_directory.is_symlink()


def test_fails_closed_on_an_existing_final_dataset(tmp_path: Path) -> None:
    batch = _batch()
    publication = parquet_writer.write_canonical_dataset_parquet(batch, output_root=tmp_path / "output")

    _assert_sanitized_writer_error(batch, tmp_path / "output")
    assert {path.name for path in publication.dataset_directory.iterdir()} == {
        "dataset_manifest.json",
        "events.parquet",
    }


def test_cleans_staging_after_pre_publish_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_parquet(*_args: object, **_kwargs: object) -> None:
        raise OSError("private failure that must not escape")

    monkeypatch.setattr(parquet_writer, "_write_parquet_file", fail_parquet)
    root = tmp_path / "output"

    _assert_sanitized_writer_error(_batch(), root)

    assert not tuple(root.rglob(".canonical-parquet-stage-*"))
    assert not tuple(root.rglob("dataset_id=*"))


def test_keeps_the_completed_final_directory_after_parent_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_fsync = parquet_writer.os.fsync
    final_parent_identity: tuple[int, int] | None = None
    original_publish = parquet_writer._rename_directory_exclusively

    def capture_final_parent(stage_path: Path, output_path: Path) -> None:
        nonlocal final_parent_identity
        original_publish(stage_path, output_path)
        metadata = output_path.parent.stat()
        final_parent_identity = metadata.st_dev, metadata.st_ino

    def fail_parent_sync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if final_parent_identity == (metadata.st_dev, metadata.st_ino):
            raise OSError("post-publish private failure")
        original_fsync(descriptor)

    monkeypatch.setattr(parquet_writer, "_rename_directory_exclusively", capture_final_parent)
    monkeypatch.setattr(parquet_writer.os, "fsync", fail_parent_sync)
    root = tmp_path / "output"

    _assert_sanitized_writer_error(_batch(), root)

    final_directories = tuple(root.rglob("dataset_id=*"))
    assert len(final_directories) == 1
    assert {path.name for path in final_directories[0].iterdir()} == {
        "dataset_manifest.json",
        "events.parquet",
    }
    assert not tuple(root.rglob(".canonical-parquet-stage-*"))


@pytest.mark.parametrize(
    ("platform", "expected_helper"),
    (
        ("darwin", "_rename_directory_darwin"),
        ("linux", "_rename_directory_linux"),
    ),
)
def test_dispatches_exclusive_rename_to_the_platform_native_boundary(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    expected_helper: str,
) -> None:
    calls: list[str] = []

    def record_call(stage_path: Path, output_path: Path) -> None:
        assert stage_path == Path("/stage")
        assert output_path == Path("/output")
        calls.append(expected_helper)

    monkeypatch.setattr(parquet_writer.sys, "platform", platform)
    for helper in (
        "_rename_directory_darwin",
        "_rename_directory_linux",
    ):
        monkeypatch.setattr(parquet_writer, helper, record_call, raising=False)

    parquet_writer._rename_directory_exclusively(Path("/stage"), Path("/output"))

    assert calls == [expected_helper]


@pytest.mark.parametrize("platform", ("win32", "freebsd13"))
def test_exclusive_rename_fails_closed_without_a_windows_fallback(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    def unexpected_windows_fallback(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Windows rename fallback must not be called")

    monkeypatch.setattr(parquet_writer.sys, "platform", platform)
    monkeypatch.setattr(
        parquet_writer,
        "_rename_directory_windows",
        unexpected_windows_fallback,
        raising=False,
    )

    with pytest.raises(OSError):
        parquet_writer._rename_directory_exclusively(Path("/stage"), Path("/output"))


@pytest.mark.parametrize("mode", (0o750, 0o755))
def test_rejects_symlinked_or_unsafe_output_root(tmp_path: Path, mode: int) -> None:
    unsafe_root = tmp_path / "unsafe"
    unsafe_root.mkdir(mode=0o700)
    unsafe_root.chmod(mode)
    actual_root = tmp_path / "actual"
    actual_root.mkdir(mode=0o700)
    actual_root.chmod(0o700)
    symlink_root = tmp_path / "symlink"
    symlink_root.symlink_to(actual_root, target_is_directory=True)

    _assert_sanitized_writer_error(_batch(), unsafe_root)
    _assert_sanitized_writer_error(_batch(), symlink_root)
    assert not tuple(actual_root.iterdir())


def test_has_no_network_credential_or_broker_import_side_effects() -> None:
    source = Path(parquet_writer.__file__).read_text(encoding="utf-8")
    spec = importlib.util.find_spec("trading_agent.canonical_parquet_writer")

    assert spec is not None
    assert "httpx" not in source
    assert "socket" not in source
    assert "alpaca" not in source
    assert "credential" not in source
    assert "broker" not in source
    assert "requests" not in source
    assert "trading_agent.alpaca" not in sys.modules


class _BatchSubclass(CanonicalDatasetBatch):
    pass


def _batch() -> CanonicalDatasetBatch:
    receipt = RawReceipt.from_payload(
        receipt_id="a" * 64,
        source_id="synthetic.market",
        market_date=MARKET_DATE,
        received_at=RECEIVED_AT,
        payload_sha256=hashlib.sha256(RAW_SECRET).hexdigest(),
        payload=RawReceiptPayload(RAW_SECRET),
    )
    manifest = project_raw_receipt_partition(
        (receipt,),
        source_id="synthetic.market",
        market_date=MARKET_DATE,
        parent_ledger_generation=3,
    )
    return CanonicalDatasetBatch(
        partition=CanonicalDatasetPartition(
            source_id=DataSourceId(provider="synthetic", feed="market"),
            market_domain=DataMarketDomain.US_EQUITIES,
            event_type="minute_bar",
            market_date=MARKET_DATE,
        ),
        raw_manifest=manifest,
        events=(
            CanonicalEventEnvelope(
                event_id="event-0001",
                source_id=DataSourceId(provider="synthetic", feed="market"),
                entity_refs=(
                    CanonicalEntityRef(
                        entity_type=CanonicalEntityType.INSTRUMENT,
                        entity_id="us-eq-fixture-0001",
                    ),
                ),
                event_type="minute_bar",
                event_time=RECEIVED_AT,
                received_at=RECEIVED_AT,
                normalized_at=RECEIVED_AT + dt.timedelta(seconds=1),
                sequence_or_offset="42",
                operation=CanonicalEventOperation.ORIGINAL,
                raw_receipt_ref=manifest.receipts[0].receipt_id,
                content_hash="c" * 64,
                quality_flags=("complete",),
            ),
        ),
    )


def _assert_sanitized_writer_error(value: object, output_root: Path) -> None:
    with pytest.raises(parquet_writer.CanonicalDatasetParquetWriterError) as captured:
        parquet_writer.write_canonical_dataset_parquet(value, output_root=output_root)  # type: ignore[arg-type]

    assert str(captured.value) == "canonical dataset parquet output could not be written"
    assert repr(captured.value) == "CanonicalDatasetParquetWriterError()"
    assert RAW_SECRET.decode() not in str(captured.value)
    assert RAW_SECRET.decode() not in repr(captured.value)
    assert ACCOUNT_SECRET not in str(captured.value)
    assert ACCOUNT_SECRET not in repr(captured.value)


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
