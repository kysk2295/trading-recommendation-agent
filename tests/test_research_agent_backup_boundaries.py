from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Literal, assert_never

import pytest

from tests.test_research_agent_backup import _reason, _request, stat_mode
from trading_agent.research_agent_backup import create_backup, verify_restore
from trading_agent.research_agent_backup_models import (
    BackupFailureReason,
    BackupLimits,
    RestoreRequest,
)


@pytest.mark.parametrize("shape", ["receipt_version", "receipt_semantic", "database_version", "database_semantic"])
def test_restore_rejects_asymmetric_manifest_artifact_fields(
    tmp_path: Path,
    shape: Literal["receipt_version", "receipt_semantic", "database_version", "database_semantic"],
) -> None:
    # Given: a canonical-looking manifest has one asymmetric optional field pair.
    request = _request(tmp_path)
    _ = create_backup(request)
    manifest_path = request.destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    receipt = next(item for item in manifest["artifacts"] if item["kind"] == "cycle_receipt")
    database = next(item for item in manifest["artifacts"] if item["kind"] == "cycle_database")
    match shape:
        case "receipt_version":
            receipt["user_version"] = 1
        case "receipt_semantic":
            receipt["semantic_sha256"] = "a" * 64
        case "database_version":
            database["semantic_sha256"] = None
        case "database_semantic":
            database["user_version"] = None
        case unreachable:
            assert_never(unreachable)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
    target = request.destination.parent / "malformed-target"

    # When: restore verification parses the malformed artifact shape.
    reason = _reason(lambda: verify_restore(RestoreRequest(request.destination, target, request.limits)))

    # Then: it fails closed without any target or success receipt.
    assert reason is BackupFailureReason.MANIFEST_INVALID
    assert not target.exists()
    assert not tuple(request.destination.parent.glob(".malformed-target.stage-*"))


@pytest.mark.parametrize("damage", ["corrupt", "truncated"])
def test_backup_rejects_damaged_source_database(tmp_path: Path, damage: Literal["corrupt", "truncated"]) -> None:
    # Given: the authoritative cycle database is already corrupt or truncated.
    request = _request(tmp_path)
    original = request.cycle_database.read_bytes()
    damaged = b"not-sqlite" if damage == "corrupt" else original[:100]
    request.cycle_database.write_bytes(damaged)
    source_sha256 = hashlib.sha256(damaged).hexdigest()

    # When: backup attempts a SQLite snapshot.
    reason = _reason(lambda: create_backup(request))

    # Then: it fails typed, preserves the damaged authority, and publishes nothing.
    assert reason in {BackupFailureReason.SOURCE_INVALID, BackupFailureReason.SQLITE_INVALID}
    assert hashlib.sha256(request.cycle_database.read_bytes()).hexdigest() == source_sha256
    assert not request.destination.exists()
    assert not tuple(request.destination.parent.glob(".bundle.stage-*"))


def test_restore_rejects_existing_target_without_touching_it(tmp_path: Path) -> None:
    # Given: a valid bundle and an existing target containing authoritative user data.
    request = _request(tmp_path)
    _ = create_backup(request)
    target = request.destination.parent / "existing-restore"
    target.mkdir(mode=0o700)
    sentinel = target / "keep.txt"
    sentinel.write_text("keep\n")
    sentinel.chmod(0o600)
    bundle_sha256 = hashlib.sha256((request.destination / "manifest.json").read_bytes()).hexdigest()

    # When: verify-restore is pointed at the existing target.
    reason = _reason(lambda: verify_restore(RestoreRequest(request.destination, target, request.limits)))

    # Then: it rejects the target without mutation, staging, or success receipt.
    assert reason is BackupFailureReason.DESTINATION_EXISTS
    assert sentinel.read_text() == "keep\n"
    assert hashlib.sha256((request.destination / "manifest.json").read_bytes()).hexdigest() == bundle_sha256
    assert not (target / "restore-verification.json").exists()
    assert not tuple(request.destination.parent.glob(".existing-restore.stage-*"))


@pytest.mark.parametrize("budget", ["within", "over"])
def test_backup_includes_active_private_wal_without_source_drift(
    tmp_path: Path, budget: Literal["within", "over"]
) -> None:
    # Given: a committed row exists only in a private active WAL.
    request = _request(tmp_path)
    with closing(sqlite3.connect(request.cycle_database)) as writer:
        _ = writer.execute("PRAGMA journal_mode=WAL")
        _ = writer.execute("PRAGMA wal_autocheckpoint=0")
        _ = writer.execute(
            "INSERT INTO evidence(evidence_id,agent_family_id,available_at,payload_json) VALUES(?,?,?,?)",
            ("e-wal", "family", "2026-08-03T00:01:00+00:00", '{"value":2}'),
        )
        _ = writer.executemany(
            "INSERT INTO evidence(evidence_id,agent_family_id,available_at,payload_json) VALUES(?,?,?,?)",
            tuple((f"filler-{index}", "family", "2026-08-03T00:01:00+00:00", "x" * 1024) for index in range(128)),
        )
        writer.commit()
        sidecars = tuple(Path(f"{request.cycle_database}{suffix}") for suffix in ("", "-wal", "-shm"))
        for path in sidecars:
            path.chmod(0o600)
        immutable_uri = f"{request.cycle_database.resolve().as_uri()}?mode=ro&immutable=1"
        with closing(sqlite3.connect(immutable_uri, uri=True)) as main_only:
            assert main_only.execute("SELECT COUNT(*) FROM evidence WHERE evidence_id='e-wal'").fetchone() == (0,)
        before = tuple((path.stat(), path.read_bytes()) for path in sidecars)
        match budget:
            case "over":
                copied_bytes = sum(path.stat().st_size for path in sidecars[:2])
                request = replace(request, limits=BackupLimits(max_files=10, max_bytes=copied_bytes - 1))
            case "within":
                pass
            case unreachable:
                assert_never(unreachable)

        # When: the active store is backed up.
        match budget:
            case "over":
                result = _reason(lambda: create_backup(request))
            case "within":
                result = create_backup(request)
            case unreachable:
                assert_never(unreachable)

        # Then: the WAL row is captured and every source file remains byte/identity stable.
        match budget:
            case "over":
                assert result is BackupFailureReason.LIMIT_EXCEEDED
                assert not request.destination.exists()
                assert not tuple(request.destination.parent.glob(".bundle.stage-*"))
            case "within":
                with closing(sqlite3.connect(request.destination / "databases/cycle.sqlite3")) as snapshot:
                    assert snapshot.execute("SELECT COUNT(*) FROM evidence WHERE evidence_id='e-wal'").fetchone() == (
                        1,
                    )
            case unreachable:
                assert_never(unreachable)
        after = tuple((path.stat(), path.read_bytes()) for path in sidecars)
        assert after == before
        assert all(stat_mode(path) == 0o600 for path in sidecars)
