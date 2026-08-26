from __future__ import annotations

import re
from typing import Final

_REDACTED: Final = "[REDACTED]"
_SEPARATOR: Final = r"[^A-Za-z0-9]*"
_LABEL: Final = rf"(?:authorization|access{_SEPARATOR}(?:key|token)|refresh{_SEPARATOR}(?:key|token)|" + (
    rf"api{_SEPARATOR}(?:key|secret|token)|session(?:{_SEPARATOR}(?:cookie|id|key|token))?|"
    rf"cookie|password|client{_SEPARATOR}secret|account{_SEPARATOR}(?:id|number)|"
    rf"auth{_SEPARATOR}(?:secret|token)|token|secret|jwt)"
)
_ASSIGNED_SECRET: Final = re.compile(
    rf"(?P<label>\b{_LABEL}\b\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|(?:bearer|basic)\s+[^\s,;]+|[^\s,;]+)",
    flags=re.IGNORECASE,
)
_BEARER_SECRET: Final = re.compile(r"\bbearer\s+[^\s,;]+", flags=re.IGNORECASE)
_JWT_SECRET: Final = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)


def redact_browser_observation_text(value: str) -> str:
    assigned = _ASSIGNED_SECRET.sub(lambda match: f"{match.group('label')}{_REDACTED}", value)
    bearer = _BEARER_SECRET.sub(f"Bearer {_REDACTED}", assigned)
    return _JWT_SECRET.sub(_REDACTED, bearer)


def contains_browser_secret_text(value: str) -> bool:
    return bool(_ASSIGNED_SECRET.search(value) or _BEARER_SECRET.search(value) or _JWT_SECRET.search(value))


__all__ = ["contains_browser_secret_text", "redact_browser_observation_text"]
