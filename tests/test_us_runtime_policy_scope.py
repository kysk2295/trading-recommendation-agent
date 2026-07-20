from __future__ import annotations

import datetime as dt

from trading_agent.us_runtime_policy_scope import completed_regular_minute


def test_exact_boundary_advances_runtime_minute_for_reobservation_guard() -> None:
    boundary = dt.datetime(2026, 7, 21, 14, 30, tzinfo=dt.UTC)

    assert completed_regular_minute(boundary) == 60
    assert completed_regular_minute(boundary + dt.timedelta(seconds=1)) == 60
