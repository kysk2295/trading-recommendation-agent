from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class InvalidKrFutureSessionSupervisorError(ValueError):
    pass


class KrSupervisorResult(StrEnum):
    WAITING = "waiting"
    TERMINAL_NO_RECOMMENDATION = "terminal_no_recommendation"
    TERMINAL_VERIFIED = "terminal_verified"
    INCIDENT = "incident"


class KrSupervisorPhase(StrEnum):
    CALENDAR = "calendar"
    COMPOSITE = "composite"
    REGISTER = "register"
    START = "start"
    CYCLE = "cycle"
    ONBOARD = "onboard"
    TICK_OPEN = "tick_open"
    TICK_CLOSE = "tick_close"
    TICK_POST = "tick_post"
    POST = "post"
    VERIFY = "verify"


class KrSupervisorCycleOutcome(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class KrFutureSessionSupervisorState(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )

    schema_version: Literal[1] = 1
    manifest_sha256: str
    completed_phases: tuple[KrSupervisorPhase, ...] = ()
    cycle_outcome: KrSupervisorCycleOutcome | None = None
    opportunity_id: str | None = None
    result: KrSupervisorResult = KrSupervisorResult.WAITING
    provider_mutations: Literal[0] = 0

    @model_validator(mode="after")
    def validate_state(self) -> KrFutureSessionSupervisorState:
        phases = self.completed_phases
        common = (
            KrSupervisorPhase.CALENDAR,
            KrSupervisorPhase.COMPOSITE,
            KrSupervisorPhase.REGISTER,
            KrSupervisorPhase.START,
            KrSupervisorPhase.CYCLE,
        )
        opportunity = (
            *common,
            KrSupervisorPhase.ONBOARD,
            KrSupervisorPhase.TICK_OPEN,
            KrSupervisorPhase.TICK_CLOSE,
            KrSupervisorPhase.TICK_POST,
            KrSupervisorPhase.VERIFY,
        )
        no_recommendation = (*common, KrSupervisorPhase.POST)
        prefix_geometry = phases == opportunity[: len(phases)] or phases == no_recommendation
        cycle_complete = KrSupervisorPhase.CYCLE in phases
        opportunity_phases = {
            KrSupervisorPhase.ONBOARD,
            KrSupervisorPhase.TICK_OPEN,
            KrSupervisorPhase.TICK_CLOSE,
            KrSupervisorPhase.TICK_POST,
            KrSupervisorPhase.VERIFY,
        }
        if (
            _SHA256.fullmatch(self.manifest_sha256) is None
            or len(set(phases)) != len(phases)
            or not prefix_geometry
            or cycle_complete != (self.cycle_outcome is not None)
            or (bool(opportunity_phases.intersection(phases)) and self.opportunity_id is None)
            or (self.opportunity_id is not None and self.cycle_outcome is not KrSupervisorCycleOutcome.READY)
            or (self.result is KrSupervisorResult.TERMINAL_VERIFIED and phases != opportunity)
            or (self.result is KrSupervisorResult.TERMINAL_NO_RECOMMENDATION and phases != no_recommendation)
        ):
            raise InvalidKrFutureSessionSupervisorError
        return self


def canonical_kr_supervisor_state_json(
    state: KrFutureSessionSupervisorState,
) -> str:
    return (
        json.dumps(
            state.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def kr_supervisor_state_path(manifest_path: Path) -> Path:
    return manifest_path.parent / "kr-supervisor-state.json"


__all__ = (
    "InvalidKrFutureSessionSupervisorError",
    "KrFutureSessionSupervisorState",
    "KrSupervisorCycleOutcome",
    "KrSupervisorPhase",
    "KrSupervisorResult",
    "canonical_kr_supervisor_state_json",
    "kr_supervisor_state_path",
)
