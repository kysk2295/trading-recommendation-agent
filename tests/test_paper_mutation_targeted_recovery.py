from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_paper_mutation_recovery import (
    _account,
    _oco,
    _plan,
    _record_ambiguous,
    _recover,
)
from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT, initialized_store
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    IntentId,
    PaperBrokerState,
    PaperOrderSide,
    PaperOrderSnapshot,
)
from trading_agent.paper_mutation_intents import (
    protective_oco_mutation_intent,
    safety_action_mutation_intent,
)
from trading_agent.paper_mutation_keys import paper_mutation_key
from trading_agent.paper_mutation_recovery_models import (
    PaperMutationRecoverySnapshot,
    PaperMutationRecoveryState,
)
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)
from trading_agent.paper_stream_recovery import PaperRecoveryState
from trading_agent.paper_stream_recovery_models import (
    PaperCancelOrderMutationLookup,
    PaperProtectiveOcoMutationLookup,
)


def test_targeted_client_id_lookup_acknowledges_matching_oco(
    tmp_path: Path,
) -> None:
    # Given: an ambiguous OCO and a matching deterministic client-ID lookup.
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)
    stored = store.protective_oco_plans()[0]
    mutation = protective_oco_mutation_intent(FINGERPRINT, stored)
    observed_at = OBSERVED_AT + dt.timedelta(seconds=10)
    with store.writer() as writer:
        _record_ambiguous(writer, mutation)
        protection = _oco(observed_at)
        state = PaperRecoveryState(
            PaperBrokerState(_account(observed_at), (), (), (protection,)),
            (),
            protective_ocos=(protection,),
            mutation_lookups=(
                PaperProtectiveOcoMutationLookup(
                    paper_mutation_key(mutation),
                    observed_at,
                    protection,
                ),
            ),
        )

        # When: current-epoch recovery evaluates the targeted observation.
        results = _recover(
            store,
            writer,
            PaperMutationRecoverySnapshot(
                "epoch-1",
                OBSERVED_AT + dt.timedelta(seconds=2),
                OBSERVED_AT + dt.timedelta(seconds=12),
                state,
            ),
        )

    # Then: the OCO is acknowledged without another POST.
    assert results[0].state is PaperMutationRecoveryState.ACKNOWLEDGED
    assert results[0].broker_order_id == "oco-parent-1"


@pytest.mark.parametrize(
    ("completed_seconds", "expected"),
    [
        (12, PaperMutationRecoveryState.UNRESOLVED),
        (42, PaperMutationRecoveryState.ABSENT),
    ],
)
def test_targeted_client_id_404_requires_conservative_settlement(
    tmp_path: Path,
    completed_seconds: int,
    expected: PaperMutationRecoveryState,
) -> None:
    # Given: a settled ambiguous OCO and an explicit targeted 404 observation.
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)
    stored = store.protective_oco_plans()[0]
    mutation = protective_oco_mutation_intent(FINGERPRINT, stored)
    completed_at = OBSERVED_AT + dt.timedelta(seconds=completed_seconds)
    observed_at = completed_at - dt.timedelta(seconds=2)
    with store.writer() as writer:
        _record_ambiguous(writer, mutation)
        state = PaperRecoveryState(
            PaperBrokerState(_account(observed_at), (), ()),
            (),
            mutation_lookups=(
                PaperProtectiveOcoMutationLookup(
                    paper_mutation_key(mutation),
                    observed_at,
                    None,
                ),
            ),
        )

        # When: recovery evaluates the explicit absence after settlement.
        results = _recover(
            store,
            writer,
            PaperMutationRecoverySnapshot(
                "epoch-1",
                completed_at - dt.timedelta(seconds=10),
                completed_at,
                state,
            ),
        )

    # Then: only this bounded 404 evidence authorizes a retryable absence.
    assert results[0].state is expected


