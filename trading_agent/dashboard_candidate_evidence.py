from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1
from trading_agent.dashboard_outbound_redaction import redact_outbound_text
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)


class AutonomousCandidateEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    trigger_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{8,100}$")
    agent_family_id: AgentFamilyId
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    response_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_summary: str = Field(min_length=1, max_length=240)


def publish_model_candidate(
    experiment: Path,
    trigger: AutonomousTriggerV1,
    stdout: bytes,
) -> AutonomousCandidateEvidenceV1:
    evidence = AutonomousCandidateEvidenceV1(
        trigger_id=trigger.trigger_id,
        agent_family_id=trigger.agent_family_id,
        payload_sha256=trigger.payload_sha256,
        response_sha256=hashlib.sha256(stdout).hexdigest(),
        candidate_summary=redact_outbound_text(
            stdout.decode("utf-8", errors="replace").strip()
        ),
    )
    path = experiment / "candidate.json"
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        _ = publish_private_immutable_text(path, evidence.model_dump_json())
    else:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise InvalidPrivateImmutableFileError
    return evidence


__all__ = (
    "AutonomousCandidateEvidenceV1",
    "publish_model_candidate",
)
