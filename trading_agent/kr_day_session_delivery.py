from __future__ import annotations

from pathlib import Path

from trading_agent.hermes_delivery_projection import HermesProjectionResult
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_delivery import (
    KrDayDecisionDeliveryBatch,
    project_kr_day_decision_delivery,
)
from trading_agent.kr_day_decision_store import KrDayDecisionStore


def project_kr_day_session_delivery(
    state_root: Path,
    delivery_database: Path,
) -> HermesProjectionResult:
    decisions = KrDayDecisionStore(state_root / "kr-day-decisions.sqlite3").events()
    shadows = KrDayCapsuleShadowStore(state_root / "kr-day-capsule-shadow.sqlite3").events()
    with HermesDeliveryStore(delivery_database).writer() as writer:
        return project_kr_day_decision_delivery(
            KrDayDecisionDeliveryBatch(decision_events=decisions, shadow_events=shadows),
            writer,
        )


__all__ = ("project_kr_day_session_delivery",)
