from __future__ import annotations

import ast
import datetime as dt
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import sys
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import trading_agent.canonical_duckdb_replay as replay_module
import trading_agent.canonical_parquet_writer as parquet_writer
from trading_agent.canonical_dataset_models import CanonicalDatasetBatch, CanonicalDatasetPartition
from trading_agent.canonical_duckdb_replay import (
    CanonicalDatasetReplay,
    CanonicalDatasetReplayError,
    replay_canonical_dataset,
)
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
RAW_SECRET = b"m3.4-private-raw-payload"
ACCOUNT_SECRET = "m3.4-private-account-id"


def test_exposes_replay_module() -> None:
    assert importlib.util.find_spec("trading_agent.canonical_duckdb_replay") is not None


def test_exposes_sanitized_replay_api() -> None:
    module = importlib.import_module("trading_agent.canonical_duckdb_replay")

    assert hasattr(module, "replay_canonical_dataset")
    assert hasattr(module, "CanonicalDatasetReplay")
    assert hasattr(module, "CanonicalDatasetReplayError")


def test_replays_writer_dataset_deterministically_with_nested_refs_and_utc_hash(tmp_path: Path) -> None:
    batch = _batch()
    original_batch = batch.model_dump(mode="python")
    publication = parquet_writer.write_canonical_dataset_parquet(
        batch,
        output_root=tmp_path / f"{ACCOUNT_SECRET}-' ; SELECT 1 --",
    )

    first = replay_canonical_dataset(publication.dataset_directory)
    second = replay_canonical_dataset(publication.dataset_directory)

    expected_rows = [
        {
            "schema_version": 1,
            "event_id": "event-0001",
            "source_provider": "synthetic",
            "source_feed": "market",
            "provider_event_id": "provider-event-0001",
            "entity_refs": [
                {"entity_type": "instrument", "entity_id": "us-eq-fixture-0001"},
                {"entity_type": "organization", "entity_id": "issuer-fixture-0001"},
            ],
            "event_type": "minute_bar",
            "event_time": "2026-07-17T00:30:00Z",
            "published_at": None,
            "provider_time": None,
            "received_at": "2026-07-17T00:30:00Z",
            "normalized_at": "2026-07-17T00:30:01Z",
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

    assert first == second
    assert first.dataset_id == publication.dataset_id
    assert first.event_count == 1
    assert first.canonical_event_content_sha256 == publication.canonical_event_content_sha256
    assert first.canonical_event_content_sha256 == _sha256(_canonical_json_bytes(expected_rows))
    assert first.parquet_sha256 == publication.parquet_sha256
    assert first.raw_manifest_id == batch.raw_manifest.manifest_id
    assert first.raw_manifest_content_sha256 == batch.raw_manifest.content_sha256
    sidecar_bytes = (publication.dataset_directory / "dataset_manifest.json").read_bytes()
    sidecar = json.loads(sidecar_bytes)
    assert sidecar_bytes == _canonical_json_bytes(sidecar) + b"\n"
    assert set(sidecar) == {
        "canonical_event_content_sha256",
        "dataset_id",
        "event_count",
        "parquet_sha256",
        "partition",
        "raw_manifest_content_sha256",
        "raw_manifest_id",
        "schema_version",
    }
    assert tuple(field.name for field in fields(CanonicalDatasetReplay)) == (
        "dataset_id",
        "event_count",
        "canonical_event_content_sha256",
        "parquet_sha256",
        "raw_manifest_id",
        "raw_manifest_content_sha256",
    )
    assert not hasattr(first, "dataset_directory")
    assert RAW_SECRET.decode() not in repr(first)
    assert ACCOUNT_SECRET not in repr(first)
    with pytest.raises(FrozenInstanceError):
        first.event_count = 2  # type: ignore[misc]
    assert batch.model_dump(mode="python") == original_batch


def test_rejects_parquet_field_schema_metadata_even_when_hash_is_updated(tmp_path: Path) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "output")
    parquet_path = publication.dataset_directory / "events.parquet"
    table = pq.read_table(parquet_path)
    fields_with_metadata = list(table.schema)
    fields_with_metadata[1] = fields_with_metadata[1].with_metadata({b"not_allowed": b"metadata"})
    pq.write_table(table.cast(pa.schema(fields_with_metadata)), parquet_path)
    parquet_path.chmod(0o600)
    _update_parquet_sha256(publication.dataset_directory)

    _assert_sanitized_replay_error(publication.dataset_directory)


@pytest.mark.parametrize("mutation", ("append", "truncate"))
def test_rejects_modified_or_truncated_parquet_before_duckdb_replay(tmp_path: Path, mutation: str) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "output")
    parquet_path = publication.dataset_directory / "events.parquet"
    parquet_bytes = parquet_path.read_bytes()
    mutated_bytes = parquet_bytes + b"modified" if mutation == "append" else parquet_bytes[: len(parquet_bytes) // 2]
    parquet_path.write_bytes(mutated_bytes)
    parquet_path.chmod(0o600)

    _assert_sanitized_replay_error(publication.dataset_directory)


def test_rejects_modified_sidecar_hash_and_noncanonical_sidecar_bytes(tmp_path: Path) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "hash-output")
    sidecar_path = publication.dataset_directory / "dataset_manifest.json"
    sidecar = _read_sidecar(publication.dataset_directory)
    sidecar["parquet_sha256"] = "0" * 64
    _write_sidecar(sidecar_path, sidecar)

    _assert_sanitized_replay_error(publication.dataset_directory)

    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "json-output")
    sidecar_path = publication.dataset_directory / "dataset_manifest.json"
    sidecar_path.write_text(json.dumps(_read_sidecar(publication.dataset_directory), indent=2), encoding="ascii")
    sidecar_path.chmod(0o600)

    _assert_sanitized_replay_error(publication.dataset_directory)


