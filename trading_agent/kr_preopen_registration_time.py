from __future__ import annotations

import datetime as dt
from typing import override

_KST = dt.timezone(dt.timedelta(hours=9))
_MAX_AGE = dt.timedelta(minutes=5)


class InvalidKrPreopenRegistrationTimeError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR pre-open registration time is invalid"


def require_current_kr_preopen_registration(recorded_at: dt.datetime, now: dt.datetime) -> None:
    if not _aware(recorded_at) or not _aware(now):
        raise InvalidKrPreopenRegistrationTimeError
    age = now - recorded_at
    if age < dt.timedelta() or age > _MAX_AGE or recorded_at.astimezone(_KST).time() >= dt.time(9):
        raise InvalidKrPreopenRegistrationTimeError


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
