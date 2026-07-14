from __future__ import annotations

import datetime as dt
from typing import Final

from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_safety_models import PaperSafetyPhase
from trading_agent.paper_safety_store import StoredPaperSafetyPlan

DAILY_KILL_SWITCH_LATCHED: Final = "당일 Paper kill switch가 실행 원장에 latch되어 신규 진입을 차단합니다"


def daily_kill_switch_latched(
    plans: tuple[StoredPaperSafetyPlan, ...],
    account_fingerprint: AccountFingerprint,
    session_date: dt.date,
) -> bool:
    return any(
        stored.plan.account_fingerprint == account_fingerprint
        and stored.plan.session_date == session_date
        and stored.plan.phase is PaperSafetyPhase.KILL_SWITCH
        for stored in plans
    )
