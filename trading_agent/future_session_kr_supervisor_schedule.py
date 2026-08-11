from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

Clock = Callable[[], dt.datetime]
Sleeper = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class KrSupervisorPhaseWindow:
    start_epoch: int
    deadline_epoch: int


def await_kr_supervisor_phase_window(
    window: KrSupervisorPhaseWindow,
    clock: Clock,
    sleeper: Sleeper,
) -> bool:
    while (remaining := window.start_epoch - int(clock().timestamp())) > 0:
        sleeper(float(min(remaining, 60)))
    return int(clock().timestamp()) < window.deadline_epoch


def kr_session_close_epoch(session_date: dt.date) -> int:
    return int(
        dt.datetime.combine(
            session_date,
            dt.time(15, 30),
            tzinfo=dt.timezone(dt.timedelta(hours=9)),
        ).timestamp()
    )


__all__ = (
    "KrSupervisorPhaseWindow",
    "await_kr_supervisor_phase_window",
    "kr_session_close_epoch",
)
