from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionDateRange:
    start: dt.date
    end: dt.date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("session start must be on or before session end")

    def contains(self, session_date: dt.date) -> bool:
        return self.start <= session_date <= self.end

    @classmethod
    def optional(
        cls,
        start: dt.date | None,
        end: dt.date | None,
    ) -> SessionDateRange | None:
        if start is None and end is None:
            return None
        if start is None or end is None:
            raise ValueError("session start and end must be provided together")
        return cls(start, end)
