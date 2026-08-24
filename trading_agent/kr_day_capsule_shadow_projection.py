from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowEventPayload,
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
)


def project_kr_day_capsule_shadow_event(
    evaluation: KrDayCapsuleEvaluation,
    previous: KrDayCapsuleShadowEvent | None,
    status: KrDayCapsuleShadowStatus,
    reason: KrDayCapsuleShadowReason,
    *,
    accepted_cursor: dt.datetime | None = None,
    signal_id: str | None = None,
    entry_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    target_prices: tuple[Decimal, ...] = (),
) -> KrDayCapsuleShadowEvent:
    latest = evaluation.setup_input.bars[-1]
    payload = KrDayCapsuleShadowEventPayload(
        capsule_id=evaluation.capsule_id,
        evaluation_id=evaluation.evaluation_id,
        session_date=evaluation.session_date,
        calendar_snapshot_id=evaluation.calendar_snapshot_id,
        collection_cycle_id=evaluation.collection_cycle_id,
        symbol=evaluation.symbol,
        attempted_bar_cursor=evaluation.completed_bar_cursor,
        accepted_bar_cursor=accepted_cursor if accepted_cursor is not None else (
            None if previous is None else previous.accepted_bar_cursor
        ),
        previous_event_id=None if previous is None else previous.event_id,
        status=status,
        reason=reason,
        signal_id=signal_id,
        entry_price=entry_price,
        stop_price=stop_price,
        target_prices=target_prices,
        occurred_at=evaluation.evaluated_at,
        evaluation_payload_sha256=hashlib.sha256(
            canonical_experiment_ledger_json(evaluation).encode()
        ).hexdigest(),
        bar_payload_sha256=hashlib.sha256(
            canonical_experiment_ledger_json(latest).encode()
        ).hexdigest(),
    )
    return KrDayCapsuleShadowEvent.model_validate(
        payload.model_dump(mode="python")
        | {"event_id": KrDayCapsuleShadowEvent.canonical_id_for(payload)}
    )


__all__ = ("project_kr_day_capsule_shadow_event",)
