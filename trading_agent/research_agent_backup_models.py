from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArtifactKind(StrEnum):
    CYCLE_DATABASE = "cycle_database"
    HERMES_DATABASE = "hermes_database"
    CYCLE_RECEIPT = "cycle_receipt"
    HERMES_RECEIPT = "hermes_receipt"


class BackupFailureReason(StrEnum):
    INVALID_REQUEST = "invalid_request"
    DESTINATION_EXISTS = "destination_exists"
    DESTINATION_INVALID = "destination_invalid"
    SOURCE_INVALID = "source_invalid"
    SOURCE_DRIFT = "source_drift"
    SCHEMA_INVALID = "schema_invalid"
    SQLITE_INVALID = "sqlite_invalid"
    RECEIPT_INVALID = "receipt_invalid"
    LIMIT_EXCEEDED = "limit_exceeded"
    MANIFEST_INVALID = "manifest_invalid"
    ARTIFACT_INVALID = "artifact_invalid"
    PUBLICATION_FAILED = "publication_failed"


class BackupError(RuntimeError):
    __slots__ = ("reason",)

    reason: BackupFailureReason

    def __init__(self, reason: BackupFailureReason) -> None:
        self.reason = reason
        super().__init__(reason.value)

    @override
    def __str__(self) -> str:
        return self.reason.value


@dataclass(frozen=True, slots=True)
class BackupLimits:
    max_files: int
    max_bytes: int


@dataclass(frozen=True, slots=True)
class BackupRequest:
    cycle_database: Path
    hermes_database: Path
    cycle_receipts: Path
    hermes_receipts: Path
    destination: Path
    limits: BackupLimits

    @property
    def databases(self) -> tuple[Path, Path]:
        return self.cycle_database, self.hermes_database


@dataclass(frozen=True, slots=True)
class RestoreRequest:
    bundle: Path
    destination: Path
    limits: BackupLimits


class ManifestLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_files: int = Field(ge=2)
    max_bytes: int = Field(ge=1)


class ManifestArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: ArtifactKind
    path: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    user_version: int | None = Field(default=None, ge=1)
    semantic_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_safe_shape(self) -> Self:
        relative = PurePosixPath(self.path)
        if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError
        match self.kind:
            case ArtifactKind.CYCLE_DATABASE | ArtifactKind.HERMES_DATABASE:
                if self.user_version is None or self.semantic_sha256 is None:
                    raise ValueError
            case ArtifactKind.CYCLE_RECEIPT | ArtifactKind.HERMES_RECEIPT:
                if self.user_version is not None or self.semantic_sha256 is not None:
                    raise ValueError
            case unreachable:
                assert_never(unreachable)
        return self


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["research_agent_backup"] = "research_agent_backup"
    version: Literal[1] = 1
    limits: ManifestLimits
    artifacts: tuple[ManifestArtifact, ...]
    total_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def require_complete_shape(self) -> Self:
        paths = tuple(item.path for item in self.artifacts)
        kinds = tuple(item.kind for item in self.artifacts)
        if len(paths) != len(set(paths)) or paths != tuple(sorted(paths)):
            raise ValueError
        if kinds.count(ArtifactKind.CYCLE_DATABASE) != 1 or kinds.count(ArtifactKind.HERMES_DATABASE) != 1:
            raise ValueError
        if len(paths) > self.limits.max_files or sum(item.size for item in self.artifacts) != self.total_bytes:
            raise ValueError
        if self.total_bytes > self.limits.max_bytes:
            raise ValueError
        return self


@dataclass(frozen=True, slots=True)
class BackupResult:
    manifest_sha256: str
    artifact_count: int
    total_bytes: int
    semantic_digests: tuple[str, str]
    provider_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    heavy_processes: Literal[0] = 0
    broker_mutation: Literal[0] = 0


__all__ = (
    "ArtifactKind",
    "BackupError",
    "BackupFailureReason",
    "BackupLimits",
    "BackupManifest",
    "BackupRequest",
    "BackupResult",
    "ManifestArtifact",
    "ManifestLimits",
    "RestoreRequest",
)
