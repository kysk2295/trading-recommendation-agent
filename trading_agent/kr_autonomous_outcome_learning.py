from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths
from trading_agent.kr_autonomous_outcome_memory import append_records, bundle_records, outcome_record
from trading_agent.kr_autonomous_outcome_models import InvalidKrAutonomousOutcomeError
from trading_agent.kr_autonomous_outcome_observation import (
    build_kr_autonomous_outcome,
    outcome_memory_key,
)
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore


@dataclass(frozen=True, slots=True)
class KrOutcomeLearningResult:
    inserted_memories: int
    inserted_bundles: int
    memory_keys: tuple[str, ...]
    bundle_keys: tuple[str, ...]


def observe_kr_autonomous_outcomes(
    paths: KrAutonomousOperatorPaths,
    *,
    now: dt.datetime,
) -> KrOutcomeLearningResult:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidKrAutonomousOutcomeError
    memory = AutonomousMemoryStore(paths.memory_database)
    outcomes = tuple(
        build_kr_autonomous_outcome(paths, event, now)
        for event in KrAutonomousTradeStore(paths.trade_database).events()
    )
    records = tuple(outcome_record(memory, outcome) for outcome in outcomes)
    inserted_memories = append_records(memory, tuple(record for record in records if record is not None))
    bundles = bundle_records(memory, outcomes)
    inserted_bundles = append_records(memory, tuple(record for record in bundles if record is not None))
    return KrOutcomeLearningResult(
        inserted_memories,
        inserted_bundles,
        tuple(outcome_memory_key(outcome) for outcome in outcomes),
        tuple(record.memory_key for record in bundles if record is not None),
    )


__all__ = ("KrOutcomeLearningResult", "observe_kr_autonomous_outcomes", "outcome_memory_key")
