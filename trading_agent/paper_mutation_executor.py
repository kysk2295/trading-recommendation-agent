from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, assert_never, final

import httpx2

from trading_agent.alpaca_paper_mutation_client import (
    PaperMutationRejectedError,
    PaperMutationResponseError,
)
from trading_agent.execution_writer import ExecutionWriter
from trading_agent.paper_execution_models import AccountFingerprint, SizedPaperOrder
from trading_agent.paper_mutation_acknowledgement import acknowledged_mutation_event
from trading_agent.paper_mutation_executor_models import (
    PaperMutationExecutionResult,
    PaperMutationExecutionState,
)
from trading_agent.paper_mutation_intents import (
    entry_order_mutation_intent,
    protective_oco_mutation_intent,
    safety_action_mutation_intent,
)
from trading_agent.paper_mutation_keys import PaperMutationKey, paper_mutation_key
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationEventType,
    PaperMutationIntent,
)
from trading_agent.paper_mutation_models import (
    PaperCancelOrderReceipt,
    PaperClosePositionReceipt,
    PaperEntryOrderReceipt,
    PaperProtectiveOcoReceipt,
)
from trading_agent.paper_mutation_store import StoredPaperMutationEvent
from trading_agent.paper_protective_oco_models import ProtectiveOcoExitPlan
from trading_agent.paper_protective_oco_store import StoredProtectiveOcoPlan
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
)
from trading_agent.paper_safety_store import StoredPaperSafetyPlan

type PaperMutationReceipt = (
    PaperProtectiveOcoReceipt | PaperCancelOrderReceipt | PaperClosePositionReceipt | PaperEntryOrderReceipt
)
type MutationCall = Callable[[], PaperMutationReceipt]


class PaperMutationBroker(Protocol):
    def submit_entry(self, order: SizedPaperOrder) -> PaperEntryOrderReceipt: ...

    def submit_protective_oco(
        self,
        plan: ProtectiveOcoExitPlan,
    ) -> PaperProtectiveOcoReceipt: ...

    def cancel_order(
        self,
        action: PaperCancelOrderAction,
    ) -> PaperCancelOrderReceipt: ...

    def close_position(
        self,
        action: PaperClosePositionAction,
    ) -> PaperClosePositionReceipt: ...


@dataclass(frozen=True, slots=True)
class PaperMutationExecutorDependencies:
    writer: ExecutionWriter
    events: Callable[[], tuple[StoredPaperMutationEvent, ...]]
    broker: PaperMutationBroker
    clock: Callable[[], dt.datetime]


