from __future__ import annotations

import hashlib

from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionRecord
from trading_agent.us_day_operating_models import UsDayOperatingRequest


def build_us_day_active_record(
    request: UsDayOperatingRequest,
    root_source_event_id: str,
    rendered_plan: str,
) -> HermesProjectionRecord:
    intent = request.order_admission.candidate_intent
    source_id = us_day_lifecycle_source_id(request, "ACTIVE")
    return HermesProjectionRecord(
        source_event_id=source_id,
        root_source_event_id=root_source_event_id,
        kind=HermesDeliveryKind.ACTIONABLE,
        market_id="us_equities",
        agent_family="day_trading",
        lane_id=request.lane_id.value,
        strategy_version=request.strategy_version,
        instrument_id=intent.symbol,
        occurred_at=request.evaluated_at,
        status="ACTIVE",
        evidence_refs=(
            f"actionable:{request.actionable_payload_sha256}",
            f"intent:{intent.intent_id}",
        ),
        rendered_text=f"US Day Alpaca Paper ACTIVE: {rendered_plan}",
        payload_sha256=hashlib.sha256(
            f"ACTIVE:{request.actionable_payload_sha256}".encode()
        ).hexdigest(),
    )


def us_day_lifecycle_source_id(request: UsDayOperatingRequest, status: str) -> str:
    identity = request.order_admission.candidate_intent.intent_id
    if request.thesis is not None:
        identity = request.thesis.thesis_id
    return f"us-day:lifecycle:{identity}:{status}"


__all__ = ("build_us_day_active_record", "us_day_lifecycle_source_id")
