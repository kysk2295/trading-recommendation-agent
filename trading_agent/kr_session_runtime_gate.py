from __future__ import annotations

import datetime as dt
from typing import Final, override
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot

KST: Final = ZoneInfo("Asia/Seoul")
_FIRST_COLLECTION: Final = dt.time(9, 1)
_SESSION_CLOSE: Final = dt.time(15, 30)
_EOD_CLOSE: Final = dt.time(15, 31)


class InvalidKrSessionRuntimeError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR session runtime evidence is invalid"


def require_open_kr_runtime_session(
    snapshot: KrSessionCalendarSnapshot,
    observed_at: dt.datetime,
) -> dt.date:
    try:
        local = _require_open_day(snapshot, observed_at)
        if local.time() < _FIRST_COLLECTION or local.time() >= _SESSION_CLOSE:
            raise InvalidKrSessionRuntimeError
        return local.date()
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrSessionRuntimeError from None


def require_open_kr_eod_session(
    snapshot: KrSessionCalendarSnapshot,
    observed_at: dt.datetime,
) -> dt.date:
    try:
        local = _require_open_day(snapshot, observed_at)
        if local.time() < _SESSION_CLOSE or local.time() >= _EOD_CLOSE:
            raise InvalidKrSessionRuntimeError
        return local.date()
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrSessionRuntimeError from None


def _require_open_day(
    snapshot: KrSessionCalendarSnapshot,
    observed_at: dt.datetime,
) -> dt.datetime:
    snapshot = KrSessionCalendarSnapshot.model_validate(snapshot.model_dump(mode="python"))
    if not _aware(observed_at) or snapshot.payload.observed_at > observed_at:
        raise InvalidKrSessionRuntimeError
    local = observed_at.astimezone(KST)
    matches = tuple(day for day in snapshot.payload.days if day.session_date == local.date())
    if len(matches) != 1 or not matches[0].business_day or not matches[0].trading_day or not matches[0].open_day:
        raise InvalidKrSessionRuntimeError
    return local


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
