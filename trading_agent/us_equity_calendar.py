from __future__ import annotations

import datetime as dt
from typing import Final
from zoneinfo import ZoneInfo

NEW_YORK: Final = ZoneInfo("America/New_York")
REGULAR_OPEN: Final = dt.time(9, 30)
REGULAR_CLOSE: Final = dt.time(16)
EARLY_CLOSE: Final = dt.time(13)
PUBLISHED_CALENDAR_YEARS: Final = frozenset(range(2023, 2029))
FULL_DAY_HOLIDAYS: Final[frozenset[dt.date]] = frozenset(
    {
        dt.date(2023, 1, 2),
        dt.date(2023, 1, 16),
        dt.date(2023, 2, 20),
        dt.date(2023, 4, 7),
        dt.date(2023, 5, 29),
        dt.date(2023, 6, 19),
        dt.date(2023, 7, 4),
        dt.date(2023, 9, 4),
        dt.date(2023, 11, 23),
        dt.date(2023, 12, 25),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 15),
        dt.date(2024, 2, 19),
        dt.date(2024, 3, 29),
        dt.date(2024, 5, 27),
        dt.date(2024, 6, 19),
        dt.date(2024, 7, 4),
        dt.date(2024, 9, 2),
        dt.date(2024, 11, 28),
        dt.date(2024, 12, 25),
        dt.date(2025, 1, 1),
        dt.date(2025, 1, 9),
        dt.date(2025, 1, 20),
        dt.date(2025, 2, 17),
        dt.date(2025, 4, 18),
        dt.date(2025, 5, 26),
        dt.date(2025, 6, 19),
        dt.date(2025, 7, 4),
        dt.date(2025, 9, 1),
        dt.date(2025, 11, 27),
        dt.date(2025, 12, 25),
        dt.date(2026, 1, 1),
        dt.date(2026, 1, 19),
        dt.date(2026, 2, 16),
        dt.date(2026, 4, 3),
        dt.date(2026, 5, 25),
        dt.date(2026, 6, 19),
        dt.date(2026, 7, 3),
        dt.date(2026, 9, 7),
        dt.date(2026, 11, 26),
        dt.date(2026, 12, 25),
        dt.date(2027, 1, 1),
        dt.date(2027, 1, 18),
        dt.date(2027, 2, 15),
        dt.date(2027, 3, 26),
        dt.date(2027, 5, 31),
        dt.date(2027, 6, 18),
        dt.date(2027, 7, 5),
        dt.date(2027, 9, 6),
        dt.date(2027, 11, 25),
        dt.date(2027, 12, 24),
        dt.date(2028, 1, 17),
        dt.date(2028, 2, 21),
        dt.date(2028, 4, 14),
        dt.date(2028, 5, 29),
        dt.date(2028, 6, 19),
        dt.date(2028, 7, 4),
        dt.date(2028, 9, 4),
        dt.date(2028, 11, 23),
        dt.date(2028, 12, 25),
    }
)
EARLY_CLOSE_DAYS: Final[frozenset[dt.date]] = frozenset(
    {
        dt.date(2023, 7, 3),
        dt.date(2023, 11, 24),
        dt.date(2024, 7, 3),
        dt.date(2024, 11, 29),
        dt.date(2024, 12, 24),
        dt.date(2025, 7, 3),
        dt.date(2025, 11, 28),
        dt.date(2025, 12, 24),
        dt.date(2026, 11, 27),
        dt.date(2026, 12, 24),
        dt.date(2027, 11, 26),
        dt.date(2028, 7, 3),
        dt.date(2028, 11, 24),
    }
)


def regular_session_bounds(
    session_date: dt.date,
) -> tuple[dt.datetime, dt.datetime] | None:
    if (
        session_date.year not in PUBLISHED_CALENDAR_YEARS
        or session_date.weekday() >= 5
        or session_date in FULL_DAY_HOLIDAYS
    ):
        return None
    close_time = EARLY_CLOSE if session_date in EARLY_CLOSE_DAYS else REGULAR_CLOSE
    return (
        dt.datetime.combine(session_date, REGULAR_OPEN, tzinfo=NEW_YORK),
        dt.datetime.combine(session_date, close_time, tzinfo=NEW_YORK),
    )
