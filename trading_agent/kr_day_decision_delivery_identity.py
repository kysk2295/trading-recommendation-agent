from __future__ import annotations

import hashlib
import json

from trading_agent.kr_day_decision_models import KrDayDecisionEvent


def kr_day_delivery_source_id(decision: KrDayDecisionEvent, state: str) -> str:
    material = (
        decision.capsule_id,
        decision.hypothesis_version_id,
        decision.opportunity_id,
        decision.session_date.isoformat(),
        decision.symbol,
        state,
    )
    digest = hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()
    return f"kr-day:{state.split(':')[0]}:{digest}"


def same_kr_day_thesis(left: KrDayDecisionEvent, right: KrDayDecisionEvent) -> bool:
    return (
        left.capsule_id,
        left.hypothesis_version_id,
        left.opportunity_id,
        left.session_date,
        left.symbol,
    ) == (
        right.capsule_id,
        right.hypothesis_version_id,
        right.opportunity_id,
        right.session_date,
        right.symbol,
    )


__all__ = ("kr_day_delivery_source_id", "same_kr_day_thesis")
