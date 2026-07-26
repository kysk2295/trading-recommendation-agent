from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Literal

from trading_agent.dashboard_models_v2 import SourceStateName

ProviderName = Literal[
    "fred",
    "alfred",
    "treasury",
    "cftc",
    "opendart",
    "kis",
    "ls",
    "alpaca",
]
DashboardEntitlement = Literal[
    "realtime",
    "delayed",
    "research_only",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    provider: ProviderName
    state: SourceStateName
    entitlement: DashboardEntitlement
    observed_at: dt.datetime | None
    value: str | None
    safe_ref: str
    blocker_code: str | None


def unavailable_provider(
    provider: ProviderName,
    blocker_code: str,
) -> ProviderEvidence:
    return ProviderEvidence(
        provider=provider,
        state="unavailable",
        entitlement="unavailable",
        observed_at=None,
        value=None,
        safe_ref=hashlib.sha256(f"{provider}:{blocker_code}".encode()).hexdigest(),
        blocker_code=blocker_code,
    )


__all__ = (
    "DashboardEntitlement",
    "ProviderEvidence",
    "ProviderName",
    "unavailable_provider",
)
