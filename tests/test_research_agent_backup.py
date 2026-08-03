from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Literal, assert_never

import pytest

from trading_agent.hermes_delivery_schema import prepare_hermes_delivery_schema
from trading_agent.research_agent_backup import create_backup, verify_restore
from trading_agent.research_agent_backup_models import (
    BackupError,
    BackupFailureReason,
    BackupLimits,
    BackupRequest,
    BackupResult,
    RestoreRequest,
)
from trading_agent.research_agent_cycle_schema import (
    RESEARCH_AGENT_CYCLE_SCHEMA,
    RESEARCH_AGENT_CYCLE_SCHEMA_VERSION,
)


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _databases(root: Path) -> tuple[Path, Path]:
    cycle = root / "cycle.sqlite3"
    with closing(sqlite3.connect(cycle)) as connection:
        for statement in RESEARCH_AGENT_CYCLE_SCHEMA:
            _ = connection.execute(statement)
        _ = connection.execute(f"PRAGMA user_version={RESEARCH_AGENT_CYCLE_SCHEMA_VERSION}")
        _ = connection.execute(
            "INSERT INTO evidence(evidence_id,agent_family_id,available_at,payload_json) VALUES(?,?,?,?)",
            ("e-1", "family", "2026-08-03T00:00:00+00:00", '{"value":1}'),
        )
        connection.commit()
    hermes = root / "hermes.sqlite3"
    with closing(sqlite3.connect(hermes)) as connection:
        prepare_hermes_delivery_schema(connection)
        _ = connection.execute(
            "INSERT INTO hermes_delivery_events VALUES(?,?,?,?,?)",
            ("d-1", "d-1", "2026-08-03T00:00:00+00:00", 2, '{"delivery_id":"d-1"}'),
        )
        connection.commit()
    cycle.chmod(0o600)
    hermes.chmod(0o600)
    return cycle, hermes


def _request(tmp_path: Path, *, max_files: int = 10, max_bytes: int = 2_000_000) -> BackupRequest:
    source = _private_dir(tmp_path / "source")
    cycle, hermes = _databases(source)
    cycle_receipts = _private_dir(source / "cycle-receipts")
    hermes_receipts = _private_dir(source / "hermes-receipts")
    (cycle_receipts / "one.json").write_text('{"cycle":1}\n', encoding="utf-8")
    (hermes_receipts / "two.json").write_text('{"hermes":1}\n', encoding="utf-8")
    (cycle_receipts / "one.json").chmod(0o600)
    (hermes_receipts / "two.json").chmod(0o600)
    destination_parent = _private_dir(tmp_path / "destination")
    return BackupRequest(
        cycle_database=cycle,
        hermes_database=hermes,
        cycle_receipts=cycle_receipts,
        hermes_receipts=hermes_receipts,
        destination=destination_parent / "bundle",
        limits=BackupLimits(max_files=max_files, max_bytes=max_bytes),
    )


def _reason(action: Callable[[], BackupResult]) -> BackupFailureReason:
    with pytest.raises(BackupError) as captured:
        action()
    return captured.value.reason


def test_backup_restore_preserves_semantics_and_private_publication(tmp_path: Path) -> None:
    # Given: two valid private SQLite stores and two private receipt roots.
    request = _request(tmp_path)
    source_states = tuple((path.stat(), hashlib.sha256(path.read_bytes()).hexdigest()) for path in request.databases)

    # When: the bounded backup is created and verified into a new restore target.
    backup_result = create_backup(request)
    restore_target = request.destination.parent / "restored"
    restore_result = verify_restore(RestoreRequest(request.destination, restore_target, request.limits))

    # Then: publication is private, semantic digests match, and sources were not mutated.
    manifest = json.loads((request.destination / "manifest.json").read_text())
    assert backup_result.semantic_digests == restore_result.semantic_digests
    restored_source_states = tuple(
        (path.stat(), hashlib.sha256(path.read_bytes()).hexdigest()) for path in request.databases
    )
    assert restored_source_states == source_states
    assert stat_mode(request.destination) == 0o700
    assert stat_mode(request.destination / "manifest.json") == 0o600
    assert stat_mode(restore_target / "restore-verification.json") == 0o600
    assert str(request.cycle_database) not in json.dumps(manifest)
    assert restore_result.provider_calls == restore_result.model_calls == 0
    assert restore_result.heavy_processes == restore_result.broker_mutation == 0


