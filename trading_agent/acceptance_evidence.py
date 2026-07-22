from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

_CRITERION: Final = re.compile(r"^AC-[0-9]{3}$")
_GIT_SHA: Final = re.compile(r"^[0-9a-f]{40}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


class AcceptanceSessionKind(StrEnum):
    REAL = "real"
    FIXTURE = "fixture"
    SYNTHETIC = "synthetic"


class AcceptanceEvidenceFailure(StrEnum):
    INVALID_REPOSITORY = "invalid_repository"
    GIT_UNAVAILABLE = "git_unavailable"
    COMMIT_MISMATCH = "commit_mismatch"
    DIRTY_REPOSITORY = "dirty_repository"
    MISSING_SESSION = "missing_session"
    NON_REAL_SESSION = "non_real_session"
    INVALID_ARTIFACT = "invalid_artifact"
    ARTIFACT_HASH_MISMATCH = "artifact_hash_mismatch"
    CRITERION_MISMATCH = "criterion_mismatch"
    INVALID_MANIFEST = "invalid_manifest"
    INVALID_OUTPUT = "invalid_output"


class InvalidAcceptanceEvidenceError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, reason: AcceptanceEvidenceFailure) -> None:
        super().__init__()
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason.value


class AcceptanceArtifactEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    path: Path
    sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        if not _is_relative_artifact_path(self.path) or _SHA256.fullmatch(self.sha256) is None:
            raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.INVALID_ARTIFACT)
        return self


class AcceptanceSessionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    session_id: str
    market_id: str
    kind: AcceptanceSessionKind
    observed_from: AwareDatetime
    observed_through: AwareDatetime

    @model_validator(mode="after")
    def validate_session(self) -> Self:
        if (
            _IDENTIFIER.fullmatch(self.session_id) is None
            or _IDENTIFIER.fullmatch(self.market_id) is None
            or self.observed_through < self.observed_from
        ):
            raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.INVALID_MANIFEST)
        return self


class AcceptanceEvidenceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    criterion_id: str
    policy_version: str
    commit_sha: str
    verifier_version: str
    generated_at: AwareDatetime
    sessions: tuple[AcceptanceSessionEvidence, ...]
    artifacts: tuple[AcceptanceArtifactEvidence, ...]
    fixture_labels: tuple[str, ...] = ()
    source_artifact_hashes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if (
            _CRITERION.fullmatch(self.criterion_id) is None
            or _IDENTIFIER.fullmatch(self.policy_version) is None
            or _GIT_SHA.fullmatch(self.commit_sha) is None
            or _IDENTIFIER.fullmatch(self.verifier_version) is None
            or not self.artifacts
            or len({artifact.path for artifact in self.artifacts}) != len(self.artifacts)
            or self.fixture_labels != tuple(sorted(set(self.fixture_labels)))
            or self.source_artifact_hashes != tuple(sorted(set(self.source_artifact_hashes)))
            or any(_IDENTIFIER.fullmatch(label) is None for label in self.fixture_labels)
            or any(_SHA256.fullmatch(value) is None for value in self.source_artifact_hashes)
        ):
            raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.INVALID_MANIFEST)
        return self


class AcceptanceEvidenceBuildRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    criterion_id: str
    policy_version: str
    verifier_version: str
    generated_at: AwareDatetime
    sessions: tuple[AcceptanceSessionEvidence, ...]
    artifact_paths: tuple[Path, ...]
    fixture_labels: tuple[str, ...] = ()
    source_artifact_hashes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            _CRITERION.fullmatch(self.criterion_id) is None
            or _IDENTIFIER.fullmatch(self.policy_version) is None
            or _IDENTIFIER.fullmatch(self.verifier_version) is None
            or not self.artifact_paths
            or any(not _is_relative_artifact_path(path) for path in self.artifact_paths)
            or len(set(self.artifact_paths)) != len(self.artifact_paths)
            or self.fixture_labels != tuple(sorted(set(self.fixture_labels)))
            or self.source_artifact_hashes != tuple(sorted(set(self.source_artifact_hashes)))
            or any(_IDENTIFIER.fullmatch(label) is None for label in self.fixture_labels)
            or any(_SHA256.fullmatch(value) is None for value in self.source_artifact_hashes)
        ):
            raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.INVALID_MANIFEST)
        return self


def build_acceptance_manifest(
    request: AcceptanceEvidenceBuildRequest,
    repository: Path,
    output: Path,
) -> AcceptanceEvidenceManifest:
    root = _repository_root(repository)
    commit_sha = require_clean_repository_commit(root)
    artifacts = tuple(
        AcceptanceArtifactEvidence(path=path, sha256=_artifact_sha256(root, path)) for path in request.artifact_paths
    )
    manifest = AcceptanceEvidenceManifest(
        criterion_id=request.criterion_id,
        policy_version=request.policy_version,
        commit_sha=commit_sha,
        verifier_version=request.verifier_version,
        generated_at=request.generated_at,
        sessions=request.sessions,
        artifacts=artifacts,
        fixture_labels=request.fixture_labels,
        source_artifact_hashes=request.source_artifact_hashes,
    )
    try:
        write_private_stable_report(output, manifest.model_dump_json(indent=2) + "\n")
    except InvalidPrivateStableReportError:
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.INVALID_OUTPUT) from None
    return manifest


def verify_acceptance_manifest(
    manifest: AcceptanceEvidenceManifest,
    repository: Path,
    *,
    require_clean_commit: bool,
    require_session_binding: bool,
) -> None:
    root = _repository_root(repository)
    if _git(root, "rev-parse", "HEAD") != manifest.commit_sha:
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.COMMIT_MISMATCH)
    if require_clean_commit and _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.DIRTY_REPOSITORY)
    if require_session_binding and not manifest.sessions:
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.MISSING_SESSION)
    if require_session_binding and any(session.kind is not AcceptanceSessionKind.REAL for session in manifest.sessions):
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.NON_REAL_SESSION)
    for artifact in manifest.artifacts:
        if _artifact_sha256(root, artifact.path) != artifact.sha256:
            raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.ARTIFACT_HASH_MISMATCH)


def require_clean_repository_commit(repository: Path) -> str:
    root = _repository_root(repository)
    commit_sha = _git(root, "rev-parse", "HEAD")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.DIRTY_REPOSITORY)
    return commit_sha


def acceptance_artifact_sha256(repository: Path, relative_path: Path) -> str:
    return _artifact_sha256(_repository_root(repository), relative_path)


def _repository_root(repository: Path) -> Path:
    try:
        root = repository.expanduser().resolve(strict=True)
    except OSError:
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.INVALID_REPOSITORY) from None
    if not root.is_dir() or not (root / ".git").exists():
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.INVALID_REPOSITORY)
    return root


def _git(repository: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, UnicodeError):
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.GIT_UNAVAILABLE) from None
    if completed.returncode != 0:
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.GIT_UNAVAILABLE)
    return completed.stdout.strip()


def _artifact_sha256(repository: Path, relative_path: Path) -> str:
    path = repository / relative_path
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(repository)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (OSError, ValueError):
        raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.INVALID_ARTIFACT) from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.INVALID_ARTIFACT)
        digest = hashlib.sha256()
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _is_relative_artifact_path(path: Path) -> bool:
    return not path.is_absolute() and path != Path() and ".." not in path.parts
