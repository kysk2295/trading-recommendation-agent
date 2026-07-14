from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT
from trading_agent.paper_safety_latch import daily_kill_switch_latched
from trading_agent.paper_safety_models import PaperSafetyPhase, PaperSafetyPlan
from trading_agent.paper_safety_store import (
    PaperSafetyPlanKey,
    StoredPaperSafetyPlan,
)


def _stored(session_date: dt.date) -> StoredPaperSafetyPlan:
    return StoredPaperSafetyPlan(
        PaperSafetyPlanKey("k" * 64),
        "h" * 64,
        PaperSafetyPlan(
            FINGERPRINT,
            OBSERVED_AT,
            session_date,
            PaperSafetyPhase.KILL_SWITCH,
            Decimal("-301"),
            Decimal("-301"),
            (),
        ),
    )


def test_daily_kill_latch_expires_at_the_next_new_york_session() -> None:
    plans = (_stored(dt.date(2026, 7, 14)),)

    assert daily_kill_switch_latched(plans, FINGERPRINT, dt.date(2026, 7, 14))
    assert not daily_kill_switch_latched(
        plans,
        FINGERPRINT,
        dt.date(2026, 7, 15),
    )
