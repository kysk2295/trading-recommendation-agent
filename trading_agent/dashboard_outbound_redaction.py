from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_FORBIDDEN: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?i)\b(?:api[_-]?key|authorization|bearer|cookie|password|secret|token)\b"
        r"(?:\s*[:=]?\s*\S+)?"
    ),
    re.compile(r"(?i)\b(?:account[_-]?(?:id|fingerprint)|session[_-]?id|worktree)\b(?:\s+\S+)?"),
    re.compile(r"(?i)\b(?:raw[_-]?(?:payload|header|response))\b(?:\s+\S+)?"),
    re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\)[^\s]+"),
)


@dataclass(frozen=True, slots=True)
class UnsafeOutboundAgentEventError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


def redact_outbound_text(value: str, *, max_chars: int = 240) -> str:
    redacted = value
    for pattern in _FORBIDDEN:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted[:max_chars]


def require_safe_outbound_text(value: str) -> None:
    if any(pattern.search(value) is not None for pattern in _FORBIDDEN):
        raise UnsafeOutboundAgentEventError(reason="outbound_agent_event_contains_private_data")


__all__ = (
    "UnsafeOutboundAgentEventError",
    "redact_outbound_text",
    "require_safe_outbound_text",
)