@pytest.mark.parametrize("mutation", ["content", "hash_swap", "truncate"])
def test_restore_rejects_corrupt_or_swapped_artifacts(
    tmp_path: Path, mutation: Literal["content", "hash_swap", "truncate"]
) -> None:
    # Given: a valid bundle with a corrupt artifact or manifest hash mapping.
    request = _request(tmp_path)
    _ = create_backup(request)
    manifest_path = request.destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    match mutation:
        case "hash_swap":
            manifest["artifacts"][0]["sha256"], manifest["artifacts"][1]["sha256"] = (
                manifest["artifacts"][1]["sha256"],
                manifest["artifacts"][0]["sha256"],
            )
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
            manifest_path.chmod(0o600)
        case "content" | "truncate":
            database = request.destination / "databases/cycle.sqlite3"
            payload = b"broken" if mutation == "content" else database.read_bytes()[:100]
            database.write_bytes(payload)
            database.chmod(0o600)
        case unreachable:
            assert_never(unreachable)

    # When: restore verification is attempted.
    target = request.destination.parent / "rejected"
    reason = _reason(lambda: verify_restore(RestoreRequest(request.destination, target, request.limits)))

    # Then: it fails closed without publishing a target.
    assert reason in {BackupFailureReason.MANIFEST_INVALID, BackupFailureReason.ARTIFACT_INVALID}
    assert not target.exists()


@pytest.mark.parametrize("defect", ["version", "public", "symlink", "hardlink"])
def test_backup_rejects_wrong_schema_or_unsafe_receipt(
    tmp_path: Path, defect: Literal["version", "public", "symlink", "hardlink"]
) -> None:
    # Given: one source violates a schema or private-file invariant.
    request = _request(tmp_path)
    receipt = request.cycle_receipts / "one.json"
    match defect:
        case "version":
            with sqlite3.connect(request.cycle_database) as connection:
                _ = connection.execute("PRAGMA user_version=99")
        case "public":
            receipt.chmod(0o644)
        case "symlink":
            receipt.unlink()
            receipt.symlink_to(request.hermes_receipts / "two.json")
        case "hardlink":
            os.link(receipt, request.cycle_receipts / "linked.json")
        case unreachable:
            assert_never(unreachable)

    # When: backup is attempted.
    reason = _reason(lambda: create_backup(request))

    # Then: it fails closed and cleans its unique staging directory.
    assert reason in {BackupFailureReason.SCHEMA_INVALID, BackupFailureReason.RECEIPT_INVALID}
    assert not request.destination.exists()
    assert not tuple(request.destination.parent.glob(".bundle.stage-*"))


@pytest.mark.parametrize("constraint", ["files", "entries", "bytes", "existing"])
def test_backup_rejects_bounds_and_existing_destination(
    tmp_path: Path, constraint: Literal["files", "entries", "bytes", "existing"]
) -> None:
    # Given: an over-limit request or existing destination.
    match constraint:
        case "files":
            request = _request(tmp_path, max_files=3)
            expected = BackupFailureReason.LIMIT_EXCEEDED
        case "entries":
            request = _request(tmp_path, max_files=4)
            for index in range(5):
                _private_dir(request.cycle_receipts / f"nested-{index}")
            expected = BackupFailureReason.LIMIT_EXCEEDED
        case "bytes":
            request = _request(tmp_path, max_bytes=10)
            expected = BackupFailureReason.LIMIT_EXCEEDED
        case "existing":
            request = _request(tmp_path)
            request.destination.mkdir(mode=0o700)
            expected = BackupFailureReason.DESTINATION_EXISTS
        case unreachable:
            assert_never(unreachable)

    # When: backup is attempted.
    reason = _reason(lambda: create_backup(request))

    # Then: input is rejected without overwriting the destination.
    assert reason is expected
    assert request.destination.exists() is (constraint == "existing")


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
