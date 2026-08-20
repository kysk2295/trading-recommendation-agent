from __future__ import annotations

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_forward_trial_models import DayForwardTrial
from trading_agent.research_identity_models import MarketId


class InvalidForwardProbeAdmissionError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


class AdmissionModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )


class ForwardProbeQueueItem(AdmissionModel):
    trial: DayForwardTrial
    policy_priority: int = Field(ge=0)
    queued_at: AwareDatetime

    @model_validator(mode="after")
    def queued_after_preregistration(self) -> Self:
        if self.queued_at < self.trial.preregistered_at:
            raise InvalidForwardProbeAdmissionError("forward_probe_queue_time_invalid")
        return self


class ForwardProbeSlotRequest(AdmissionModel):
    market_id: MarketId
    candidates: tuple[ForwardProbeQueueItem, ...]
    active_capsule_ids: tuple[str, ...] = Field(max_length=3)
    max_active_slots: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="after")
    def valid_request(self) -> Self:
        trial_ids = tuple(item.trial.trial_id for item in self.candidates)
        active = self.active_capsule_ids
        if any(item.trial.market_id is not self.market_id for item in self.candidates):
            raise InvalidForwardProbeAdmissionError("forward_probe_candidate_market_mismatch")
        if len(set(trial_ids)) != len(trial_ids):
            raise InvalidForwardProbeAdmissionError("forward_probe_trial_duplicate")
        if (
            active != tuple(sorted(set(active)))
            or any(
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in active
            )
            or len(active) > self.max_active_slots
        ):
            raise InvalidForwardProbeAdmissionError("forward_probe_active_slots_invalid")
        return self


class ForwardProbeSlotSelection(AdmissionModel):
    market_id: MarketId
    selected: tuple[ForwardProbeQueueItem, ...]
    queued: tuple[ForwardProbeQueueItem, ...]
    active_capsule_ids: tuple[str, ...] = Field(max_length=3)


def select_active_probe_slots(
    request: ForwardProbeSlotRequest,
) -> ForwardProbeSlotSelection:
    ordered = tuple(
        sorted(
            request.candidates,
            key=lambda item: (
                item.policy_priority,
                item.queued_at,
                item.trial.first_eligible_completed_bar_at,
                item.trial.capsule_id,
                item.trial.trial_id,
            ),
        )
    )
    active = list(request.active_capsule_ids)
    selected: list[ForwardProbeQueueItem] = []
    queued: list[ForwardProbeQueueItem] = []
    for item in ordered:
        if item.trial.capsule_id in active:
            queued.append(item)
        elif len(active) < request.max_active_slots:
            active.append(item.trial.capsule_id)
            selected.append(item)
        else:
            queued.append(item)
    return ForwardProbeSlotSelection(
        market_id=request.market_id,
        selected=tuple(selected),
        queued=tuple(queued),
        active_capsule_ids=tuple(sorted(active)),
    )


__all__ = (
    "ForwardProbeQueueItem",
    "ForwardProbeSlotRequest",
    "ForwardProbeSlotSelection",
    "InvalidForwardProbeAdmissionError",
    "select_active_probe_slots",
)
