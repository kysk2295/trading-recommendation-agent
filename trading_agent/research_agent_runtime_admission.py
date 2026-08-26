from __future__ import annotations

import datetime as dt
from typing import assert_never

from trading_agent import us_equity_calendar as us_calendar
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1


def primary_admission_required(
    evidence: ResearchAgentEvidenceV1,
    now: dt.datetime,
) -> bool:
    match evidence.agent_family_id:
        case "opportunity_manager":
            blocked_prefix = "opportunity.blocked."
        case "market_context":
            blocked_prefix = "market_context.blocked."
        case "day_trading":
            blocked_prefix = "day.blocked."
        case "swing_trading" | "systematic_quant" | "derivatives_research":
            return False
        case unreachable:
            assert_never(unreachable)
    if evidence.source_key.startswith(blocked_prefix):
        return True
    if not evidence.source_key.startswith(f"scheduled.{evidence.agent_family_id}."):
        return False
    current = now.astimezone(us_calendar.NEW_YORK)
    bounds = us_calendar.regular_session_bounds(current.date())
    return bounds is None or not bounds[0] <= current < bounds[1]


__all__ = ("primary_admission_required",)
