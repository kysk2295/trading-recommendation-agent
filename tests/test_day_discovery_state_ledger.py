from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from trading_agent.day_discovery_ledger_models import (
    DayDiscoveryBudgetAccount,
    DayDiscoveryBudgetDebit,
    DayDiscoveryCallReservationPayload,
    DayDiscoveryCallResponsePayload,
    DayDiscoveryCycle,
    DayDiscoveryDebitKind,
    DayDiscoveryEvent,
    DayDiscoveryEventKind,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.research_identity_models import MarketId

NOW = dt.datetime(2026, 8, 20, 14, tzinfo=dt.UTC)
SHA_A = "a" * 64


def _account(limit: int = 1) -> DayDiscoveryBudgetAccount:
    payload = {
        "account_id": "",
        "market_id": MarketId.US_EQUITIES,
        "budget_epoch_ref": "session-2026-08-20",
        "debit_limit": limit,
        "created_at": NOW,
    }
    return DayDiscoveryBudgetAccount.model_validate(
        payload | {"account_id": DayDiscoveryBudgetAccount.canonical_id_for(payload)}
    )


def _cycle(account: DayDiscoveryBudgetAccount, cursor: str) -> DayDiscoveryCycle:
    payload = {
        "cycle_id": "",
        "account_id": account.account_id,
        "market_id": account.market_id,
        "evidence_sha256": SHA_A,
        "cursor_sha256": cursor,
        "opened_at": NOW,
    }
    return DayDiscoveryCycle.model_validate(
        payload | {"cycle_id": DayDiscoveryCycle.canonical_id_for(payload)}
    )


def _debit(account: DayDiscoveryBudgetAccount, cycle: DayDiscoveryCycle) -> DayDiscoveryBudgetDebit:
    payload = {
        "debit_id": "",
        "account_id": account.account_id,
        "cycle_id": cycle.cycle_id,
        "branch_index": 0,
        "debit_kind": DayDiscoveryDebitKind.CALL_RESERVATION,
        "amount": 1,
        "debited_at": NOW + dt.timedelta(microseconds=1),
    }
    return DayDiscoveryBudgetDebit.model_validate(
        payload | {"debit_id": DayDiscoveryBudgetDebit.canonical_id_for(payload)}
    )


def _reservation_event(cycle: DayDiscoveryCycle, previous: DayDiscoveryEvent) -> DayDiscoveryEvent:
    reservation_payload = {
        "reservation_id": "",
        "account_id": cycle.account_id,
        "cycle_id": cycle.cycle_id,
        "branch_index": 0,
        "prompt_sha256": "d" * 64,
        "prompt_bytes_sha256": "d" * 64,
        "prompt_length": 12,
        "model_id": "test-model",
        "seed": 7,
        "temperature": 0.0,
        "protocol_sha256": "e" * 64,
        "creator": "test-suite",
        "creator_sha256": hashlib.sha256(b"test-suite").hexdigest(),
        "reserved_at": NOW + dt.timedelta(microseconds=1),
    }
    reservation = DayDiscoveryCallReservationPayload.model_validate(
        reservation_payload
        | {
            "reservation_id": DayDiscoveryCallReservationPayload.canonical_id_for(
                reservation_payload
            )
        }
    )
    payload = {
        "event_id": "",
        "cycle_id": cycle.cycle_id,
        "sequence": 2,
        "previous_event_id": previous.event_id,
        "branch_index": 0,
        "event_kind": DayDiscoveryEventKind.CALL_RESERVED,
        "event_at": NOW + dt.timedelta(microseconds=1),
        "payload_json": canonical_experiment_ledger_json(reservation),
    }
    return DayDiscoveryEvent.model_validate(
        payload | {"event_id": DayDiscoveryEvent.canonical_id_for(payload)}
    )


def _event(
    cycle: DayDiscoveryCycle,
    previous: DayDiscoveryEvent,
    kind: DayDiscoveryEventKind,
    details: dict[str, object] | None = None,
) -> DayDiscoveryEvent:
    event_payload = {
        "event_id": "",
        "cycle_id": cycle.cycle_id,
        "sequence": previous.sequence + 1,
        "previous_event_id": previous.event_id,
        "branch_index": 0,
        "event_kind": kind,
        "event_at": previous.event_at + dt.timedelta(microseconds=1),
        "payload_json": json.dumps(details or {}, separators=(",", ":"), sort_keys=True),
    }
    return DayDiscoveryEvent.model_validate(
        event_payload | {"event_id": DayDiscoveryEvent.canonical_id_for(event_payload)}
    )


def _response_event(
    cycle: DayDiscoveryCycle,
    reserved: DayDiscoveryEvent,
    *,
    reservation_id: str | None = None,
) -> DayDiscoveryEvent:
    reservation = DayDiscoveryCallReservationPayload.model_validate_json(
        reserved.payload_json
    )
    raw = b'{"hypothesis":"bounded"}'
    received_at = reserved.event_at + dt.timedelta(microseconds=2)
    response = DayDiscoveryCallResponsePayload(
        reservation_id=reservation_id or reservation.reservation_id,
        response_base64=base64.b64encode(raw).decode("ascii"),
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_length=len(raw),
        invocation_started_at=reserved.event_at + dt.timedelta(microseconds=1),
        received_at=received_at,
    )
    payload = {
        "event_id": "",
        "cycle_id": cycle.cycle_id,
        "sequence": reserved.sequence + 1,
        "previous_event_id": reserved.event_id,
        "branch_index": 0,
        "event_kind": DayDiscoveryEventKind.CALL_RESPONSE_RECORDED,
        "event_at": received_at,
        "payload_json": canonical_experiment_ledger_json(response),
    }
    return DayDiscoveryEvent.model_validate(
        payload | {"event_id": DayDiscoveryEvent.canonical_id_for(payload)}
    )


def test_shared_budget_epoch_rejects_second_cursor_cycle_reservation(tmp_path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    account = _account()
    first_cycle = _cycle(account, "b" * 64)
    second_cycle = _cycle(account, "c" * 64)

    with store.writer() as writer:
        first_opened = writer.open_day_discovery_cycle(account, first_cycle)
        second_opened = writer.open_day_discovery_cycle(account, second_cycle)
        assert writer.reserve_day_discovery_call(
            _debit(account, first_cycle),
            _reservation_event(first_cycle, first_opened),
        )
        with pytest.raises(InvalidExperimentLedgerSourceError):
            writer.reserve_day_discovery_call(
                _debit(account, second_cycle),
                _reservation_event(second_cycle, second_opened),
            )


def test_budget_account_identity_is_stable_across_cycle_times() -> None:
    first = _account(limit=3)
    later_payload = first.model_dump(mode="python") | {
        "account_id": "",
        "created_at": NOW + dt.timedelta(hours=1),
    }
    later_id = DayDiscoveryBudgetAccount.canonical_id_for(later_payload)

    assert later_id == first.account_id


def test_reader_rejects_debit_index_payload_substitution(tmp_path) -> None:
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    account = _account(limit=2)
    cycle = _cycle(account, "b" * 64)
    with store.writer() as writer:
        opened = writer.open_day_discovery_cycle(account, cycle)
        assert writer.reserve_day_discovery_call(
            _debit(account, cycle),
            _reservation_event(cycle, opened),
        )
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER day_discovery_budget_debits_no_update")
        connection.execute("UPDATE day_discovery_budget_debits SET amount=2")
        connection.commit()

    with pytest.raises(InvalidExperimentLedgerSourceError):
        store.reader().day_discovery_cycle_state(cycle.cycle_id)


def test_prepared_branch_tops_up_only_cartesian_demand_minus_initial_debit(tmp_path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    account = _account(limit=2)
    cycle = _cycle(account, "b" * 64)
    with store.writer() as writer:
        opened = writer.open_day_discovery_cycle(account, cycle)
        reserved = _reservation_event(cycle, opened)
        assert writer.reserve_day_discovery_call(_debit(account, cycle), reserved)
        response = _response_event(cycle, reserved)
        assert writer.record_day_discovery_call_response(response)
        top_up_payload = {
            "debit_id": "",
            "account_id": account.account_id,
            "cycle_id": cycle.cycle_id,
            "branch_index": 0,
            "debit_kind": DayDiscoveryDebitKind.CARTESIAN_TOP_UP,
            "amount": 1,
            "debited_at": response.event_at + dt.timedelta(microseconds=1),
        }
        top_up = DayDiscoveryBudgetDebit.model_validate(
            top_up_payload
            | {"debit_id": DayDiscoveryBudgetDebit.canonical_id_for(top_up_payload)}
        )
        prepared = _event(
            cycle,
            response,
            DayDiscoveryEventKind.BRANCH_PREPARED,
            {
                "cartesian_demand": 2,
                "prepared": {"search_budget_debit": 2},
            },
        )
        assert writer.prepare_day_discovery_branch(top_up, prepared)

    state = store.reader().day_discovery_cycle_state(cycle.cycle_id)
    assert tuple(debit.amount for debit in state.debits) == (1, 1)
    assert state.remaining_budget == 0


@pytest.mark.parametrize("mutation", ("amount", "time"))
def test_prepared_branch_rejects_top_up_not_bound_to_planned_demand_and_event_time(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    account = _account(limit=4)
    cycle = _cycle(account, "b" * 64)
    with store.writer() as writer:
        opened = writer.open_day_discovery_cycle(account, cycle)
        reserved = _reservation_event(cycle, opened)
        writer.reserve_day_discovery_call(_debit(account, cycle), reserved)
        response = _response_event(cycle, reserved)
        writer.record_day_discovery_call_response(response)
        prepared = _event(
            cycle,
            response,
            DayDiscoveryEventKind.BRANCH_PREPARED,
            {
                "cartesian_demand": 3,
                "prepared": {"search_budget_debit": 3},
            },
        )
        debit_payload = {
            "debit_id": "",
            "account_id": account.account_id,
            "cycle_id": cycle.cycle_id,
            "branch_index": 0,
            "debit_kind": DayDiscoveryDebitKind.CARTESIAN_TOP_UP,
            "amount": 1 if mutation == "amount" else 2,
            "debited_at": (
                prepared.event_at + dt.timedelta(microseconds=1)
                if mutation == "time"
                else prepared.event_at
            ),
        }
        top_up = DayDiscoveryBudgetDebit.model_validate(
            debit_payload
            | {"debit_id": DayDiscoveryBudgetDebit.canonical_id_for(debit_payload)}
        )

        with pytest.raises(InvalidExperimentLedgerSourceError):
            writer.prepare_day_discovery_branch(top_up, prepared)


def test_prepared_branch_rejects_search_budget_debit_not_equal_to_cartesian_demand(
    tmp_path: Path,
) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    account = _account(limit=4)
    cycle = _cycle(account, "b" * 64)
    with store.writer() as writer:
        opened = writer.open_day_discovery_cycle(account, cycle)
        reserved = _reservation_event(cycle, opened)
        writer.reserve_day_discovery_call(_debit(account, cycle), reserved)
        response = _response_event(cycle, reserved)
        writer.record_day_discovery_call_response(response)
        prepared = _event(
            cycle,
            response,
            DayDiscoveryEventKind.BRANCH_PREPARED,
            {
                "cartesian_demand": 2,
                "prepared": {"search_budget_debit": 1},
            },
        )
        top_up_payload = {
            "debit_id": "",
            "account_id": account.account_id,
            "cycle_id": cycle.cycle_id,
            "branch_index": 0,
            "debit_kind": DayDiscoveryDebitKind.CARTESIAN_TOP_UP,
            "amount": 1,
            "debited_at": prepared.event_at,
        }
        top_up = DayDiscoveryBudgetDebit.model_validate(
            top_up_payload
            | {"debit_id": DayDiscoveryBudgetDebit.canonical_id_for(top_up_payload)}
        )

        with pytest.raises(InvalidExperimentLedgerSourceError):
            writer.prepare_day_discovery_branch(top_up, prepared)


def test_reader_rejects_rehashed_prepared_debit_cartesian_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    account = _account(limit=2)
    cycle = _cycle(account, "b" * 64)
    with store.writer() as writer:
        opened = writer.open_day_discovery_cycle(account, cycle)
        reserved = _reservation_event(cycle, opened)
        writer.reserve_day_discovery_call(_debit(account, cycle), reserved)
        response = _response_event(cycle, reserved)
        writer.record_day_discovery_call_response(response)
        prepared = _event(
            cycle,
            response,
            DayDiscoveryEventKind.BRANCH_PREPARED,
            {
                "cartesian_demand": 1,
                "prepared": {"search_budget_debit": 1},
            },
        )
        writer.prepare_day_discovery_branch(None, prepared)
    forged = _event(
        cycle,
        response,
        DayDiscoveryEventKind.BRANCH_PREPARED,
        {
            "cartesian_demand": 1,
            "prepared": {"search_budget_debit": 2},
        },
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER day_discovery_events_no_update")
        connection.execute(
            "UPDATE day_discovery_events SET event_id=?, payload_json=? WHERE event_id=?",
            (
                forged.event_id,
                canonical_experiment_ledger_json(forged),
                prepared.event_id,
            ),
        )
        connection.commit()

    with pytest.raises(InvalidExperimentLedgerSourceError):
        store.reader().day_discovery_cycle_state(cycle.cycle_id)


def test_reader_rejects_rehashed_illegal_event_transition(tmp_path) -> None:
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    account = _account(limit=2)
    cycle = _cycle(account, "b" * 64)
    with store.writer() as writer:
        opened = writer.open_day_discovery_cycle(account, cycle)
    illegal = _event(cycle, opened, DayDiscoveryEventKind.ARTIFACT_VERIFIED)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO day_discovery_events VALUES (?,?,?,?,?,?,?,?)",
            (
                illegal.event_id,
                illegal.cycle_id,
                illegal.sequence,
                illegal.previous_event_id,
                illegal.branch_index,
                illegal.event_kind.value,
                illegal.event_at.isoformat(),
                canonical_experiment_ledger_json(illegal),
            ),
        )
        connection.commit()

    with pytest.raises(InvalidExperimentLedgerSourceError):
        store.reader().day_discovery_cycle_state(cycle.cycle_id)


def test_call_reservation_rejects_missing_prompt_and_model_commitments(tmp_path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    account = _account(limit=2)
    cycle = _cycle(account, "b" * 64)
    with store.writer() as writer:
        opened = writer.open_day_discovery_cycle(account, cycle)
        invalid_payload = {
            "event_id": "",
            "cycle_id": cycle.cycle_id,
            "sequence": 2,
            "previous_event_id": opened.event_id,
            "branch_index": 0,
            "event_kind": DayDiscoveryEventKind.CALL_RESERVED,
            "event_at": NOW + dt.timedelta(microseconds=1),
            "payload_json": "{}",
        }
        invalid_event = DayDiscoveryEvent.model_validate(
            invalid_payload
            | {"event_id": DayDiscoveryEvent.canonical_id_for(invalid_payload)}
        )
        with pytest.raises(InvalidExperimentLedgerSourceError):
            writer.reserve_day_discovery_call(
                _debit(account, cycle),
                invalid_event,
            )


def test_call_response_rejects_a_different_reservation(tmp_path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    account = _account(limit=2)
    cycle = _cycle(account, "b" * 64)
    with store.writer() as writer:
        opened = writer.open_day_discovery_cycle(account, cycle)
        reserved = _reservation_event(cycle, opened)
        writer.reserve_day_discovery_call(_debit(account, cycle), reserved)
        with pytest.raises(InvalidExperimentLedgerSourceError):
            writer.record_day_discovery_call_response(
                _response_event(cycle, reserved, reservation_id="f" * 64)
            )


def test_effect_and_finalization_apis_append_one_legal_terminal_chain(tmp_path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    account = _account(limit=1)
    cycle = _cycle(account, "b" * 64)
    with store.writer() as writer:
        opened = writer.open_day_discovery_cycle(account, cycle)
        reserved = _reservation_event(cycle, opened)
        writer.reserve_day_discovery_call(_debit(account, cycle), reserved)
        response = _response_event(cycle, reserved)
        writer.record_day_discovery_call_response(response)
        prepared = _event(
            cycle,
            response,
            DayDiscoveryEventKind.BRANCH_PREPARED,
            {
                "cartesian_demand": 1,
                "prepared": {"search_budget_debit": 1},
            },
        )
        writer.prepare_day_discovery_branch(None, prepared)
        resolution_intent = _event(
            cycle,
            prepared,
            DayDiscoveryEventKind.RESOLUTION_INTENT,
        )
        writer.start_day_discovery_effect(resolution_intent)
        artifact = _event(
            cycle,
            resolution_intent,
            DayDiscoveryEventKind.ARTIFACT_VERIFIED,
        )
        writer.finalize_day_discovery_effect(artifact)
        preflight_intent = _event(
            cycle,
            artifact,
            DayDiscoveryEventKind.PREFLIGHT_INTENT,
        )
        writer.start_day_discovery_effect(preflight_intent)
        preflight = _event(
            cycle,
            preflight_intent,
            DayDiscoveryEventKind.PREFLIGHT_VERIFIED,
        )
        writer.finalize_day_discovery_effect(preflight)
        branch = _event(cycle, preflight, DayDiscoveryEventKind.BRANCH_FINALIZED)
        writer.finalize_day_discovery_branch(branch)
        finalized = _event(cycle, branch, DayDiscoveryEventKind.CYCLE_FINALIZED)
        writer.finalize_day_discovery_cycle(finalized)

    state = store.reader().day_discovery_cycle_state(cycle.cycle_id)
    assert state.events[-1].event_kind is DayDiscoveryEventKind.CYCLE_FINALIZED
    assert state.remaining_budget == 0
