from __future__ import annotations

from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError, PydanticSerializationError

from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousCriticStatus,
    KrAutonomousCriticVerdict,
    KrAutonomousTradeRequest,
    KrAutonomousTradeThesis,
    KrCriticReason,
    verdict_id,
)

_SHA = r"^[a-f0-9]{64}$"


class KrTradeRejectionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    thesis: KrAutonomousTradeThesis
    evaluated_at: AwareDatetime
    next_wake_at: AwareDatetime
    previous_event_id: Annotated[str | None, Field(pattern=_SHA)] = None

    @model_validator(mode="after")
    def validate_wake(self) -> Self:
        if self.next_wake_at <= self.evaluated_at:
            raise PydanticCustomError("kr_trade_rejection_context", "KR trade rejection context is invalid")
        return self


type KrTradeEventContext = KrAutonomousTradeRequest | KrTradeRejectionContext


def revalidate_kr_autonomous_trade_request(
    request: KrAutonomousTradeRequest,
) -> KrAutonomousTradeRequest | None:
    try:
        return KrAutonomousTradeRequest.model_validate_json(request.model_dump_json())
    except (PydanticSerializationError, ValidationError):
        return None


def project_kr_trade_rejection_context(
    request: KrAutonomousTradeRequest,
) -> KrTradeRejectionContext | None:
    try:
        payload = request.model_dump_json(exclude={"social_signal", "market", "open_exposures"})
        return KrTradeRejectionContext.model_validate_json(payload)
    except (PydanticSerializationError, ValidationError):
        return None


def has_missing_kr_trade_spread(request: KrAutonomousTradeRequest) -> bool:
    try:
        snapshot = request.market.market_snapshot
        return snapshot.bid_price is None or snapshot.ask_price is None or request.market.spread_bps < 0
    except (AttributeError, TypeError):
        return False


def build_kr_integrity_verdict(context: KrTradeEventContext) -> KrAutonomousCriticVerdict:
    draft = KrAutonomousCriticVerdict.model_construct(
        verdict_id="",
        proposal_id=None,
        thesis_id=context.thesis.thesis_id,
        status=KrAutonomousCriticStatus.REJECTED,
        reason_codes=(KrCriticReason.EVIDENCE_LINEAGE,),
    )
    return KrAutonomousCriticVerdict.model_validate(
        draft.model_copy(update={"verdict_id": verdict_id(draft)}).model_dump(mode="python")
    )
