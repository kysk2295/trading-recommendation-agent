from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_agent_version_models import DayAgentVersionStoreError
from trading_agent.us_forward_shadow_artifacts import (
    UsForwardShadowOutcomeArtifact,
    UsForwardShadowSignalArtifact,
)
from trading_agent.us_forward_shadow_models import (
    UsForwardShadowStatus,
    UsForwardShadowTick,
    UsForwardShadowTickResult,
)
from trading_agent.us_forward_shadow_runtime import run_us_forward_shadow_tick
from trading_agent.us_forward_shadow_services import UsForwardShadowServices

_INCIDENT_STATUSES: Final = frozenset(
    {
        UsForwardShadowStatus.BLOCKED,
        UsForwardShadowStatus.CENSORED,
        UsForwardShadowStatus.FAILED,
    }
)
_NON_INCIDENT_REASONS: Final = frozenset({"target_r1_reached"})


class DayForwardShadowTickRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    tick: UsForwardShadowTick
    evaluation_at: AwareDatetime


class DayForwardShadowSessionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    ticks: tuple[DayForwardShadowTickRequest, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_session(self) -> DayForwardShadowSessionRequest:
        session_ids = {item.tick.session_id for item in self.ticks}
        sequences = tuple(item.tick.completed_bar_sequence for item in self.ticks)
        if len(session_ids) != 1 or sequences != tuple(sorted(set(sequences))):
            raise DayAgentVersionStoreError("future_shadow_session_invalid")
        return self


class DayForwardShadowIncidentEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    completed_bar_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: UsForwardShadowStatus
    event_ids: tuple[str, ...] = Field(min_length=1)
    reason_codes: tuple[str, ...] = Field(min_length=1)


class DayForwardShadowSessionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    session_id: str
    session_date: dt.date
    completed_bar_ids: tuple[str, ...] = Field(min_length=1)
    tick_results: tuple[UsForwardShadowTickResult, ...] = Field(min_length=1)
    incidents: tuple[DayForwardShadowIncidentEvidence, ...]
    signals: tuple[UsForwardShadowSignalArtifact, ...]
    outcomes: tuple[UsForwardShadowOutcomeArtifact, ...]


@dataclass(frozen=True, slots=True)
class UsForwardShadowControllerRunner:
    services: UsForwardShadowServices

    def run_session(
        self,
        request: DayForwardShadowSessionRequest,
        capsule_ids: tuple[str, str],
    ) -> DayForwardShadowSessionEvidence:
        results = tuple(
            run_us_forward_shadow_tick(
                item.tick,
                self.services,
                evaluation_at=item.evaluation_at,
            )
            for item in request.ticks
        )
        trial_ids = tuple(
            sorted(
                {
                    result.trial_id
                    for tick_result in results
                    for result in tick_result.results
                    if result.capsule_id in capsule_ids
                }
            )
        )
        signals = tuple(
            signal
            for trial_id in trial_ids
            for signal in (self.services.shadow_artifacts.signal_for_trial(trial_id),)
            if signal is not None
        )
        outcomes = tuple(
            outcome
            for trial_id in trial_ids
            for outcome in (self.services.shadow_artifacts.outcome_for_trial(trial_id),)
            if outcome is not None
        )
        first = request.ticks[0].tick
        return DayForwardShadowSessionEvidence(
            session_id=first.session_id,
            session_date=first.session_date,
            completed_bar_ids=tuple(item.tick.completed_bar_id for item in request.ticks),
            tick_results=results,
            incidents=controller_incidents(results),
            signals=signals,
            outcomes=outcomes,
        )


def controller_incidents(
    tick_results: tuple[UsForwardShadowTickResult, ...],
) -> tuple[DayForwardShadowIncidentEvidence, ...]:
    incidents: list[DayForwardShadowIncidentEvidence] = []
    for tick_result in tick_results:
        for result in tick_result.results:
            reasons = tuple(item for item in result.reason_codes if item not in _NON_INCIDENT_REASONS)
            if result.status not in _INCIDENT_STATUSES and not reasons:
                continue
            if not result.event_ids or not reasons:
                raise DayAgentVersionStoreError("future_shadow_incident_unresolved")
            incidents.append(
                DayForwardShadowIncidentEvidence(
                    completed_bar_id=tick_result.completed_bar_id,
                    capsule_id=result.capsule_id,
                    trial_id=result.trial_id,
                    status=result.status,
                    event_ids=result.event_ids,
                    reason_codes=reasons,
                )
            )
    return tuple(incidents)


__all__ = (
    "DayForwardShadowIncidentEvidence",
    "DayForwardShadowSessionEvidence",
    "DayForwardShadowSessionRequest",
    "DayForwardShadowTickRequest",
    "UsForwardShadowControllerRunner",
    "controller_incidents",
)
