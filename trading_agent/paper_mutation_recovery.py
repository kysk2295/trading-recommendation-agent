from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, assert_never, final

from trading_agent.execution_writer import ExecutionWriter
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationEventType,
)
from trading_agent.paper_mutation_recovery_models import (
    PaperMutationRecoveryResult,
    PaperMutationRecoverySnapshot,
    PaperMutationRecoveryState,
)
from trading_agent.paper_mutation_recovery_rules import (
    PaperMutationRecoveryCase,
    decide_paper_mutation_recovery,
)
from trading_agent.paper_mutation_store import (
    StoredPaperMutationEvent,
    StoredPaperMutationIntent,
)
from trading_agent.paper_protective_oco_store import StoredProtectiveOcoPlan
from trading_agent.paper_runtime import MAX_RUNTIME_RECEIPT_AGE

_RECOVERABLE_TYPES: Final = frozenset({PaperMutationEventType.ATTEMPTED, PaperMutationEventType.AMBIGUOUS})


class InvalidPaperMutationRecoverySnapshotError(RuntimeError):
    def __str__(self) -> str:
        return "Paper mutation 복구 snapshot이 current-epoch 계약과 일치하지 않습니다"


class PaperMutationRecoveryAccountError(RuntimeError):
    def __str__(self) -> str:
        return "Paper mutation 복구 계좌와 불변 intent 계좌가 다릅니다"


@dataclass(frozen=True, slots=True)
class PaperMutationRecoveryDependencies:
    writer: ExecutionWriter
    intents: Callable[[], tuple[StoredPaperMutationIntent, ...]]
    events: Callable[[], tuple[StoredPaperMutationEvent, ...]]
    protective_plans: Callable[[], tuple[StoredProtectiveOcoPlan, ...]]


@final
class PaperMutationRecovery:
    __slots__ = ("_dependencies",)

    def __init__(self, dependencies: PaperMutationRecoveryDependencies) -> None:
        self._dependencies = dependencies

    def recover(
        self,
        snapshot: PaperMutationRecoverySnapshot,
    ) -> tuple[PaperMutationRecoveryResult, ...]:
        _require_current_epoch_snapshot(snapshot)
        stored_events = self._dependencies.events()
        protective_plans = self._dependencies.protective_plans()
        results: list[PaperMutationRecoveryResult] = []
        for stored_intent in self._dependencies.intents():
            events = tuple(
                stored.event for stored in stored_events if stored.mutation_key == stored_intent.mutation_key
            )
            if not events or events[-1].event_type not in _RECOVERABLE_TYPES:
                continue
            if stored_intent.intent.account_fingerprint != snapshot.state.broker_state.account.account_fingerprint:
                raise PaperMutationRecoveryAccountError
            attempt_number = events[-1].attempt_number
            attempted = tuple(
                event
                for event in events
                if event.attempt_number == attempt_number and event.event_type is PaperMutationEventType.ATTEMPTED
            )
            if len(attempted) != 1:
                raise InvalidPaperMutationRecoverySnapshotError
            result = decide_paper_mutation_recovery(
                PaperMutationRecoveryCase(
                    stored_intent,
                    attempted[0],
                    snapshot,
                    protective_plans,
                )
            )
            results.append(result)
            self._append_recovery_event(result, attempt_number, snapshot)
        return tuple(results)

    def _append_recovery_event(
        self,
        result: PaperMutationRecoveryResult,
        attempt_number: int,
        snapshot: PaperMutationRecoverySnapshot,
    ) -> None:
        match result.state:
            case PaperMutationRecoveryState.ACKNOWLEDGED:
                event_type = PaperMutationEventType.RECOVERED_ACKNOWLEDGED
            case PaperMutationRecoveryState.ABSENT:
                event_type = PaperMutationEventType.RECOVERED_ABSENT
            case PaperMutationRecoveryState.UNRESOLVED:
                return
            case unreachable:
                assert_never(unreachable)
        evidence = json.dumps(
            (
                result.mutation_key,
                result.state.value,
                result.broker_order_id,
                snapshot.connection_epoch,
                snapshot.completed_at.isoformat(),
            ),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        _ = self._dependencies.writer.append_paper_mutation_event(
            result.mutation_key,
            PaperMutationEvent(
                attempt_number,
                snapshot.completed_at,
                event_type,
                None,
                None,
                result.broker_order_id,
                hashlib.sha256(evidence.encode()).hexdigest(),
            ),
        )


def _require_current_epoch_snapshot(
    snapshot: PaperMutationRecoverySnapshot,
) -> None:
    observed_at = snapshot.state.broker_state.account.observed_at
    receipts = tuple(protection.observed_at for protection in snapshot.state.protective_ocos)
    lookup_receipts = tuple(lookup.observed_at for lookup in snapshot.state.mutation_lookups)
    aware = (
        snapshot.started_at,
        snapshot.completed_at,
        observed_at,
        *receipts,
        *lookup_receipts,
    )
    if (
        not snapshot.connection_epoch
        or snapshot.connection_epoch.strip() != snapshot.connection_epoch
        or any(value.tzinfo is None or value.utcoffset() is None for value in aware)
        or snapshot.started_at >= snapshot.completed_at
        or not snapshot.started_at <= observed_at <= snapshot.completed_at
        or snapshot.completed_at - observed_at > MAX_RUNTIME_RECEIPT_AGE
        or any(
            not snapshot.started_at <= receipt <= snapshot.completed_at
            or snapshot.completed_at - receipt > MAX_RUNTIME_RECEIPT_AGE
            for receipt in receipts
        )
        or any(
            not snapshot.started_at <= receipt <= snapshot.completed_at
            or snapshot.completed_at - receipt > MAX_RUNTIME_RECEIPT_AGE
            for receipt in lookup_receipts
        )
    ):
        raise InvalidPaperMutationRecoverySnapshotError
