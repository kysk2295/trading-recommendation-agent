from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PreparedKrSupervisorArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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


class KrFutureSessionPreparationManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    market: Literal["kr"] = "kr"
    target_session: str
    request_sha256: str
    plan_sha256: str
    canonical_plan_file_sha256: str
    request_file: Path
    plan_file: Path
    scheduler_main_sha: str
    scheduler_authority_mode: Literal["current_main", "frozen_runtime"] = "current_main"
    runtime_commit_sha: str
    authority_repository: Path
    frozen_runtime: Path
    runtime_interpreter: Path
    experiment_ledger: Path
    experiment_ledger_schema_version: Literal[9] = 9
    experiment_ledger_identity_sha256: str
    kr_rollover_bundle_sha256: str
    kr_policy_sha256: str
    internal_phase_epochs: tuple[int, int, int, int, int, int]
    entry: PreparedKrSupervisorArtifact

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        hashes = (
            self.request_sha256,
            self.plan_sha256,
            self.canonical_plan_file_sha256,
            self.experiment_ledger_identity_sha256,
            self.kr_rollover_bundle_sha256,
            self.kr_policy_sha256,
        )
        if (
            any(_SHA256.fullmatch(value) is None for value in hashes)
            or tuple(sorted(self.internal_phase_epochs)) != self.internal_phase_epochs
            or len(set(self.internal_phase_epochs)) != 6
            or not all(
                path.is_absolute()
                for path in (
                    self.authority_repository,
                    self.frozen_runtime,
                    self.runtime_interpreter,
                    self.experiment_ledger,
                    self.request_file,
                    self.plan_file,
                )
            )
        ):
            raise ValueError("invalid KR preparation manifest")
        return self


def canonical_kr_manifest_json(value: KrFutureSessionPreparationManifest) -> str:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


__all__ = (
    "KrFutureSessionPreparationManifest",
    "PreparedKrSupervisorArtifact",
    "canonical_kr_manifest_json",
)
