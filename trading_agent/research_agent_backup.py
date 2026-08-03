from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.hermes_delivery_schema import (
    HERMES_DELIVERY_SCHEMA_VERSION,
    prepare_hermes_delivery_schema,
)
from trading_agent.private_query_bytes import read_private_bytes_query_only
from trading_agent.research_agent_backup_io import (
    ReceiptScan,
    ReceiptSource,
    clean_stage,
    copy_receipt,
    new_stage,
    publish,
    receipt_inventory,
    require_bundle_shape,
    require_inventory_unchanged,
    require_private_directory,
    snapshot_sqlite,
    write_private,
)
from trading_agent.research_agent_backup_models import (
    ArtifactKind,
    BackupError,
    BackupFailureReason,
    BackupLimits,
    BackupManifest,
    BackupRequest,
    BackupResult,
    ManifestArtifact,
    ManifestLimits,
    RestoreRequest,
)
from trading_agent.research_agent_cycle_schema import (
    RESEARCH_AGENT_CYCLE_SCHEMA,
    RESEARCH_AGENT_CYCLE_SCHEMA_VERSION,
)
from trading_agent.sqlite_uri import sqlite_read_only_uri


@dataclass(frozen=True, slots=True)
class _DatabaseSpec:
    source: Path
    relative: str
    kind: ArtifactKind
    version: int


def create_backup(request: BackupRequest) -> BackupResult:
    _require_limits(request.limits)
    destination, stage = new_stage(request.destination)
    try:
        specs = (
            _DatabaseSpec(
                request.cycle_database,
                "databases/cycle.sqlite3",
                ArtifactKind.CYCLE_DATABASE,
                RESEARCH_AGENT_CYCLE_SCHEMA_VERSION,
            ),
            _DatabaseSpec(
                request.hermes_database,
                "databases/hermes.sqlite3",
                ArtifactKind.HERMES_DATABASE,
                HERMES_DELIVERY_SCHEMA_VERSION,
            ),
        )
        entry_budget = request.limits.max_files - len(specs)
        cycle_inventory = receipt_inventory(
            request.cycle_receipts,
            ReceiptScan("receipts/cycle", ArtifactKind.CYCLE_RECEIPT, entry_budget),
        )
        entry_budget -= len(cycle_inventory.entries)
        hermes_inventory = receipt_inventory(
            request.hermes_receipts,
            ReceiptScan("receipts/hermes", ArtifactKind.HERMES_RECEIPT, entry_budget),
        )
        receipts = cycle_inventory.records + hermes_inventory.records
        byte_budget = request.limits.max_bytes
        artifacts: list[ManifestArtifact] = []
        for spec in specs:
            artifact, consumed = _snapshot_database(spec, stage, byte_budget)
            artifacts.append(artifact)
            byte_budget -= consumed
        for receipt in receipts:
            artifact = _copy_receipt(receipt, stage, byte_budget)
            artifacts.append(artifact)
            byte_budget -= artifact.size
        require_inventory_unchanged(request.cycle_receipts, cycle_inventory)
        require_inventory_unchanged(request.hermes_receipts, hermes_inventory)
        manifest = _manifest(tuple(sorted(artifacts, key=lambda item: item.path)), request.limits)
        raw = _canonical_manifest(manifest)
        write_private(stage / "manifest.json", raw)
        publish(stage, destination)
        return _result(raw, manifest)
    except BackupError:
        raise
    except (OSError, sqlite3.Error, TypeError, UnicodeError, ValidationError, ValueError):
        raise BackupError(BackupFailureReason.SOURCE_INVALID) from None
    finally:
        clean_stage(stage)


def verify_restore(request: RestoreRequest) -> BackupResult:
    _require_limits(request.limits)
    destination, stage = new_stage(request.destination)
    try:
        require_private_directory(request.bundle, BackupFailureReason.MANIFEST_INVALID)
        raw = read_private_bytes_query_only(request.bundle / "manifest.json", max_bytes=_manifest_limit(request.limits))
        manifest = BackupManifest.model_validate_json(raw)
        if raw != _canonical_manifest(manifest):
            raise BackupError(BackupFailureReason.MANIFEST_INVALID)
        if len(manifest.artifacts) > request.limits.max_files or manifest.total_bytes > request.limits.max_bytes:
            raise BackupError(BackupFailureReason.LIMIT_EXCEEDED)
        require_bundle_shape(
            request.bundle,
            {"manifest.json", *(item.path for item in manifest.artifacts)},
            request.limits.max_files,
        )
        restored: list[ManifestArtifact] = []
        for artifact in manifest.artifacts:
            payload = read_private_bytes_query_only(request.bundle / artifact.path, max_bytes=artifact.size + 1)
            if len(payload) != artifact.size or _sha256(payload) != artifact.sha256:
                raise BackupError(BackupFailureReason.ARTIFACT_INVALID)
            target = stage / artifact.path
            write_private(target, payload)
            restored.append(_verify_restored(target, artifact))
        if tuple(restored) != manifest.artifacts:
            raise BackupError(BackupFailureReason.ARTIFACT_INVALID)
        write_private(stage / "manifest.json", raw)
        write_private(stage / "restore-verification.json", _verification_receipt(raw, manifest))
        publish(stage, destination)
        return _result(raw, manifest)
    except BackupError:
        raise
    except (OSError, sqlite3.Error, TypeError, UnicodeError, ValidationError, ValueError):
        raise BackupError(BackupFailureReason.MANIFEST_INVALID) from None
    finally:
        clean_stage(stage)


