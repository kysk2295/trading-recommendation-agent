from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.kr_loop_engineer_git import KrLoopMutationExecutionError, git
from trading_agent.kr_loop_engineer_models import KrLoopReleaseAction, KrLoopReleaseEvent
from trading_agent.kr_loop_release_artifacts import (
    InvalidKrLoopReleaseArtifactError,
    KrLoopReleaseArtifactStore,
)
from trading_agent.private_query_file import InvalidPrivateQueryFileError, read_private_text_query_only
from trading_agent.private_stable_report import InvalidPrivateStableReportError, write_private_stable_report

_SHA = r"^[a-f0-9]{64}$"
_GIT_SHA = r"^[a-f0-9]{40}$"


class InvalidKrLoopActiveReleaseError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop active release is invalid"


class KrLoopActiveRelease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    generation: int = Field(ge=0)
    release_id: str | None = Field(default=None, pattern=_SHA)
    candidate_id: str | None = Field(default=None, pattern=_SHA)
    action: Literal["baseline", "candidate"]
    source_root: Path
    active_commit: str = Field(pattern=_GIT_SHA)
    applied_at: AwareDatetime
    paper_only: Literal[True] = True
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def require_absolute_source(self) -> Self:
        bootstrap = self.generation == 0 and self.action == "baseline"
        if not self.source_root.is_absolute() or bootstrap != (self.release_id is None and self.candidate_id is None):
            raise InvalidKrLoopActiveReleaseError
        return self


def active_release_for_event(
    repository: Path,
    artifacts: KrLoopReleaseArtifactStore,
    event: KrLoopReleaseEvent,
    now: dt.datetime,
) -> KrLoopActiveRelease:
    del repository
    artifact = artifacts.verified(event.candidate_id)
    match event.action:
        case KrLoopReleaseAction.PROMOTE:
            action: Literal["baseline", "candidate"] = "candidate"
            source_root = artifact.candidate_root
            expected_commit = artifact.candidate_commit
        case KrLoopReleaseAction.ROLLBACK:
            action = "baseline"
            source_root = artifact.baseline_root
            expected_commit = artifact.base_commit
    if event.active_commit != expected_commit:
        raise InvalidKrLoopActiveReleaseError
    return KrLoopActiveRelease(
        generation=event.generation,
        release_id=event.release_id,
        candidate_id=event.candidate_id,
        action=action,
        source_root=source_root,
        active_commit=event.active_commit,
        applied_at=now,
    )


def bootstrap_active_release(
    repository: Path,
    active_commit: str,
    now: dt.datetime,
) -> KrLoopActiveRelease:
    root = repository.expanduser().absolute()
    if root.is_symlink() or not root.is_dir() or git(root, "rev-parse", "HEAD").strip() != active_commit:
        raise InvalidKrLoopActiveReleaseError
    return KrLoopActiveRelease(
        generation=0,
        release_id=None,
        candidate_id=None,
        action="baseline",
        source_root=root,
        active_commit=active_commit,
        applied_at=now,
    )


def baseline_active_release(
    artifacts: KrLoopReleaseArtifactStore,
    event: KrLoopReleaseEvent,
    now: dt.datetime,
) -> KrLoopActiveRelease:
    artifact = artifacts.verified(event.candidate_id)
    return KrLoopActiveRelease(
        generation=event.generation,
        release_id=event.release_id,
        candidate_id=event.candidate_id,
        action="baseline",
        source_root=artifact.baseline_root,
        active_commit=artifact.base_commit,
        applied_at=now,
    )


def replace_active_release(path: Path, release: KrLoopActiveRelease) -> bool:
    try:
        trusted = KrLoopActiveRelease.model_validate(release.model_dump(mode="python"))
        try:
            current = load_active_release(path)
        except InvalidKrLoopActiveReleaseError:
            current = None
        if current == trusted:
            return False
        write_private_stable_report(path, _canonical(trusted))
        return True
    except (
        InvalidKrLoopActiveReleaseError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrLoopActiveReleaseError from None


def load_active_release(path: Path) -> KrLoopActiveRelease:
    try:
        payload = read_private_text_query_only(path.expanduser().absolute())
        release = KrLoopActiveRelease.model_validate_json(payload)
        if payload != _canonical(release):
            raise InvalidKrLoopActiveReleaseError
        return release
    except (
        InvalidKrLoopActiveReleaseError,
        InvalidPrivateQueryFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrLoopActiveReleaseError from None


def resolve_active_source(
    path: Path,
    repository: Path,
    artifacts: KrLoopReleaseArtifactStore,
) -> Path:
    try:
        release = load_active_release(path)
        if release.generation == 0:
            root = repository.expanduser().absolute()
            if (
                release.source_root != root
                or root.is_symlink()
                or not root.is_dir()
                or git(root, "rev-parse", "HEAD").strip() != release.active_commit
            ):
                raise InvalidKrLoopActiveReleaseError
            return root
        if release.candidate_id is None:
            raise InvalidKrLoopActiveReleaseError
        artifact = artifacts.verified(release.candidate_id)
        if release.action == "candidate":
            expected_root = artifact.candidate_root
            expected_commit = artifact.candidate_commit
        else:
            expected_root = artifact.baseline_root
            expected_commit = artifact.base_commit
        if (
            release.source_root != expected_root
            or release.active_commit != expected_commit
            or git(release.source_root, "rev-parse", "HEAD").strip() != expected_commit
            or repository.is_symlink()
            or not repository.is_dir()
        ):
            raise InvalidKrLoopActiveReleaseError
        return release.source_root
    except (
        InvalidKrLoopActiveReleaseError,
        InvalidKrLoopReleaseArtifactError,
        KrLoopMutationExecutionError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise InvalidKrLoopActiveReleaseError from None


def _canonical(release: KrLoopActiveRelease) -> str:
    return json.dumps(release.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


__all__ = (
    "InvalidKrLoopActiveReleaseError",
    "KrLoopActiveRelease",
    "active_release_for_event",
    "baseline_active_release",
    "bootstrap_active_release",
    "load_active_release",
    "replace_active_release",
    "resolve_active_source",
)
