from __future__ import annotations

import datetime as dt
import os
import stat
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.canonical_event_models import CanonicalEventEnvelope
from trading_agent.research_evidence_models import ResearchClaimExtraction

_MAX_INPUT_BYTES = 10_485_760


class ResearchEvidenceRequestError(ValueError):
    @override
    def __str__(self) -> str:
        return "research evidence request is invalid"


class ResearchEvidenceReadModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    as_of: dt.datetime
    current_window_seconds: int
    baseline_window_seconds: int
    burst_threshold_bps: int
    events: tuple[CanonicalEventEnvelope, ...]
    extractions: tuple[ResearchClaimExtraction, ...]

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            self.as_of.tzinfo is None
            or self.as_of.utcoffset() is None
            or not 1 <= self.current_window_seconds <= 86_400
            or not self.current_window_seconds <= self.baseline_window_seconds <= 2_592_000
            or not 10_000 <= self.burst_threshold_bps <= 100_000
            or not self.events
            or not self.extractions
        ):
            raise ResearchEvidenceRequestError
        return self


def load_research_evidence_request(path: Path) -> ResearchEvidenceReadModelRequest:
    try:
        candidate = path.expanduser().absolute()
        metadata = candidate.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > _MAX_INPUT_BYTES
        ):
            raise OSError
        return ResearchEvidenceReadModelRequest.model_validate_json(candidate.read_bytes())
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise ResearchEvidenceRequestError from None


__all__ = (
    "ResearchEvidenceReadModelRequest",
    "ResearchEvidenceRequestError",
    "load_research_evidence_request",
)
