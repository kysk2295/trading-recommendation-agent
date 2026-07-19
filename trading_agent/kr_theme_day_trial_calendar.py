from __future__ import annotations

import datetime as dt
import re
from typing import Final, override
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot

_KST: Final = ZoneInfo("Asia/Seoul")
_MAX_CALENDAR_AGE: Final = dt.timedelta(minutes=5)
_CALENDAR_PREFIX: Final = "calendar_snapshot:"
_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")


class InvalidKrThemeDayTrialCalendarError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day trial calendar evidence is invalid"


def require_kr_theme_day_trial_calendar(
    snapshot: KrSessionCalendarSnapshot,
    session_date: dt.date,
    registered_at: dt.datetime,
) -> str:
    try:
        if not _aware(registered_at):
            raise InvalidKrThemeDayTrialCalendarError
        snapshot = KrSessionCalendarSnapshot.model_validate(snapshot.model_dump(mode="python"))
        local_registered = registered_at.astimezone(_KST)
        payload = snapshot.payload
        matches = tuple(day for day in payload.days if day.session_date == session_date)
        if (
            payload.base_date != local_registered.date()
            or payload.observed_at > registered_at
            or registered_at - payload.observed_at > _MAX_CALENDAR_AGE
            or len(matches) != 1
            or not matches[0].business_day
            or not matches[0].trading_day
            or not matches[0].open_day
        ):
            raise InvalidKrThemeDayTrialCalendarError
        return snapshot.snapshot_id
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayTrialCalendarError from None


def kr_theme_day_trial_evidence_budget(
    base_budget: tuple[str, ...],
    calendar_snapshot_id: str,
) -> tuple[str, ...]:
    if _HEX64.fullmatch(calendar_snapshot_id) is None:
        raise InvalidKrThemeDayTrialCalendarError
    return tuple(sorted((*base_budget, f"{_CALENDAR_PREFIX}{calendar_snapshot_id}")))


def calendar_snapshot_id_from_evidence(evidence_budget: tuple[str, ...]) -> str:
    matches = tuple(
        value.removeprefix(_CALENDAR_PREFIX) for value in evidence_budget if value.startswith(_CALENDAR_PREFIX)
    )
    if len(matches) != 1 or _HEX64.fullmatch(matches[0]) is None:
        raise InvalidKrThemeDayTrialCalendarError
    return matches[0]


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
