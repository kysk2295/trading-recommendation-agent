from __future__ import annotations

from pathlib import Path

from tests.test_kr_day_decision_store import _event as decision_event
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_day_decision_models import (
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.kr_day_session_delivery import project_kr_day_session_delivery


def test_session_projects_one_actionable_message_across_repeated_ticks(tmp_path: Path) -> None:
    # Given: the session decision ledger contains one user-visible conditional plan.
    state_root = tmp_path / "state"
    delivery_database = tmp_path / "hermes" / "delivery.sqlite3"
    armed = decision_event(
        status=KrDayDecisionStatus.ARMED,
        reason_codes=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,),
    )
    assert KrDayDecisionStore(state_root / "kr-day-decisions.sqlite3").append(armed)

    # When: two unchanged 120-second ticks project the complete immutable history.
    first = project_kr_day_session_delivery(state_root, delivery_database)
    replay = project_kr_day_session_delivery(state_root, delivery_database)

    # Then: Hermes retains one conditional ACTIONABLE event and suppresses the duplicate tick.
    events = HermesDeliveryStore(delivery_database).events()
    assert (first.inserted, replay.inserted) == (1, 0)
    assert tuple(event.kind for event in events) == (HermesDeliveryKind.ACTIONABLE,)
    assert "조건부" in events[0].rendered_text
    assert "shadow 전용" in events[0].rendered_text
