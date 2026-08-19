from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.generated_strategy_artifact import PublishedGeneratedStrategy
from trading_agent.generated_strategy_execution import GeneratedStrategyExecutionError
from trading_agent.generated_strategy_runtime import GeneratedStrategyRuntimeIdentity

_MAX_SOURCE_BYTES: Final = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GeneratedStrategySourceSnapshot:
    artifact_id: str
    source_sha256: str
    source_bytes: bytes


def capture_generated_strategy_source(
    published: PublishedGeneratedStrategy,
    runtime: GeneratedStrategyRuntimeIdentity,
) -> GeneratedStrategySourceSnapshot:
    if published.artifact.payload.runtime != runtime:
        raise GeneratedStrategyExecutionError("generated_artifact_invalid")
    source = _read_stable_source(
        published.source_path,
        published.artifact.payload.source_sha256,
        "generated_artifact_invalid",
    )
    return GeneratedStrategySourceSnapshot(
        artifact_id=published.artifact.artifact_id,
        source_sha256=published.artifact.payload.source_sha256,
        source_bytes=source,
    )


def materialize_generated_strategy_source(
    path: Path,
    snapshot: GeneratedStrategySourceSnapshot,
) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            offset = 0
            while offset < len(snapshot.source_bytes):
                offset += os.write(descriptor, snapshot.source_bytes[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        raise GeneratedStrategyExecutionError("session_source_invalid") from None


def require_generated_strategy_session_source(path: Path, expected_sha256: str) -> None:
    _ = _read_stable_source(path, expected_sha256, "session_source_invalid")


def _read_stable_source(path: Path, expected_sha256: str, error_reason: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size < 1
                or before.st_size > _MAX_SOURCE_BYTES
            ):
                raise GeneratedStrategyExecutionError(error_reason)
            content = bytearray()
            while chunk := os.read(descriptor, min(64 * 1024, _MAX_SOURCE_BYTES + 1 - len(content))):
                content.extend(chunk)
                if len(content) > _MAX_SOURCE_BYTES:
                    raise GeneratedStrategyExecutionError(error_reason)
            after = os.fstat(descriptor)
            before_identity = _source_identity(before)
            source = bytes(content)
            if before_identity != _source_identity(after) or hashlib.sha256(source).hexdigest() != expected_sha256:
                raise GeneratedStrategyExecutionError(error_reason)
            return source
        finally:
            os.close(descriptor)
    except GeneratedStrategyExecutionError:
        raise
    except OSError:
        raise GeneratedStrategyExecutionError(error_reason) from None


def _source_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