@final
class PaperMutationExecutor:
    __slots__ = ("_dependencies",)

    def __init__(self, dependencies: PaperMutationExecutorDependencies) -> None:
        self._dependencies = dependencies

    def execute_protective_oco(
        self,
        account_fingerprint: AccountFingerprint,
        stored: StoredProtectiveOcoPlan,
    ) -> PaperMutationExecutionResult:
        intent = protective_oco_mutation_intent(account_fingerprint, stored)
        return self._run(
            intent,
            lambda: self._dependencies.broker.submit_protective_oco(stored.plan),
        )

    def execute_entry(
        self,
        account_fingerprint: AccountFingerprint,
        order: SizedPaperOrder,
    ) -> PaperMutationExecutionResult:
        intent = entry_order_mutation_intent(account_fingerprint, order)
        _ = self._dependencies.writer.save_entry_mutation_intent(order, intent)
        return self._run(
            intent,
            lambda: self._dependencies.broker.submit_entry(order),
            persist_intent=False,
        )

    def execute_safety_plan(
        self,
        stored: StoredPaperSafetyPlan,
    ) -> tuple[PaperMutationExecutionResult, ...]:
        results: list[PaperMutationExecutionResult] = []
        for sequence, action in enumerate(stored.plan.actions):
            intent = safety_action_mutation_intent(stored, sequence, action)
            match action:
                case PaperCancelOrderAction():
                    result = self._run(
                        intent,
                        lambda action=action: self._dependencies.broker.cancel_order(action),
                    )
                case PaperClosePositionAction():
                    result = self._run(
                        intent,
                        lambda action=action: self._dependencies.broker.close_position(action),
                    )
                case unreachable:
                    assert_never(unreachable)
            results.append(result)
            if result.state not in (
                PaperMutationExecutionState.ACKNOWLEDGED,
                PaperMutationExecutionState.ALREADY_ACKNOWLEDGED,
            ):
                break
        return tuple(results)

    def _run(
        self,
        intent: PaperMutationIntent,
        invoke: MutationCall,
        *,
        persist_intent: bool = True,
    ) -> PaperMutationExecutionResult:
        if persist_intent:
            _ = self._dependencies.writer.save_paper_mutation_intent(intent)
        mutation_key = paper_mutation_key(intent)
        events = tuple(stored.event for stored in self._dependencies.events() if stored.mutation_key == mutation_key)
        existing = _existing_result(mutation_key, events)
        if existing is not None:
            return existing
        attempt_number = (
            max(
                (event.attempt_number for event in events),
                default=0,
            )
            + 1
        )
        _ = self._dependencies.writer.append_paper_mutation_event(
            mutation_key,
            PaperMutationEvent(
                attempt_number,
                self._dependencies.clock(),
                PaperMutationEventType.ATTEMPTED,
                None,
                None,
                None,
                intent.request_sha256,
            ),
        )
        try:
            receipt = invoke()
        except PaperMutationRejectedError as error:
            return self._rejected(mutation_key, attempt_number, error)
        except httpx2.TransportError:
            return self._ambiguous(mutation_key, attempt_number, "transport_error")
        except PaperMutationResponseError:
            return self._ambiguous(mutation_key, attempt_number, "response_incomplete")
        event = acknowledged_mutation_event(
            receipt,
            attempt_number,
            self._dependencies.clock(),
        )
        _ = self._dependencies.writer.append_paper_mutation_event(
            mutation_key,
            event,
        )
        return PaperMutationExecutionResult(
            mutation_key,
            PaperMutationExecutionState.ACKNOWLEDGED,
            event.broker_order_id,
        )

    def _rejected(
        self,
        mutation_key: PaperMutationKey,
        attempt_number: int,
        error: PaperMutationRejectedError,
    ) -> PaperMutationExecutionResult:
        event = PaperMutationEvent(
            attempt_number,
            self._dependencies.clock(),
            PaperMutationEventType.REJECTED,
            error.request_id,
            error.status_code,
            None,
            _evidence_sha256(("rejected", error.request_id, error.status_code)),
        )
        _ = self._dependencies.writer.append_paper_mutation_event(mutation_key, event)
        return PaperMutationExecutionResult(
            mutation_key,
            PaperMutationExecutionState.REJECTED,
            None,
        )

    def _ambiguous(
        self,
        mutation_key: PaperMutationKey,
        attempt_number: int,
        reason: str,
    ) -> PaperMutationExecutionResult:
        event = PaperMutationEvent(
            attempt_number,
            self._dependencies.clock(),
            PaperMutationEventType.AMBIGUOUS,
            None,
            None,
            None,
            _evidence_sha256(("ambiguous", reason)),
        )
        _ = self._dependencies.writer.append_paper_mutation_event(mutation_key, event)
        return PaperMutationExecutionResult(
            mutation_key,
            PaperMutationExecutionState.AMBIGUOUS,
            None,
        )


def _existing_result(
    mutation_key: PaperMutationKey,
    events: tuple[PaperMutationEvent, ...],
) -> PaperMutationExecutionResult | None:
    if not events:
        return None
    latest = events[-1]
    match latest.event_type:
        case PaperMutationEventType.ACKNOWLEDGED | PaperMutationEventType.RECOVERED_ACKNOWLEDGED:
            state = PaperMutationExecutionState.ALREADY_ACKNOWLEDGED
        case PaperMutationEventType.REJECTED:
            state = PaperMutationExecutionState.REJECTED
        case PaperMutationEventType.ATTEMPTED | PaperMutationEventType.AMBIGUOUS:
            state = PaperMutationExecutionState.AMBIGUOUS
        case PaperMutationEventType.RECOVERED_ABSENT:
            return None
        case unreachable:
            assert_never(unreachable)
    return PaperMutationExecutionResult(mutation_key, state, latest.broker_order_id)


def _evidence_sha256(material: tuple[str | int | None, ...]) -> str:
    encoded = json.dumps(material, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
