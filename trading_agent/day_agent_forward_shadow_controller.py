from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_agent_version_models import DayAgentVersionStoreError
from trading_agent.us_forward_shadow_artifacts import (
    UsForwardShadowOutcomeArtifact,
    UsForwardShadowSignalArtifact,
)
from trading_agent.us_forward_shadow_models import UsForwardShadowTick, UsForwardShadowTickResult
from trading_agent.us_forward_shadow_runtime import run_us_forward_shadow_tick
from trading_agent.us_forward_shadow_services import UsForwardShadowServices


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


class DayForwardShadowSessionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    session_id: str
    session_date: dt.date
    completed_bar_ids: tuple[str, ...] = Field(min_length=1)
    tick_results: tuple[UsForwardShadowTickResult, ...] = Field(min_length=1)
    signals: tuple[UsForwardShadowSignalArtifact, ...]
    outcomes: tuple[UsForwardShadowOutcomeArtifact, ...]


class DayForwardShadowRunner(Protocol):
    def run_session(
        self,
        request: DayForwardShadowSessionRequest,
        capsule_ids: tuple[str, str],
    ) -> DayForwardShadowSessionEvidence: ...


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
            signals=signals,
            outcomes=outcomes,
        )


__all__ = (
    "DayForwardShadowRunner",
    "DayForwardShadowSessionEvidence",
    "DayForwardShadowSessionRequest",
    "DayForwardShadowTickRequest",
    "UsForwardShadowControllerRunner",
)