def test_rejects_parquet_schema_with_an_extra_column_even_when_hash_is_updated(tmp_path: Path) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "output")
    parquet_path = publication.dataset_directory / "events.parquet"
    table = pq.read_table(parquet_path)
    pq.write_table(table.append_column("unexpected", pa.array([1], type=pa.int8())), parquet_path)
    parquet_path.chmod(0o600)
    _update_parquet_sha256(publication.dataset_directory)

    _assert_sanitized_replay_error(publication.dataset_directory)


@pytest.mark.parametrize("file_name", ("events.parquet", "dataset_manifest.json"))
def test_rejects_non_private_file_modes(tmp_path: Path, file_name: str) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / file_name)
    (publication.dataset_directory / file_name).chmod(0o640)

    _assert_sanitized_replay_error(publication.dataset_directory)


def test_rejects_non_private_hive_mode_extra_file_and_partition_path(tmp_path: Path) -> None:
    mode_publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "mode-output")
    mode_publication.dataset_directory.parent.chmod(0o755)
    _assert_sanitized_replay_error(mode_publication.dataset_directory)

    extra_publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "extra-output")
    extra_path = extra_publication.dataset_directory / "unexpected"
    extra_path.write_bytes(b"unexpected")
    extra_path.chmod(0o600)
    _assert_sanitized_replay_error(extra_publication.dataset_directory)

    path_publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "path-output")
    invalid_path = path_publication.dataset_directory.with_name(f"dataset_id={'0' * 64}")
    path_publication.dataset_directory.rename(invalid_path)
    _assert_sanitized_replay_error(invalid_path)


def test_rejects_non_private_output_root(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=output_root)
    output_root.chmod(0o777)

    _assert_sanitized_replay_error(publication.dataset_directory)


def test_rejects_symlinked_output_root(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=output_root)
    moved_output_root = tmp_path / "moved-output"
    output_root.rename(moved_output_root)
    output_root.symlink_to(moved_output_root, target_is_directory=True)

    _assert_sanitized_replay_error(publication.dataset_directory)


def test_rejects_a_symlinked_hive_partition_ancestor(tmp_path: Path) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "output")
    source_provider_directory = publication.dataset_directory.parents[5]
    target_directory = source_provider_directory.with_name("moved-source-provider")
    source_provider_directory.rename(target_directory)
    source_provider_directory.symlink_to(target_directory, target_is_directory=True)

    _assert_sanitized_replay_error(publication.dataset_directory)


def test_rejects_duplicate_event_ids_even_with_self_consistent_hashes(tmp_path: Path) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(
        _batch(event_ids=("event-0001", "event-0002")),
        output_root=tmp_path / "output",
    )
    parquet_path = publication.dataset_directory / "events.parquet"
    duplicate_table = pq.read_table(parquet_path).take(pa.array([0, 0], type=pa.int64()))
    pq.write_table(duplicate_table, parquet_path)
    parquet_path.chmod(0o600)
    duplicate_directory = _rewrite_sidecar_for_table(publication.dataset_directory, duplicate_table)

    _assert_sanitized_replay_error(duplicate_directory)


def test_rejects_self_consistent_invalid_physical_event_rows(tmp_path: Path) -> None:
    mutations = (
        ("schema-version", "schema_version", pa.array([2], type=pa.int8())),
        ("event-id", "event_id", pa.array(["invalid\nevent-id"], type=pa.string())),
        ("operation", "operation", pa.array(["correction"], type=pa.string())),
        (
            "quality-flags",
            "quality_flags",
            pa.array([["complete", "complete"]], type=pa.list_(pa.string())),
        ),
        ("source-provider", "source_provider", pa.array(["forged"], type=pa.string())),
        ("event-type", "event_type", pa.array(["forged_event"], type=pa.string())),
    )
    for name, column, values in mutations:
        publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / name)
        parquet_path = publication.dataset_directory / "events.parquet"
        tampered_table = _replace_event_column(pq.read_table(parquet_path), column, values)
        pq.write_table(tampered_table, parquet_path)
        parquet_path.chmod(0o600)
        tampered_directory = _rewrite_sidecar_for_table(publication.dataset_directory, tampered_table)

        _assert_sanitized_replay_error(tampered_directory)


