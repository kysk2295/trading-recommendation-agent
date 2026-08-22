from __future__ import annotations

import hashlib
import re
from typing import Final, override

_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")


class UsDayTaskIdentityError(ValueError):
    @override
    def __str__(self) -> str:
        return "us_day_task_identity_invalid"


def us_day_task_id(version_id: str, situation_id: str) -> str:
    if _HEX64.fullmatch(version_id) is None or _HEX64.fullmatch(situation_id) is None:
        raise UsDayTaskIdentityError
    digest = hashlib.sha256(f"{version_id}|{situation_id}".encode()).hexdigest()
    return f"us-day-{digest}"


__all__ = ("UsDayTaskIdentityError", "us_day_task_id")
