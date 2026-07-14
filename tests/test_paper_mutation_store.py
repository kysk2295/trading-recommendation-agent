from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
    intent,
)
from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSide
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationEventType,
    PaperMutationIntent,
    PaperMutationOperation,
)
from trading_agent.paper_mutation_store import (
    InvalidPaperMutationTransitionError,
    PaperMutationKey,
)
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoClientOrderId,
    ProtectiveOcoExitPlan,
)


def _protective_plan() -> ProtectiveOcoExitPlan:
    return ProtectiveOcoExitPlan(
        ProtectiveOcoClientOrderId("protect-" + "a" * 40),
        intent().intent_id,
        "AAA",
        PaperOrderSide.SELL,
        10,
        Decimal("10.5"),
        Decimal("9.75"),
    )


def _mutation(source_key: str) -> PaperMutationIntent:
    return PaperMutationIntent(
        account_fingerprint=FINGERPRINT,
        created_at=OBSERVED_AT,
        operation=PaperMutationOperation.SUBMIT_PROTECTIVE_OCO,
        protective_plan_key=source_key,
        safety_plan_key=None,
        action_sequence=None,
        request_sha256="1" * 64,
        symbol="AAA",
        broker_order_id=None,
        side=PaperOrderSide.SELL,
        quantity=Decimal(10),
    )


def _event(
    event_type: PaperMutationEventType,
    attempt: int = 1,
) -> PaperMutationEvent:
    acknowledged = event_type in (
        PaperMutationEventType.ACKNOWLEDGED,
        PaperMutationEventType.RECOVERED_ACKNOWLEDGED,
    )
    return PaperMutationEvent(
        attempt_number=attempt,
        occurred_at=OBSERVED_AT + dt.timedelta(seconds=attempt),
        event_type=event_type,
        request_id="request-1" if acknowledged else None,
        status_code=200 if acknowledged else None,
        broker_order_id=BrokerOrderId("oco-parent-1") if acknowledged else None,
        evidence_sha256="2" * 64,
    )


def test_mutation_intent_and_events_are_typed_append_only_and_idempotent(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_protective_plan(), OBSERVED_AT)
    source_key = store.protective_oco_plans()[0].plan_key

    with store.writer() as writer:
        first = writer.save_paper_mutation_intent(_mutation(source_key))
        replay = writer.save_paper_mutation_intent(_mutation(source_key))
        mutation_key = store.paper_mutation_intents()[0].mutation_key
        attempted = writer.append_paper_mutation_event(
            mutation_key,
            _event(PaperMutationEventType.ATTEMPTED),
        )
        acknowledged = writer.append_paper_mutation_event(
            mutation_key,
            _event(PaperMutationEventType.ACKNOWLEDGED),
        )

    assert first is True
    assert replay is False
    assert attempted is True
    assert acknowledged is True
    assert store.paper_mutation_intents()[0].intent == _mutation(source_key)
    assert tuple(stored.event.event_type for stored in store.paper_mutation_events()) == (
        PaperMutationEventType.ATTEMPTED,
        PaperMutationEventType.ACKNOWLEDGED,
    )
    assert store.reconciliation_ledger().paper_mutation_events == (*store.paper_mutation_events(),)
    with (
        sqlite3.connect(store.path) as connection,
        pytest.raises(
            sqlite3.IntegrityError,
            match="append-only",
        ),
    ):
        _ = connection.execute("UPDATE paper_mutation_events SET status_code = 201")


def test_ambiguous_attempt_must_be_recovered_absent_before_retry(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_protective_plan(), OBSERVED_AT)
    source_key = store.protective_oco_plans()[0].plan_key
    with store.writer() as writer:
        _ = writer.save_paper_mutation_intent(_mutation(source_key))
    mutation_key = store.paper_mutation_intents()[0].mutation_key

    with store.writer() as writer:
        _ = writer.append_paper_mutation_event(
            mutation_key,
            _event(PaperMutationEventType.ATTEMPTED),
        )
        _ = writer.append_paper_mutation_event(
            mutation_key,
            _event(PaperMutationEventType.AMBIGUOUS),
        )
        with pytest.raises(InvalidPaperMutationTransitionError):
            _ = writer.append_paper_mutation_event(
                mutation_key,
                _event(PaperMutationEventType.ATTEMPTED, attempt=2),
            )
        _ = writer.append_paper_mutation_event(
            mutation_key,
            _event(PaperMutationEventType.RECOVERED_ABSENT),
        )
        retried = writer.append_paper_mutation_event(
            mutation_key,
            _event(PaperMutationEventType.ATTEMPTED, attempt=2),
        )

    assert retried is True


def test_unknown_mutation_key_cannot_receive_an_event(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)

    with store.writer() as writer, pytest.raises(InvalidPaperMutationTransitionError):
        _ = writer.append_paper_mutation_event(
            PaperMutationKey("f" * 64),
            _event(PaperMutationEventType.ATTEMPTED),
        )