def test_rejects_self_consistent_invalid_physical_entity_refs_and_timestamps(tmp_path: Path) -> None:
    entity_publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "entities")
    entity_parquet_path = entity_publication.dataset_directory / "events.parquet"
    entity_table = pq.read_table(entity_parquet_path)
    duplicate_entities = pa.array(
        [
            [
                {"entity_type": "instrument", "entity_id": "us-eq-fixture-0001"},
                {"entity_type": "instrument", "entity_id": "us-eq-fixture-0001"},
            ]
        ],
        type=entity_table.schema.field("entity_refs").type,
    )
    entity_table = _replace_event_column(entity_table, "entity_refs", duplicate_entities)
    pq.write_table(entity_table, entity_parquet_path)
    entity_parquet_path.chmod(0o600)
    entity_directory = _rewrite_sidecar_for_table(entity_publication.dataset_directory, entity_table)
    _assert_sanitized_replay_error(entity_directory)

    timestamp_publication = parquet_writer.write_canonical_dataset_parquet(
        _batch(),
        output_root=tmp_path / "timestamps",
    )
    timestamp_parquet_path = timestamp_publication.dataset_directory / "events.parquet"
    timestamp_table = pq.read_table(timestamp_parquet_path)
    reverse_normalized_at = pa.array(
        [dt.datetime(2026, 7, 17, 0, 29, 59, tzinfo=dt.UTC)],
        type=timestamp_table.schema.field("normalized_at").type,
    )
    timestamp_table = _replace_event_column(timestamp_table, "normalized_at", reverse_normalized_at)
    pq.write_table(timestamp_table, timestamp_parquet_path)
    timestamp_parquet_path.chmod(0o600)
    timestamp_directory = _rewrite_sidecar_for_table(timestamp_publication.dataset_directory, timestamp_table)
    _assert_sanitized_replay_error(timestamp_directory)


def test_replay_queries_the_verified_parquet_inode_after_named_file_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "output")
    parquet_path = publication.dataset_directory / "events.parquet"
    replacement_path = tmp_path / "replacement.parquet"
    replacement_table = _replace_event_column(
        pq.read_table(parquet_path),
        "content_hash",
        pa.array(["d" * 64], type=pa.string()),
    )
    pq.write_table(replacement_table, replacement_path)
    replacement_path.chmod(0o600)
    original_query = replay_module._canonical_rows_from_duckdb

    def swap_after_verification(verified_parquet: str) -> list[dict[str, object]]:
        os.replace(replacement_path, parquet_path)
        return original_query(verified_parquet)

    monkeypatch.setattr(replay_module, "_canonical_rows_from_duckdb", swap_after_verification)

    replay = replay_canonical_dataset(publication.dataset_directory)

    assert replay.canonical_event_content_sha256 == publication.canonical_event_content_sha256
    assert _sha256(parquet_path.read_bytes()) != publication.parquet_sha256


def test_fails_closed_when_a_stable_fd_path_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "output")

    def unavailable_fd_path(_descriptor: int) -> str:
        raise OSError

    monkeypatch.setattr(replay_module, "_stable_fd_path", unavailable_fd_path)

    _assert_sanitized_replay_error(publication.dataset_directory)


def test_rejects_non_path_input_and_never_exposes_raw_or_account_secrets(tmp_path: Path) -> None:
    publication = parquet_writer.write_canonical_dataset_parquet(
        _batch(),
        output_root=tmp_path / ACCOUNT_SECRET,
    )
    sidecar_path = publication.dataset_directory / "dataset_manifest.json"
    sidecar = _read_sidecar(publication.dataset_directory)
    sidecar["raw_payload"] = RAW_SECRET.decode()
    sidecar["account_id"] = ACCOUNT_SECRET
    sidecar["request_key"] = ACCOUNT_SECRET
    sidecar["receipts"] = []
    _write_sidecar(sidecar_path, sidecar)

    _assert_sanitized_replay_error(publication.dataset_directory)
    _assert_sanitized_replay_error("not-a-path")  # type: ignore[arg-type]


