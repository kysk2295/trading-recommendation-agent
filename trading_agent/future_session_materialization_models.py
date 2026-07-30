from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.future_session_plan_models import FutureSessionUsRole


class PreparedUsRoleArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: FutureSessionUsRole
    label: str
    payload_wrapper: Path
    payload_sha256: str
    persistent_wrapper: Path
    persistent_wrapper_sha256: str
    persistent_plist: Path
    persistent_plist_sha256: str
    receipt: Path
    stdout_log: Path
    stderr_log: Path


class FutureSessionPreparationManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    request_sha256: str
    plan_sha256: str
    canonical_plan_file_sha256: str
    scheduler_main_sha: str
    runtime_commit_sha: str
    runtime_attestation_sha256: str
    authority_repository: Path
    frozen_runtime: Path
    entries: tuple[PreparedUsRoleArtifact, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if (
            tuple(entry.role for entry in self.entries)
            != tuple(FutureSessionUsRole)
            or len({entry.label for entry in self.entries}) != len(FutureSessionUsRole)
        ):
            raise ValueError("invalid US preparation entries")
        return self


def canonical_manifest_json(value: FutureSessionPreparationManifest) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


__all__ = (
    "FutureSessionPreparationManifest",
    "PreparedUsRoleArtifact",
    "canonical_manifest_json",
)
