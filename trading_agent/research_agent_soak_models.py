from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SoakEvidenceMode(StrEnum):
    ACTUAL = "actual"
    CONTROLLED_FIXTURE = "controlled_fixture"


class SoakCheckpointKind(StrEnum):
    PREPARED = "prepared"
    HEARTBEAT = "heartbeat"
    PROCESS_RESTART = "process_restart"
    REBOOT_RECOVERED = "reboot_recovered"
    PROVIDER_OUTAGE_OBSERVED = "provider_outage_observed"
    PROVIDER_OUTAGE_RECOVERED = "provider_outage_recovered"


class SoakState(StrEnum):
    COLLECTING = "collecting"
    COMPLETE = "complete"
    EXPIRED = "expired"


class SoakCheckpointPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    sequence: Annotated[int, Field(ge=1)]
    recorded_at: AwareDatetime
    monotonic_ns: Annotated[int, Field(ge=0)]
    boot_sha256: Sha256
    invocation_sha256: Sha256
    evidence_mode: SoakEvidenceMode
    kind: SoakCheckpointKind
    previous_sha256: Sha256


@dataclass(frozen=True, slots=True)
class SoakCheckpoint:
    payload: SoakCheckpointPayload
    checkpoint_sha256: str


@dataclass(frozen=True, slots=True)
class SoakObservation:
    recorded_at: dt.datetime
    monotonic_ns: int
    boot_sha256: str
    invocation_sha256: str


class SoakEffects(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_requests: Literal[0] = 0
    model_calls: Literal[0] = 0
    heavy_processes: Literal[0] = 0
    broker_mutations: Literal[0] = 0


class ResearchAgentSoakStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    status: SoakState
    evidence_mode: SoakEvidenceMode
    elapsed_seconds: Annotated[int, Field(ge=0)]
    required_seconds: Literal[86400] = 86400
    expiration_seconds: Literal[259200] = 259200
    checkpoint_count: Annotated[int, Field(ge=1)]
    head_sha256: Sha256
    actual_restart_observed: bool
    actual_reboot_observed: bool
    actual_provider_outage_observed: bool
    blockers: tuple[str, ...]
    effects: SoakEffects = SoakEffects()


def canonical_payload_json(payload: SoakCheckpointPayload) -> str:
    return json.dumps(payload.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def canonical_status_json(status: ResearchAgentSoakStatus) -> str:
    return json.dumps(status.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


__all__ = (
    "ResearchAgentSoakStatus",
    "SoakCheckpoint",
    "SoakCheckpointKind",
    "SoakCheckpointPayload",
    "SoakEvidenceMode",
    "SoakObservation",
    "SoakState",
    "canonical_payload_json",
    "canonical_status_json",
)
