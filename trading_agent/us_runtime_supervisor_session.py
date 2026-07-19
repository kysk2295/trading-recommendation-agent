from __future__ import annotations

import datetime as dt

from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


def runtime_supervisor_session_is_open(value: dt.datetime) -> bool | None:
    if type(value) is not dt.datetime or value.tzinfo is None or value.utcoffset() is None:
        return None
    current = value.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    return bounds is not None and bounds[0] <= current < bounds[1]


__all__ = ("runtime_supervisor_session_is_open",)
