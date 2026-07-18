from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import override

from trading_agent.data_capability_models import DataCapability, DataEntitlement


class DataCapabilityRegistryError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "data capability registry is invalid"


@dataclass(frozen=True, slots=True)
class RegistryAppendResult:
    capability_assessments: int
    entitlements: int


@dataclass(frozen=True, slots=True)
class DataCapabilityRegistrySnapshot:
    as_of: dt.datetime
    capabilities: tuple[DataCapability, ...]
    entitlements: tuple[DataEntitlement, ...]
    missing_capability_source_ids: tuple[str, ...]
    missing_entitlement_source_ids: tuple[str, ...]


__all__ = (
    "DataCapabilityRegistryError",
    "DataCapabilityRegistrySnapshot",
    "RegistryAppendResult",
)
