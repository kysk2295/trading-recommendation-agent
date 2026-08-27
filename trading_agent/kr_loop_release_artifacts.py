from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.kr_loop_engineer_git import KrLoopMutationExecutionError, clone_at, git, prepare_private_root
from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)

_SHA = r"^[a-f0-9]{64}$"
_GIT_SHA = r"^[a-f0-9]{40}$"


class InvalidKrLoopReleaseArtifactError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop release artifact is invalid"


class KrLoopReleaseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=_SHA)
    base_commit: str = Field(pattern=_GIT_SHA)
    candidate_commit: str = Field(pattern=_GIT_SHA)
    patch_sha256: str = Field(pattern=_SHA)
    baseline_root: Path
    candidate_root: Path
    baseline_source_sha256: str = Field(pattern=_SHA)
    candidate_source_sha256: str = Field(pattern=_SHA)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def require_distinct_absolute_roots(self) -> Self:
        if (
            self.base_commit == self.candidate_commit
            or not self.baseline_root.is_absolute()
            or not self.candidate_root.is_absolute()
            or self.baseline_root == self.candidate_root
        ):
            raise InvalidKrLoopReleaseArtifactError
        return self


class KrLoopReleaseArtifactStore:
    __slots__ = ("root",)

    def __init__(self, root: Path) -> None:
        self.root = absolute_private_path(root)

    def finalize(
        self,
        *,
        repository: Path,
        checkout: Path,
        task_root: Path,
        candidate_id: str,
        base_commit: str,
        candidate_commit: str,
        patch_sha256: str,
        created_at: dt.datetime,
    ) -> KrLoopReleaseArtifact:
        release_root = self._release_root(candidate_id)
        baseline_stage = task_root / f"{candidate_id}.baseline"
        try:
            if release_root.exists() or baseline_stage.exists():
                raise InvalidKrLoopReleaseArtifactError
            prepare_private_root(self.root)
            prepare_private_root(self.root / "releases")
            prepare_private_root(self.root / "release-manifests")
            clone_at(repository, baseline_stage, base_commit)
            if git(checkout, "rev-parse", "HEAD").strip() != candidate_commit:
                raise InvalidKrLoopReleaseArtifactError
            release_root.mkdir(mode=0o700)
            baseline_root = release_root / "baseline"
            candidate_root = release_root / "candidate"
            os.replace(baseline_stage, baseline_root)
            os.replace(checkout, candidate_root)
            manifest = KrLoopReleaseArtifact(
                candidate_id=candidate_id,
                base_commit=base_commit,
                candidate_commit=candidate_commit,
                patch_sha256=patch_sha256,
                baseline_root=baseline_root,
                candidate_root=candidate_root,
                baseline_source_sha256=_source_digest(baseline_root),
                candidate_source_sha256=_source_digest(candidate_root),
                created_at=created_at,
            )
            _ = publish_private_immutable_text(self._manifest_path(candidate_id), _canonical(manifest))
            _freeze_tree(baseline_root)
            _freeze_tree(candidate_root)
            release_root.chmod(0o500)
            return self.verified(candidate_id)
        except (
            InvalidKrLoopReleaseArtifactError,
            InvalidPrivateImmutableFileError,
            KrLoopMutationExecutionError,
            OSError,
            TypeError,
            ValidationError,
            ValueError,
        ):
            raise InvalidKrLoopReleaseArtifactError from None

    def verified(self, candidate_id: str) -> KrLoopReleaseArtifact:
        try:
            payload = read_private_text(self._manifest_path(candidate_id))
            manifest = KrLoopReleaseArtifact.model_validate_json(payload)
            release_root = self._release_root(candidate_id)
            if (
                payload != _canonical(manifest)
                or manifest.candidate_id != candidate_id
                or manifest.baseline_root != release_root / "baseline"
                or manifest.candidate_root != release_root / "candidate"
                or git(manifest.baseline_root, "rev-parse", "HEAD").strip() != manifest.base_commit
                or git(manifest.candidate_root, "rev-parse", "HEAD").strip() != manifest.candidate_commit
                or _source_digest(manifest.baseline_root) != manifest.baseline_source_sha256
                or _source_digest(manifest.candidate_root) != manifest.candidate_source_sha256
            ):
                raise InvalidKrLoopReleaseArtifactError
            _require_frozen_root(release_root)
            _require_frozen_root(manifest.baseline_root)
            _require_frozen_root(manifest.candidate_root)
            return manifest
        except (
            InvalidKrLoopReleaseArtifactError,
            InvalidPrivateImmutableFileError,
            KrLoopMutationExecutionError,
            OSError,
            TypeError,
            ValidationError,
            ValueError,
        ):
            raise InvalidKrLoopReleaseArtifactError from None

    def _release_root(self, candidate_id: str) -> Path:
        if len(candidate_id) != 64 or any(character not in "0123456789abcdef" for character in candidate_id):
            raise InvalidKrLoopReleaseArtifactError
        return self.root / "releases" / candidate_id

    def _manifest_path(self, candidate_id: str) -> Path:
        return self.root / "release-manifests" / f"{candidate_id}.json"


def _canonical(manifest: KrLoopReleaseArtifact) -> str:
    return json.dumps(manifest.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


def _source_digest(root: Path) -> str:
    paths = tuple(value for value in git(root, "ls-files", "-z").split("\0") if value)
    digest = hashlib.sha256()
    for relative in paths:
        target = root / relative
        metadata = target.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise InvalidKrLoopReleaseArtifactError
        payload = target.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative.encode())
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _freeze_tree(root: Path) -> None:
    for directory, names, files in os.walk(root, topdown=False, followlinks=False):
        parent = Path(directory)
        for name in files:
            target = parent / name
            if target.is_symlink():
                raise InvalidKrLoopReleaseArtifactError
            target.chmod(0o400)
        for name in names:
            target = parent / name
            if target.is_symlink():
                raise InvalidKrLoopReleaseArtifactError
            target.chmod(0o500)
    root.chmod(0o500)


def _require_frozen_root(root: Path) -> None:
    metadata = root.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o500:
        raise InvalidKrLoopReleaseArtifactError


__all__ = (
    "InvalidKrLoopReleaseArtifactError",
    "KrLoopReleaseArtifact",
    "KrLoopReleaseArtifactStore",
)