def test_uses_only_local_in_memory_duckdb_and_no_operational_imports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("trading_agent.canonical_duckdb_replay")
    source = inspect.getsource(module)
    imported_modules = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    blocked_import_fragments = (
        "alpaca",
        "broker",
        "credential",
        "httpx",
        "kis",
        "order",
        "requests",
        "socket",
        "urllib",
        "websocket",
    )
    duckdb_home = tmp_path / "duckdb-home"
    duckdb_home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", os.fspath(duckdb_home))
    monkeypatch.setenv("DUCKDB_EXTENSION_DIRECTORY", os.fspath(duckdb_home / "extensions"))
    before = tuple(duckdb_home.rglob("*"))
    publication = parquet_writer.write_canonical_dataset_parquet(_batch(), output_root=tmp_path / "output")

    replay_canonical_dataset(publication.dataset_directory)

    assert "read_parquet(?, hive_partitioning = false)" in source
    assert "hive_partitioning = false" in source
    assert all(
        all(fragment not in imported_module for fragment in blocked_import_fragments)
        for imported_module in imported_modules
    )
    assert "trading_agent.alpaca" not in sys.modules
    assert tuple(duckdb_home.rglob("*")) == before


def _batch(*, event_ids: tuple[str, ...] = ("event-0001",)) -> CanonicalDatasetBatch:
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
        events=tuple(
            CanonicalEventEnvelope(
                event_id=event_id,
                source_id=DataSourceId(provider="synthetic", feed="market"),
                provider_event_id=f"provider-event-{index + 1:04}",
                entity_refs=(
                    CanonicalEntityRef(
                        entity_type=CanonicalEntityType.INSTRUMENT,
                        entity_id="us-eq-fixture-0001",
                    ),
                    CanonicalEntityRef(
                        entity_type=CanonicalEntityType.ORGANIZATION,
                        entity_id="issuer-fixture-0001",
                    ),
                ),
                event_type="minute_bar",
                event_time=RECEIVED_AT,
                received_at=RECEIVED_AT,
                normalized_at=RECEIVED_AT + dt.timedelta(seconds=index + 1),
                sequence_or_offset="42",
                operation=CanonicalEventOperation.ORIGINAL,
                raw_receipt_ref=manifest.receipts[0].receipt_id,
                content_hash="c" * 64,
                quality_flags=("complete",),
            )
            for index, event_id in enumerate(event_ids)
        ),
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _update_parquet_sha256(dataset_directory: Path) -> None:
    sidecar = _read_sidecar(dataset_directory)
    sidecar["parquet_sha256"] = _sha256((dataset_directory / "events.parquet").read_bytes())
    _write_sidecar(dataset_directory / "dataset_manifest.json", sidecar)


def _replace_event_column(table: pa.Table, column: str, values: pa.Array) -> pa.Table:
    return table.set_column(table.schema.get_field_index(column), table.schema.field(column), values)


def _rewrite_sidecar_for_table(dataset_directory: Path, table: pa.Table) -> Path:
    sidecar = _read_sidecar(dataset_directory)
    canonical_rows = _json_compatible(table.to_pylist())
    assert type(canonical_rows) is list
    sidecar["event_count"] = len(canonical_rows)
    sidecar["canonical_event_content_sha256"] = _sha256(_canonical_json_bytes(canonical_rows))
    sidecar["parquet_sha256"] = _sha256((dataset_directory / "events.parquet").read_bytes())
    sidecar["dataset_id"] = _sha256(
        _canonical_json_bytes(
            {
                "canonical_event_rows": canonical_rows,
                "partition": sidecar["partition"],
                "raw_manifest": {
                    "content_sha256": sidecar["raw_manifest_content_sha256"],
                    "manifest_id": sidecar["raw_manifest_id"],
                },
            }
        )
    )
    rewritten_directory = dataset_directory.with_name(f"dataset_id={sidecar['dataset_id']}")
    dataset_directory.rename(rewritten_directory)
    _write_sidecar(rewritten_directory / "dataset_manifest.json", sidecar)
    return rewritten_directory


def _read_sidecar(dataset_directory: Path) -> dict[str, object]:
    return json.loads((dataset_directory / "dataset_manifest.json").read_text(encoding="ascii"))


def _write_sidecar(path: Path, sidecar: dict[str, object]) -> None:
    path.write_bytes(_canonical_json_bytes(sidecar) + b"\n")
    path.chmod(0o600)


def _json_compatible(value: object) -> object:
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value


def _assert_sanitized_replay_error(dataset_directory: Path) -> None:
    with pytest.raises(CanonicalDatasetReplayError) as captured:
        replay_canonical_dataset(dataset_directory)

    assert str(captured.value) == "canonical dataset replay could not be verified"
    assert repr(captured.value) == "CanonicalDatasetReplayError()"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    for value in (
        str(captured.value),
        repr(captured.value),
        str(captured.value.__cause__),
        repr(captured.value.__cause__),
        str(captured.value.__context__),
        repr(captured.value.__context__),
    ):
        assert RAW_SECRET.decode() not in value
        assert ACCOUNT_SECRET not in value