def test_targeted_broker_id_lookup_acknowledges_terminal_cancel(
    tmp_path: Path,
) -> None:
    # Given: an ambiguous cancel and the exact broker order in terminal state.
    store = initialized_store(tmp_path)
    action = PaperCancelOrderAction(BrokerOrderId("entry-1"), "AAA", False)
    plan = PaperSafetyPlan(
        FINGERPRINT,
        OBSERVED_AT,
        dt.date(2026, 7, 14),
        PaperSafetyPhase.ENTRY_CUTOFF,
        Decimal(0),
        Decimal(0),
        (action,),
    )
    with store.writer() as writer:
        _ = writer.save_paper_safety_plan(plan)
    stored = store.paper_safety_plans()[0]
    mutation = safety_action_mutation_intent(stored, 0, action)
    observed_at = OBSERVED_AT + dt.timedelta(seconds=10)
    canceled = PaperOrderSnapshot(
        BrokerOrderId("entry-1"),
        IntentId("entry-client-1"),
        "AAA",
        PaperOrderSide.BUY,
        "canceled",
        Decimal(10),
        Decimal(0),
        Decimal("10"),
        "day",
        False,
    )
    with store.writer() as writer:
        _record_ambiguous(writer, mutation)
        state = PaperRecoveryState(
            PaperBrokerState(_account(observed_at), (), ()),
            (),
            (canceled,),
            mutation_lookups=(
                PaperCancelOrderMutationLookup(
                    paper_mutation_key(mutation),
                    observed_at,
                    action.broker_order_id,
                    canceled,
                ),
            ),
        )

        # When: recovery evaluates the exact broker-ID observation.
        results = _recover(
            store,
            writer,
            PaperMutationRecoverySnapshot(
                "epoch-1",
                OBSERVED_AT + dt.timedelta(seconds=2),
                OBSERVED_AT + dt.timedelta(seconds=12),
                state,
            ),
        )

    # Then: the cancel is acknowledged without another DELETE.
    assert results[0].state is PaperMutationRecoveryState.ACKNOWLEDGED
    assert results[0].broker_order_id == "entry-1"


def test_generic_order_inventory_cannot_acknowledge_ambiguous_oco(
    tmp_path: Path,
) -> None:
    # Given: an ambiguous OCO mutation and only a generic inventory observation.
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)
    stored = store.protective_oco_plans()[0]
    observed_at = OBSERVED_AT + dt.timedelta(seconds=10)
    with store.writer() as writer:
        _record_ambiguous(writer, protective_oco_mutation_intent(FINGERPRINT, stored))
        state = PaperRecoveryState(
            PaperBrokerState(
                _account(observed_at),
                (),
                (),
                (_oco(observed_at),),
            ),
            (),
            protective_ocos=(_oco(observed_at),),
        )

        # When: mutation recovery evaluates the generic snapshot.
        results = _recover(
            store,
            writer,
            PaperMutationRecoverySnapshot(
                "epoch-1",
                OBSERVED_AT + dt.timedelta(seconds=2),
                OBSERVED_AT + dt.timedelta(seconds=12),
                state,
            ),
        )

    # Then: deterministic client-order-ID evidence is required.
    assert results[0].state is PaperMutationRecoveryState.UNRESOLVED


def test_stale_absence_cannot_authorize_oco_repost(tmp_path: Path) -> None:
    # Given: an ambiguous attempt older than the automatic same-day recovery window.
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)
    stored = store.protective_oco_plans()[0]
    observed_at = OBSERVED_AT + dt.timedelta(days=8, seconds=10)
    with store.writer() as writer:
        mutation = protective_oco_mutation_intent(FINGERPRINT, stored)
        _record_ambiguous(writer, mutation)
        state = PaperRecoveryState(
            PaperBrokerState(_account(observed_at), (), ()),
            (),
            mutation_lookups=(
                PaperProtectiveOcoMutationLookup(
                    paper_mutation_key(mutation),
                    observed_at,
                    None,
                ),
            ),
        )

        # When: current-epoch recovery sees no matching OCO.
        results = _recover(
            store,
            writer,
            PaperMutationRecoverySnapshot(
                "epoch-1",
                observed_at - dt.timedelta(seconds=8),
                observed_at + dt.timedelta(seconds=2),
                state,
            ),
        )

    # Then: absence outside the bounded evidence window stays unresolved.
    assert results[0].state is PaperMutationRecoveryState.UNRESOLVED
