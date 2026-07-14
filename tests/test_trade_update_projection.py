from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
    intent,
    trade_update,
)


def test_partial_fill_then_cancel_is_terminal_but_retains_fill_evidence(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    partial = trade_update()
    canceled = trade_update(
        "canceled",
        status="canceled",
        filled_qty="10",
        execution_id=None,
    )
    with store.writer() as writer:
        _ = writer.append_trade_update(
            partial,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )
        _ = writer.append_trade_update(
            canceled,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )

    ledger = store.reconciliation_ledger()
    assert ledger.unresolved_intent_ids == frozenset()
    assert ledger.filled_intent_ids == frozenset({intent().intent_id})
    state = ledger.order_states[0]
    assert state.terminal is True
    assert state.cumulative_filled_quantity == Decimal("10")


def test_partial_then_full_fill_projects_exact_cumulative_quantity(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_trade_update(
            trade_update(),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )
        _ = writer.append_trade_update(
            trade_update(
                "fill",
                status="filled",
                filled_qty="100",
                execution_id="execution-2",
                execution_qty="90",
            ),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )

    state = store.reconciliation_ledger().order_states[0]
    assert state.cumulative_filled_quantity == Decimal("100")
    assert state.complete_fill is True
    assert state.terminal is True
    assert state.anomaly_reasons == ()


def test_mutually_exclusive_terminal_events_are_an_anomaly(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_trade_update(
            trade_update(
                "fill",
                status="filled",
                filled_qty="100",
                execution_qty="100",
            ),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )
        _ = writer.append_trade_update(
            trade_update(
                "canceled",
                status="canceled",
                filled_qty="100",
                execution_id=None,
            ),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )

    state = store.reconciliation_ledger().order_states[0]
    assert any("모순" in reason for reason in state.anomaly_reasons)


def test_missing_execution_gap_is_projected_as_an_anomaly(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_trade_update(
            trade_update(filled_qty="40", execution_qty="10"),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )

    state = store.reconciliation_ledger().order_states[0]
    assert state.cumulative_filled_quantity == Decimal("40")
    assert any("누락" in reason for reason in state.anomaly_reasons)


def test_cumulative_fill_without_an_execution_event_is_an_anomaly(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_trade_update(
            trade_update(
                "canceled",
                status="canceled",
                filled_qty="10",
                execution_id=None,
            ),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )

    state = store.reconciliation_ledger().order_states[0]
    assert state.cumulative_filled_quantity == Decimal("10")
    assert any("누락" in reason for reason in state.anomaly_reasons)


def test_late_nonterminal_event_cannot_reopen_a_filled_intent(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_trade_update(
            trade_update(
                "fill",
                status="filled",
                filled_qty="100",
                execution_qty="100",
            ),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )
        _ = writer.append_trade_update(
            trade_update(
                "accepted",
                status="accepted",
                filled_qty="0",
                execution_id=None,
            ),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )

    ledger = store.reconciliation_ledger()
    assert ledger.unresolved_intent_ids == frozenset()
    assert ledger.filled_intent_ids == frozenset({intent().intent_id})
