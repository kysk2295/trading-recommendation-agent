from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_models import (
    EvidenceId,
    MarketId,
    ResearchAgentEvidenceV1,
    ResearchAgentTriggerKind,
)


class InvalidResearchAgentSourceError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ResearchAgentEvidenceMaterial:
    family: AgentFamilyId
    trigger: ResearchAgentTriggerKind
    source_key: str
    observed_at: dt.datetime
    available_at: dt.datetime
    market_id: MarketId
    canonical_payload: str
    subject_refs: tuple[str, ...] = ()
    payload_truncated: bool = False

    def evidence(self) -> ResearchAgentEvidenceV1:
        payload_sha256 = hashlib.sha256(self.canonical_payload.encode()).hexdigest()
        identity = hashlib.sha256(
            f"{self.family}:{self.trigger}:{self.source_key}:{payload_sha256}:evidence-v1".encode()
        ).hexdigest()
        subjects = self.subject_refs or (self.source_key,)
        return ResearchAgentEvidenceV1(
            evidence_id=EvidenceId(identity),
            agent_family_id=self.family,
            trigger_kind=self.trigger,
            source_key=self.source_key,
            evidence_refs=(payload_sha256,),
            observed_at=self.observed_at,
            available_at=self.available_at,
            payload_sha256=payload_sha256,
            market_id=self.market_id,
            bounded_payload_json=self.canonical_payload,
            payload_truncated=self.payload_truncated,
            subject_refs=tuple(sorted(set(subjects))),
        )


@dataclass(frozen=True, slots=True)
class CapabilityEvidenceSpec:
    family: AgentFamilyId
    source_key: str
    market_id: MarketId


def canonical_model_json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def canonical_payload_json(payload: Mapping[str, int | str]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def capability_evidence(
    spec: CapabilityEvidenceSpec,
    now: dt.datetime,
) -> ResearchAgentEvidenceV1:
    observed_at = now.astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    payload = canonical_payload_json({"as_of_date": observed_at.date().isoformat(), "status": spec.source_key})
    return ResearchAgentEvidenceMaterial(
        family=spec.family,
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key=spec.source_key,
        observed_at=observed_at,
        available_at=observed_at,
        market_id=spec.market_id,
        canonical_payload=payload,
    ).evidence()


def require_source_boundary(path: Path) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise InvalidResearchAgentSourceError(reason="source_path_invalid")
    if not path.exists():
        return
    metadata = path.lstat()
    if metadata.st_uid != os.getuid() or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise InvalidResearchAgentSourceError(reason="source_path_invalid")


def require_private_source_file(path: Path) -> None:
    require_source_boundary(path)
    if not path.is_file():
        raise InvalidResearchAgentSourceError(reason="source_file_invalid")
    metadata = path.lstat()
    if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
        raise InvalidResearchAgentSourceError(reason="source_file_invalid")


def interval_bucket(now: dt.datetime, minutes: int) -> dt.datetime:
    current = now.astimezone(dt.UTC)
    return current.replace(minute=current.minute - current.minute % minutes, second=0, microsecond=0)


__all__ = (
    "CapabilityEvidenceSpec",
    "InvalidResearchAgentSourceError",
    "ResearchAgentEvidenceMaterial",
    "canonical_model_json",
    "canonical_payload_json",
    "capability_evidence",
    "interval_bucket",
    "require_private_source_file",
    "require_source_boundary",
)
