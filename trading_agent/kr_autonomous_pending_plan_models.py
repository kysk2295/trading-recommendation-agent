from __future__ import annotations

import hashlib
import json
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from trading_agent.kr_autonomous_trade_models import KrAutonomousTradeProposal, KrAutonomousTradeRequest

_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
_SHA = r"^[a-f0-9]{64}$"


class KrAutonomousPendingPlan(BaseModel):
    model_config = _STRICT

    plan_id: str = Field(pattern=_SHA)
    request: KrAutonomousTradeRequest
    proposal: KrAutonomousTradeProposal

    @model_validator(mode="after")
    def validate_pending_plan(self) -> Self:
        if (
            self.request.thesis.task_id != self.request.social_signal.task_id
            or self.request.thesis.task_id != self.request.market.task_id
            or self.proposal.timestamp != self.request.evaluated_at
            or self.proposal.rationale != self.request.thesis.hypothesis
            or self.proposal.counterevidence != self.request.thesis.counterevidence
            or self.proposal.verification_state != self.request.social_signal.verification_state
            or self.proposal.valid_until != self.request.market.valid_until
            or self.plan_id != pending_plan_id(self)
        ):
            raise PydanticCustomError("kr_pending_plan", "KR autonomous pending plan is invalid")
        return self


def pending_plan_id(plan: KrAutonomousPendingPlan) -> str:
    payload = json.dumps(
        plan.model_dump(mode="json", exclude={"plan_id"}),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()
