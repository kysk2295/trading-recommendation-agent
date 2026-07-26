from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Mapping
from itertools import pairwise


def safe_ref(value: str) -> str | None:
    if len(value) == 40 and all(character in "0123456789abcdef" for character in value):
        return hashlib.sha256(value.encode()).hexdigest()
    return None


def strict_stages(values: Mapping[str, str | dt.datetime | None], now: dt.datetime) -> bool:
    timestamps = tuple(
        value
        for key in (
            "source_at",
            "hypothesis_at",
            "code_at",
            "trial_at",
            "trial_started_at",
            "terminal_at",
            "reviewed_at",
            "lifecycle_at",
        )
        if isinstance((value := values[key]), dt.datetime)
    )
    return all(left < right for left, right in pairwise(timestamps)) and all(
        timestamp <= now for timestamp in timestamps
    )
