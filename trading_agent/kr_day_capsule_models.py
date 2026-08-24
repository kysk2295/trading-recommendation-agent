from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_strategy_capsule_models import (
    CapsuleAuthorityCeiling,
    StrategyCapsule,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kr_intraday_market_gate import KrMarketConstraintSnapshot
from trading_agent.kr_theme_day_setup import KrCompletedMinuteBar, KrThemeDaySetupInput
from trading_agent.signal_contract_models import OpportunitySnapshot


class InvalidKrDayCapsuleModelError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day capsule evaluation contract is invalid"


class KrDayCapsuleModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class KrDayCapsuleEvaluationRequest(KrDayCapsuleModel):
    capsule: StrategyCapsule
    calendar: KrSessionCalendarSnapshot
    opportunity: OpportunitySnapshot
    market: KrMarketConstraintSnapshot
    bars: tuple[KrCompletedMinuteBar, ...] = Field(min_length=1)
    evaluated_at: dt.datetime
    max_slippage_bps: Decimal = Field(gt=0)


class KrDayCapsuleEvaluationPayload(KrDayCapsuleModel):
    schema_version: Literal[1] = 1
    capsule_id: str
    hypothesis_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_date: dt.date
    calendar_snapshot_id: str
    calendar_receipt_sha256: str
    collection_cycle_id: str
    opportunity_id: str
    symbol: str
    evaluated_at: dt.datetime
    completed_bar_cursor: dt.datetime
    setup_input: KrThemeDaySetupInput
    market: KrMarketConstraintSnapshot
    authority_ceiling: Literal[CapsuleAuthorityCeiling.RESEARCH_ONLY]
    trading_authority: Literal[False] = False


class KrDayCapsuleEvaluation(KrDayCapsuleEvaluationPayload):
    evaluation_id: str

    @classmethod
    def canonical_id_for(cls, payload: KrDayCapsuleEvaluationPayload) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        payload = KrDayCapsuleEvaluationPayload.model_validate(
            self.model_dump(mode="python", exclude={"evaluation_id"})
        )
        if self.evaluation_id != self.canonical_id_for(payload):
            raise InvalidKrDayCapsuleModelError
        return self


__all__ = (
    "InvalidKrDayCapsuleModelError",
    "KrDayCapsuleEvaluation",
    "KrDayCapsuleEvaluationRequest",
)
