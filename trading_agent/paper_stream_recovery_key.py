from __future__ import annotations

import hashlib

from trading_agent.paper_account_activity_schema import EMPTY_ACTIVITY_HASH
from trading_agent.paper_protective_oco_schema import EMPTY_PROTECTIVE_OCO_HASH
from trading_agent.paper_stream_recovery_models import (
    PaperStreamRecoveryKey,
    PaperStreamRecoveryObservation,
)


def paper_stream_recovery_key(
    observation: PaperStreamRecoveryObservation,
    snapshot_hash: str,
    orders_hash: str,
    activities_hash: str,
    protective_ocos_hash: str,
) -> PaperStreamRecoveryKey:
    components = _legacy_components(observation, snapshot_hash, orders_hash)
    components = (*components[:-1], activities_hash, components[-1])
    if protective_ocos_hash != EMPTY_PROTECTIVE_OCO_HASH:
        components = (*components, protective_ocos_hash)
    return _key(components)


def paper_stream_recovery_key_is_valid(
    stored_key: PaperStreamRecoveryKey,
    observation: PaperStreamRecoveryObservation,
    snapshot_hash: str,
    orders_hash: str,
    activities_hash: str,
    protective_ocos_hash: str,
) -> bool:
    current = paper_stream_recovery_key(
        observation,
        snapshot_hash,
        orders_hash,
        activities_hash,
        protective_ocos_hash,
    )
    if stored_key == current:
        return True
    return (
        activities_hash == EMPTY_ACTIVITY_HASH
        and protective_ocos_hash == EMPTY_PROTECTIVE_OCO_HASH
        and stored_key == _key(_legacy_components(observation, snapshot_hash, orders_hash))
    )


def _legacy_components(
    observation: PaperStreamRecoveryObservation,
    snapshot_hash: str,
    orders_hash: str,
) -> tuple[str, ...]:
    return (
        observation.account_fingerprint,
        observation.connection_epoch,
        observation.started_at.isoformat(),
        observation.completed_at.isoformat(),
        snapshot_hash,
        orders_hash,
        str(int(observation.execution_detail_complete)),
    )


def _key(components: tuple[str, ...]) -> PaperStreamRecoveryKey:
    material = "\x00".join(components)
    return PaperStreamRecoveryKey(f"alpaca:recovery:{hashlib.sha256(material.encode()).hexdigest()}")
