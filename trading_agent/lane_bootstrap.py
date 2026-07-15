from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import override

from trading_agent.execution_store import ExecutionStore
from trading_agent.lane_contract_models import LaneAccountBinding, lane_account_binding
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    DEFAULT_LANE_MANIFESTS,
    INTRADAY_MANIFEST,
)
from trading_agent.lane_registry_store import LaneRegistryStore


class InvalidLaneBootstrapError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "기존 execution 원장이 lane binding에 필요한 current schema와 account 결합을 제공하지 않습니다"


class LaneAccountBootstrapState(StrEnum):
    NOT_REQUESTED = "not_requested"
    REGISTERED = "registered"
    ALREADY_REGISTERED = "already_registered"


@dataclass(frozen=True, slots=True)
class LaneBootstrapResult:
    manifests_created: int
    manifests_total: int
    scopes_created: int
    scopes_total: int
    intraday_account_binding: LaneAccountBootstrapState


def bootstrap_lane_control_plane(
    registry: LaneRegistryStore,
    intraday_execution_database: Path | None = None,
) -> LaneBootstrapResult:
    binding = None if intraday_execution_database is None else _intraday_binding(intraday_execution_database)
    with registry.writer() as writer:
        manifests_created = sum(writer.register_manifest(manifest) for manifest in DEFAULT_LANE_MANIFESTS)
        scopes_created = sum(writer.register_experiment_scope(scope) for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES)
        if binding is None:
            binding_state = LaneAccountBootstrapState.NOT_REQUESTED
        else:
            binding_state = (
                LaneAccountBootstrapState.REGISTERED
                if writer.bind_account(binding)
                else LaneAccountBootstrapState.ALREADY_REGISTERED
            )
    return LaneBootstrapResult(
        manifests_created=manifests_created,
        manifests_total=len(DEFAULT_LANE_MANIFESTS),
        scopes_created=scopes_created,
        scopes_total=len(CURRENT_INTRADAY_EXPERIMENT_SCOPES),
        intraday_account_binding=binding_state,
    )


def _intraday_binding(execution_database: Path) -> LaneAccountBinding:
    store = ExecutionStore(execution_database)
    if not store.is_initialized():
        raise InvalidLaneBootstrapError
    stored = store.account_binding()
    if stored is None:
        raise InvalidLaneBootstrapError
    try:
        bound_at = dt.datetime.fromisoformat(stored.bound_at)
    except ValueError:
        raise InvalidLaneBootstrapError from None
    ledger_fingerprint = hashlib.sha256(str(store.path).encode()).hexdigest()
    return lane_account_binding(
        INTRADAY_MANIFEST,
        stored.account_fingerprint,
        ledger_fingerprint,
        bound_at,
    )