def _snapshot_database(spec: _DatabaseSpec, stage: Path, max_bytes: int) -> tuple[ManifestArtifact, int]:
    target = stage / spec.relative
    payload, consumed = snapshot_sqlite(spec.source, target, max_bytes)
    semantic = _database_semantics(target, spec)
    return (
        ManifestArtifact(
            kind=spec.kind,
            path=spec.relative,
            size=len(payload),
            sha256=_sha256(payload),
            user_version=spec.version,
            semantic_sha256=semantic,
        ),
        consumed,
    )


def _database_semantics(path: Path, spec: _DatabaseSpec) -> str:
    with closing(sqlite3.connect(f"{sqlite_read_only_uri(path)}&immutable=1", uri=True)) as connection:
        _ = connection.execute("PRAGMA query_only=ON")
        _ = connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError(BackupFailureReason.SQLITE_INVALID)
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise BackupError(BackupFailureReason.SQLITE_INVALID)
        if connection.execute("PRAGMA user_version").fetchone() != (spec.version,):
            raise BackupError(BackupFailureReason.SCHEMA_INVALID)
        schema = _schema_signature(connection)
        if schema != _expected_schema(spec.kind):
            raise BackupError(BackupFailureReason.SCHEMA_INVALID)
        semantic_rows: list[tuple[str, tuple[tuple[str, ...], ...]]] = []
        for _, name, _, _ in schema:
            if not name.startswith("sqlite_") and _schema_type(schema, name) == "table":
                columns = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{name}")'))
                projection = ",".join(f'quote("{column}")' for column in columns)
                rows = tuple(sorted(connection.execute(f'SELECT {projection} FROM "{name}"').fetchall()))
                semantic_rows.append((name, rows))
    return _sha256(json.dumps((schema, semantic_rows), ensure_ascii=True, separators=(",", ":")).encode())


def _expected_schema(kind: ArtifactKind) -> tuple[tuple[str, str, str, str], ...]:
    with closing(sqlite3.connect(":memory:")) as connection:
        match kind:
            case ArtifactKind.CYCLE_DATABASE:
                for statement in RESEARCH_AGENT_CYCLE_SCHEMA:
                    _ = connection.execute(statement)
            case ArtifactKind.HERMES_DATABASE:
                prepare_hermes_delivery_schema(connection)
            case ArtifactKind.CYCLE_RECEIPT | ArtifactKind.HERMES_RECEIPT:
                raise BackupError(BackupFailureReason.SCHEMA_INVALID)
            case unreachable:
                assert_never(unreachable)
        return _schema_signature(connection)


def _schema_signature(connection: sqlite3.Connection) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY type,name")
    )


def _schema_type(schema: tuple[tuple[str, str, str, str], ...], name: str) -> str:
    return next(row[0] for row in schema if row[1] == name)


def _copy_receipt(receipt: ReceiptSource, stage: Path, max_bytes: int) -> ManifestArtifact:
    payload = copy_receipt(receipt, stage, max_bytes)
    return ManifestArtifact(kind=receipt.kind, path=receipt.relative, size=len(payload), sha256=_sha256(payload))


def _verify_restored(path: Path, artifact: ManifestArtifact) -> ManifestArtifact:
    match artifact.kind:
        case ArtifactKind.CYCLE_DATABASE | ArtifactKind.HERMES_DATABASE:
            version = artifact.user_version
            if version is None:
                raise BackupError(BackupFailureReason.MANIFEST_INVALID)
            semantic = _database_semantics(path, _DatabaseSpec(path, artifact.path, artifact.kind, version))
            if semantic != artifact.semantic_sha256:
                raise BackupError(BackupFailureReason.ARTIFACT_INVALID)
            return artifact
        case ArtifactKind.CYCLE_RECEIPT | ArtifactKind.HERMES_RECEIPT:
            return artifact
        case unreachable:
            assert_never(unreachable)


def _manifest(artifacts: tuple[ManifestArtifact, ...], limits: BackupLimits) -> BackupManifest:
    total = sum(item.size for item in artifacts)
    if total > limits.max_bytes:
        raise BackupError(BackupFailureReason.LIMIT_EXCEEDED)
    return BackupManifest(
        limits=ManifestLimits(max_files=limits.max_files, max_bytes=limits.max_bytes),
        artifacts=artifacts,
        total_bytes=total,
    )


def _canonical_manifest(manifest: BackupManifest) -> bytes:
    return (
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def _verification_receipt(raw: bytes, manifest: BackupManifest) -> bytes:
    payload = {
        "artifact_count": len(manifest.artifacts),
        "broker_mutation": 0,
        "heavy_processes": 0,
        "manifest_sha256": _sha256(raw),
        "model_calls": 0,
        "provider_calls": 0,
        "status": "verified",
    }
    return (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _result(raw: bytes, manifest: BackupManifest) -> BackupResult:
    semantics = tuple(item.semantic_sha256 for item in manifest.artifacts if item.semantic_sha256 is not None)
    return BackupResult(_sha256(raw), len(manifest.artifacts), manifest.total_bytes, (semantics[0], semantics[1]))


def _require_limits(limits: BackupLimits) -> None:
    if limits.max_files < 2 or limits.max_bytes < 1:
        raise BackupError(BackupFailureReason.INVALID_REQUEST)


def _manifest_limit(limits: BackupLimits) -> int:
    return min(16 * 1024 * 1024, max(4096, limits.max_files * 512))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = ("create_backup", "verify_restore")
