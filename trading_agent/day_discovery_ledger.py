from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel

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

_ALLOWED_TRANSITIONS: Final = {
    DayDiscoveryEventKind.CYCLE_OPENED: frozenset({DayDiscoveryEventKind.CALL_RESERVED}),
    DayDiscoveryEventKind.CALL_RESERVED: frozenset(
        {DayDiscoveryEventKind.CALL_RESPONSE_RECORDED, DayDiscoveryEventKind.BRANCH_FINALIZED}
    ),
    DayDiscoveryEventKind.CALL_RESPONSE_RECORDED: frozenset(
        {DayDiscoveryEventKind.BRANCH_PREPARED, DayDiscoveryEventKind.BRANCH_FINALIZED}
    ),
    DayDiscoveryEventKind.BRANCH_PREPARED: frozenset(
        {DayDiscoveryEventKind.RESOLUTION_INTENT, DayDiscoveryEventKind.BRANCH_FINALIZED}
    ),
    DayDiscoveryEventKind.RESOLUTION_INTENT: frozenset(
        {
            DayDiscoveryEventKind.ARTIFACT_VERIFIED,
            DayDiscoveryEventKind.ARTIFACT_FAILED,
            DayDiscoveryEventKind.ARTIFACT_OUTCOME_UNKNOWN,
        }
    ),
    DayDiscoveryEventKind.ARTIFACT_VERIFIED: frozenset({DayDiscoveryEventKind.PREFLIGHT_INTENT}),
    DayDiscoveryEventKind.PREFLIGHT_INTENT: frozenset(
        {
            DayDiscoveryEventKind.PREFLIGHT_VERIFIED,
            DayDiscoveryEventKind.PREFLIGHT_FAILED,
            DayDiscoveryEventKind.PREFLIGHT_OUTCOME_UNKNOWN,
        }
    ),
    DayDiscoveryEventKind.ARTIFACT_FAILED: frozenset({DayDiscoveryEventKind.BRANCH_FINALIZED}),
    DayDiscoveryEventKind.ARTIFACT_OUTCOME_UNKNOWN: frozenset({DayDiscoveryEventKind.BRANCH_FINALIZED}),
    DayDiscoveryEventKind.PREFLIGHT_VERIFIED: frozenset({DayDiscoveryEventKind.BRANCH_FINALIZED}),
    DayDiscoveryEventKind.PREFLIGHT_FAILED: frozenset({DayDiscoveryEventKind.BRANCH_FINALIZED}),
    DayDiscoveryEventKind.PREFLIGHT_OUTCOME_UNKNOWN: frozenset({DayDiscoveryEventKind.BRANCH_FINALIZED}),
    DayDiscoveryEventKind.BRANCH_FINALIZED: frozenset(
        {DayDiscoveryEventKind.CALL_RESERVED, DayDiscoveryEventKind.CYCLE_FINALIZED}
    ),
    DayDiscoveryEventKind.CYCLE_FINALIZED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class DayDiscoveryLedgerConflictError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class InvalidDayDiscoveryLedgerSourceError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class DayDiscoveryCycleState:
    account: DayDiscoveryBudgetAccount
    cycle: DayDiscoveryCycle
    debits: tuple[DayDiscoveryBudgetDebit, ...]
    events: tuple[DayDiscoveryEvent, ...]
    remaining_budget: int


def open_day_discovery_cycle(
    connection: sqlite3.Connection,
    account: DayDiscoveryBudgetAccount,
    cycle: DayDiscoveryCycle,
) -> DayDiscoveryEvent:
    checked_account = _validated(account, DayDiscoveryBudgetAccount, "budget_account_invalid")
    checked_cycle = _validated(cycle, DayDiscoveryCycle, "cycle_invalid")
    if (
        checked_cycle.account_id != checked_account.account_id
        or checked_cycle.market_id is not checked_account.market_id
    ):
        raise InvalidDayDiscoveryLedgerSourceError("cycle_account_mismatch")
    account_row: tuple[str] | None = connection.execute(
        "SELECT payload_json FROM day_discovery_budget_accounts WHERE account_id=?",
        (checked_account.account_id,),
    ).fetchone()
    if account_row is None:
        _insert_or_replay(
            connection,
            "day_discovery_budget_accounts",
            "account_id",
            checked_account.account_id,
            "INSERT INTO day_discovery_budget_accounts VALUES (?,?,?,?,?,?)",
            (
                checked_account.account_id,
                checked_account.market_id.value,
                checked_account.budget_epoch_ref,
                checked_account.debit_limit,
                checked_account.created_at.isoformat(),
                canonical_experiment_ledger_json(checked_account),
            ),
            canonical_experiment_ledger_json(checked_account),
        )
    else:
        stored_account = _parse(
            account_row[0],
            DayDiscoveryBudgetAccount,
            "budget_account_invalid",
        )
        if (
            stored_account.account_id != checked_account.account_id
            or stored_account.market_id is not checked_account.market_id
            or stored_account.budget_epoch_ref != checked_account.budget_epoch_ref
            or stored_account.debit_limit != checked_account.debit_limit
        ):
            raise DayDiscoveryLedgerConflictError("budget_account_replay_conflict")
    cursor_row: tuple[str, str] | None = connection.execute(
        "SELECT cycle_id,evidence_sha256 FROM day_discovery_cycles "
        "WHERE account_id=? AND cursor_sha256=?",
        (checked_cycle.account_id, checked_cycle.cursor_sha256),
    ).fetchone()
    if cursor_row is not None and cursor_row != (
        checked_cycle.cycle_id,
        checked_cycle.evidence_sha256,
    ):
        raise DayDiscoveryLedgerConflictError("cycle_cursor_evidence_conflict")
    _insert_or_replay(
        connection,
        "day_discovery_cycles",
        "cycle_id",
        checked_cycle.cycle_id,
        "INSERT INTO day_discovery_cycles VALUES (?,?,?,?,?,?,?)",
        (
            checked_cycle.cycle_id,
            checked_cycle.account_id,
            checked_cycle.market_id.value,
            checked_cycle.evidence_sha256,
            checked_cycle.cursor_sha256,
            checked_cycle.opened_at.isoformat(),
            canonical_experiment_ledger_json(checked_cycle),
        ),
        canonical_experiment_ledger_json(checked_cycle),
    )
    existing = _events(connection, checked_cycle.cycle_id)
    if existing:
        if len(existing) != 1 or existing[0].event_kind is not DayDiscoveryEventKind.CYCLE_OPENED:
            raise DayDiscoveryLedgerConflictError("cycle_already_advanced")
        return existing[0]
    payload_json = json.dumps(
        {"account_id": checked_account.account_id, "cycle_id": checked_cycle.cycle_id},
        separators=(",", ":"),
        sort_keys=True,
    )
    event_payload = {
        "event_id": "",
        "cycle_id": checked_cycle.cycle_id,
        "sequence": 1,
        "previous_event_id": None,
        "branch_index": None,
        "event_kind": DayDiscoveryEventKind.CYCLE_OPENED,
        "event_at": checked_cycle.opened_at,
        "payload_json": payload_json,
    }
    event = DayDiscoveryEvent.model_validate(
        event_payload | {"event_id": DayDiscoveryEvent.canonical_id_for(event_payload)}
    )
    _insert_event(connection, event)
    return event


def reserve_day_discovery_call(
    connection: sqlite3.Connection,
    debit: DayDiscoveryBudgetDebit,
    event: DayDiscoveryEvent,
) -> bool:
    checked_debit = _validated(debit, DayDiscoveryBudgetDebit, "debit_invalid")
    checked_event = _validated(event, DayDiscoveryEvent, "event_invalid")
    reservation = _parse_event_payload(
        checked_event,
        DayDiscoveryCallReservationPayload,
        "call_reservation_payload_invalid",
    )
    if (
        checked_event.event_kind is not DayDiscoveryEventKind.CALL_RESERVED
        or checked_event.cycle_id != checked_debit.cycle_id
        or checked_event.branch_index != checked_debit.branch_index
        or checked_debit.debit_kind is not DayDiscoveryDebitKind.CALL_RESERVATION
        or checked_debit.amount != 1
        or reservation.account_id != checked_debit.account_id
        or reservation.cycle_id != checked_debit.cycle_id
        or reservation.branch_index != checked_debit.branch_index
        or reservation.reserved_at != checked_event.event_at
        or checked_debit.debited_at != reservation.reserved_at
    ):
        raise InvalidDayDiscoveryLedgerSourceError("call_reservation_invalid")
    cycle = _cycle(connection, checked_debit.cycle_id)
    account = _account(connection, checked_debit.account_id)
    if cycle.account_id != account.account_id or cycle.market_id is not account.market_id:
        raise InvalidDayDiscoveryLedgerSourceError("debit_account_mismatch")
    existing = connection.execute(
        "SELECT payload_json FROM day_discovery_budget_debits "
        "WHERE cycle_id=? AND branch_index=? AND debit_kind=?",
        (checked_debit.cycle_id, checked_debit.branch_index, checked_debit.debit_kind.value),
    ).fetchone()
    if existing is not None:
        if existing[0] != canonical_experiment_ledger_json(checked_debit):
            raise DayDiscoveryLedgerConflictError("debit_replay_conflict")
        _require_event_replay(connection, checked_event)
        return False
    used: tuple[int | None] = connection.execute(
        "SELECT SUM(amount) FROM day_discovery_budget_debits WHERE account_id=?",
        (account.account_id,),
    ).fetchone()
    if (used[0] or 0) + checked_debit.amount > account.debit_limit:
        raise InvalidDayDiscoveryLedgerSourceError("discovery_budget_exhausted")
    _require_next_event(connection, checked_event)
    connection.execute(
        "INSERT INTO day_discovery_budget_debits VALUES (?,?,?,?,?,?,?,?)",
        (
            checked_debit.debit_id,
            checked_debit.account_id,
            checked_debit.cycle_id,
            checked_debit.branch_index,
            checked_debit.debit_kind.value,
            checked_debit.amount,
            checked_debit.debited_at.isoformat(),
            canonical_experiment_ledger_json(checked_debit),
        ),
    )
    _insert_event(connection, checked_event)
    return True


def append_day_discovery_event(
    connection: sqlite3.Connection,
    event: DayDiscoveryEvent,
    expected_kind: DayDiscoveryEventKind,
) -> bool:
    checked = _validated(event, DayDiscoveryEvent, "event_invalid")
    if checked.event_kind is not expected_kind:
        raise InvalidDayDiscoveryLedgerSourceError("event_kind_invalid")
    row: tuple[str] | None = connection.execute(
        "SELECT payload_json FROM day_discovery_events WHERE event_id=?",
        (checked.event_id,),
    ).fetchone()
    if row is not None:
        if row[0] == canonical_experiment_ledger_json(checked):
            return False
        raise DayDiscoveryLedgerConflictError("event_replay_conflict")
    _require_next_event(connection, checked)
    _insert_event(connection, checked)
    return True


def record_day_discovery_call_response(
    connection: sqlite3.Connection,
    event: DayDiscoveryEvent,
) -> bool:
    checked = _validated(event, DayDiscoveryEvent, "event_invalid")
    if checked.event_kind is not DayDiscoveryEventKind.CALL_RESPONSE_RECORDED:
        raise InvalidDayDiscoveryLedgerSourceError("call_response_event_invalid")
    response = _parse_event_payload(
        checked,
        DayDiscoveryCallResponsePayload,
        "call_response_payload_invalid",
    )
    previous = _previous_event(connection, checked)
    reservation = _parse_event_payload(
        previous,
        DayDiscoveryCallReservationPayload,
        "call_reservation_payload_invalid",
    )
    if (
        response.reservation_id != reservation.reservation_id
        or response.invocation_started_at < reservation.reserved_at
        or response.received_at != checked.event_at
    ):
        raise InvalidDayDiscoveryLedgerSourceError("call_response_reservation_mismatch")
    return append_day_discovery_event(
        connection,
        checked,
        DayDiscoveryEventKind.CALL_RESPONSE_RECORDED,
    )


def prepare_day_discovery_branch(
    connection: sqlite3.Connection,
    debit: DayDiscoveryBudgetDebit | None,
    event: DayDiscoveryEvent,
) -> bool:
    checked = _validated(event, DayDiscoveryEvent, "event_invalid")
    if checked.event_kind is not DayDiscoveryEventKind.BRANCH_PREPARED:
        raise InvalidDayDiscoveryLedgerSourceError("prepared_event_invalid")
    _require_next_event(connection, checked)
    try:
        prepared_payload = json.loads(checked.payload_json)
        cartesian_demand = prepared_payload["cartesian_demand"]
    except (KeyError, TypeError, ValueError):
        raise InvalidDayDiscoveryLedgerSourceError("prepared_payload_invalid") from None
    if not isinstance(cartesian_demand, int) or isinstance(cartesian_demand, bool) or cartesian_demand < 1:
        raise InvalidDayDiscoveryLedgerSourceError("prepared_cartesian_demand_invalid")
    expected_top_up = cartesian_demand - 1
    if (debit is None) != (expected_top_up == 0):
        raise InvalidDayDiscoveryLedgerSourceError("prepared_debit_invalid")
    if debit is not None:
        checked_debit = _validated(debit, DayDiscoveryBudgetDebit, "debit_invalid")
        if (
            checked_debit.debit_kind is not DayDiscoveryDebitKind.CARTESIAN_TOP_UP
            or checked_debit.cycle_id != checked.cycle_id
            or checked_debit.branch_index != checked.branch_index
            or checked_debit.amount != expected_top_up
            or checked_debit.debited_at != checked.event_at
        ):
            raise InvalidDayDiscoveryLedgerSourceError("prepared_debit_invalid")
        account = _account(connection, checked_debit.account_id)
        cycle = _cycle(connection, checked_debit.cycle_id)
        if cycle.account_id != account.account_id:
            raise InvalidDayDiscoveryLedgerSourceError("prepared_debit_account_mismatch")
        used: tuple[int | None] = connection.execute(
            "SELECT SUM(amount) FROM day_discovery_budget_debits WHERE account_id=?",
            (account.account_id,),
        ).fetchone()
        if (used[0] or 0) + checked_debit.amount > account.debit_limit:
            raise InvalidDayDiscoveryLedgerSourceError("discovery_budget_exhausted")
        connection.execute(
            "INSERT INTO day_discovery_budget_debits VALUES (?,?,?,?,?,?,?,?)",
            (
                checked_debit.debit_id,
                checked_debit.account_id,
                checked_debit.cycle_id,
                checked_debit.branch_index,
                checked_debit.debit_kind.value,
                checked_debit.amount,
                checked_debit.debited_at.isoformat(),
                canonical_experiment_ledger_json(checked_debit),
            ),
        )
    _insert_event(connection, checked)
    return True


def read_day_discovery_cycle_state(
    connection: sqlite3.Connection,
    cycle_id: str,
) -> DayDiscoveryCycleState:
    cycle_row: tuple[str, str, str, str, str, str, str] | None = connection.execute(
        "SELECT cycle_id,account_id,market_id,evidence_sha256,cursor_sha256,opened_at,payload_json "
        "FROM day_discovery_cycles WHERE cycle_id=?",
        (cycle_id,),
    ).fetchone()
    if cycle_row is None:
        raise InvalidDayDiscoveryLedgerSourceError("cycle_missing")
    cycle = _parse(cycle_row[6], DayDiscoveryCycle, "cycle_invalid")
    if cycle_row[:6] != (
        cycle.cycle_id,
        cycle.account_id,
        cycle.market_id.value,
        cycle.evidence_sha256,
        cycle.cursor_sha256,
        cycle.opened_at.isoformat(),
    ):
        raise InvalidDayDiscoveryLedgerSourceError("cycle_index_invalid")
    account_row: tuple[str, str, str, int, str, str] | None = connection.execute(
        "SELECT account_id,market_id,budget_epoch_ref,debit_limit,created_at,payload_json "
        "FROM day_discovery_budget_accounts WHERE account_id=?",
        (cycle.account_id,),
    ).fetchone()
    if account_row is None:
        raise InvalidDayDiscoveryLedgerSourceError("budget_account_missing")
    account = _parse(account_row[5], DayDiscoveryBudgetAccount, "budget_account_invalid")
    if account_row[:5] != (
        account.account_id,
        account.market_id.value,
        account.budget_epoch_ref,
        account.debit_limit,
        account.created_at.isoformat(),
    ) or cycle.market_id is not account.market_id:
        raise InvalidDayDiscoveryLedgerSourceError("budget_account_index_invalid")
    debit_rows: list[tuple[str, str, str, int, str, int, str, str]] = connection.execute(
        "SELECT debit_id,account_id,cycle_id,branch_index,debit_kind,amount,debited_at,payload_json "
        "FROM day_discovery_budget_debits WHERE account_id=? ORDER BY debited_at,debit_id",
        (account.account_id,),
    ).fetchall()
    all_debits = tuple(
        _parse(row[7], DayDiscoveryBudgetDebit, "debit_invalid") for row in debit_rows
    )
    for row, debit in zip(debit_rows, all_debits, strict=True):
        if row[:7] != (
            debit.debit_id,
            debit.account_id,
            debit.cycle_id,
            debit.branch_index,
            debit.debit_kind.value,
            debit.amount,
            debit.debited_at.isoformat(),
        ) or debit.account_id != account.account_id:
            raise InvalidDayDiscoveryLedgerSourceError("debit_index_invalid")
    used = sum(debit.amount for debit in all_debits)
    if used > account.debit_limit:
        raise InvalidDayDiscoveryLedgerSourceError("discovery_budget_invalid")
    cycle_debits = tuple(debit for debit in all_debits if debit.cycle_id == cycle.cycle_id)
    events = _audited_events(connection, cycle, cycle_debits)
    return DayDiscoveryCycleState(
        account=account,
        cycle=cycle,
        debits=cycle_debits,
        events=events,
        remaining_budget=account.debit_limit - used,
    )


def _validated[ModelT: DayDiscoveryBudgetAccount | DayDiscoveryCycle | DayDiscoveryBudgetDebit | DayDiscoveryEvent](
    value: ModelT,
    model: type[ModelT],
    reason: str,
) -> ModelT:
    try:
        return model.model_validate(value.model_dump(mode="python"))
    except ValueError:
        raise InvalidDayDiscoveryLedgerSourceError(reason) from None


def _account(connection: sqlite3.Connection, account_id: str) -> DayDiscoveryBudgetAccount:
    row: tuple[str] | None = connection.execute(
        "SELECT payload_json FROM day_discovery_budget_accounts WHERE account_id=?",
        (account_id,),
    ).fetchone()
    if row is None:
        raise InvalidDayDiscoveryLedgerSourceError("budget_account_missing")
    return _parse(row[0], DayDiscoveryBudgetAccount, "budget_account_invalid")


def _cycle(connection: sqlite3.Connection, cycle_id: str) -> DayDiscoveryCycle:
    row: tuple[str] | None = connection.execute(
        "SELECT payload_json FROM day_discovery_cycles WHERE cycle_id=?",
        (cycle_id,),
    ).fetchone()
    if row is None:
        raise InvalidDayDiscoveryLedgerSourceError("cycle_missing")
    return _parse(row[0], DayDiscoveryCycle, "cycle_invalid")


def _events(connection: sqlite3.Connection, cycle_id: str) -> tuple[DayDiscoveryEvent, ...]:
    rows: list[tuple[str]] = connection.execute(
        "SELECT payload_json FROM day_discovery_events WHERE cycle_id=? ORDER BY sequence",
        (cycle_id,),
    ).fetchall()
    return tuple(_parse(row[0], DayDiscoveryEvent, "event_invalid") for row in rows)


def _parse[
    ModelT: DayDiscoveryBudgetAccount
    | DayDiscoveryCycle
    | DayDiscoveryBudgetDebit
    | DayDiscoveryEvent
](
    payload: str,
    model: type[ModelT],
    reason: str,
) -> ModelT:
    try:
        value = model.model_validate_json(payload)
    except ValueError:
        raise InvalidDayDiscoveryLedgerSourceError(reason) from None
    if payload != canonical_experiment_ledger_json(value):
        raise InvalidDayDiscoveryLedgerSourceError(reason)
    return value


def _parse_event_payload[PayloadT: BaseModel](
    event: DayDiscoveryEvent,
    model: type[PayloadT],
    reason: str,
) -> PayloadT:
    try:
        value = model.model_validate_json(event.payload_json)
    except ValueError:
        raise InvalidDayDiscoveryLedgerSourceError(reason) from None
    if event.payload_json != canonical_experiment_ledger_json(value):
        raise InvalidDayDiscoveryLedgerSourceError(reason)
    return value


def _audited_events(
    connection: sqlite3.Connection,
    cycle: DayDiscoveryCycle,
    debits: tuple[DayDiscoveryBudgetDebit, ...],
) -> tuple[DayDiscoveryEvent, ...]:
    rows: list[tuple[str, str, int, str | None, int | None, str, str, str]] = connection.execute(
        "SELECT event_id,cycle_id,sequence,previous_event_id,branch_index,event_kind,event_at,payload_json "
        "FROM day_discovery_events WHERE cycle_id=? ORDER BY sequence",
        (cycle.cycle_id,),
    ).fetchall()
    events = tuple(_parse(row[7], DayDiscoveryEvent, "event_invalid") for row in rows)
    previous: DayDiscoveryEvent | None = None
    for row, event in zip(rows, events, strict=True):
        if row[:7] != (
            event.event_id,
            event.cycle_id,
            event.sequence,
            event.previous_event_id,
            event.branch_index,
            event.event_kind.value,
            event.event_at.isoformat(),
        ):
            raise InvalidDayDiscoveryLedgerSourceError("event_index_invalid")
        if event.cycle_id != cycle.cycle_id or event.sequence != (1 if previous is None else previous.sequence + 1):
            raise InvalidDayDiscoveryLedgerSourceError("event_chain_invalid")
        if previous is None:
            if event.event_kind is not DayDiscoveryEventKind.CYCLE_OPENED or event.previous_event_id is not None:
                raise InvalidDayDiscoveryLedgerSourceError("event_chain_invalid")
        elif event.previous_event_id != previous.event_id or event.event_at < previous.event_at:
            raise InvalidDayDiscoveryLedgerSourceError("event_chain_invalid")
        elif event.event_kind not in _ALLOWED_TRANSITIONS[previous.event_kind]:
            raise InvalidDayDiscoveryLedgerSourceError("event_transition_invalid")
        previous = event
    if not events:
        raise InvalidDayDiscoveryLedgerSourceError("event_chain_missing")
    _audit_call_events(events, all_debits=debits)
    _audit_prepared_events(events, all_debits=debits)
    return events


def _audit_call_events(
    events: tuple[DayDiscoveryEvent, ...],
    *,
    all_debits: tuple[DayDiscoveryBudgetDebit, ...],
) -> None:
    reservations: dict[int, DayDiscoveryCallReservationPayload] = {}
    for event in events:
        if event.event_kind is DayDiscoveryEventKind.CALL_RESERVED:
            reservation = _parse_event_payload(
                event,
                DayDiscoveryCallReservationPayload,
                "call_reservation_payload_invalid",
            )
            if (
                reservation.cycle_id != event.cycle_id
                or reservation.branch_index != event.branch_index
                or reservation.reserved_at != event.event_at
            ):
                raise InvalidDayDiscoveryLedgerSourceError("call_reservation_payload_invalid")
            reservations[reservation.branch_index] = reservation
        elif event.event_kind is DayDiscoveryEventKind.CALL_RESPONSE_RECORDED:
            response = _parse_event_payload(
                event,
                DayDiscoveryCallResponsePayload,
                "call_response_payload_invalid",
            )
            reservation = reservations.get(event.branch_index or 0)
            if (
                reservation is None
                or response.reservation_id != reservation.reservation_id
                or response.invocation_started_at < reservation.reserved_at
                or response.received_at != event.event_at
            ):
                raise InvalidDayDiscoveryLedgerSourceError("call_response_reservation_mismatch")
    if all_debits:
        reservation_debits = {
            debit.branch_index: debit
            for debit in all_debits
            if debit.debit_kind is DayDiscoveryDebitKind.CALL_RESERVATION
        }
        if set(reservations) != set(reservation_debits):
            raise InvalidDayDiscoveryLedgerSourceError("call_reservation_debit_mismatch")
        for branch_index, reservation in reservations.items():
            debit = reservation_debits[branch_index]
            if (
                debit.account_id != reservation.account_id
                or debit.cycle_id != reservation.cycle_id
                or debit.amount != 1
                or debit.debited_at != reservation.reserved_at
            ):
                raise InvalidDayDiscoveryLedgerSourceError("call_reservation_debit_mismatch")


def _audit_prepared_events(
    events: tuple[DayDiscoveryEvent, ...],
    *,
    all_debits: tuple[DayDiscoveryBudgetDebit, ...],
) -> None:
    top_ups = {
        debit.branch_index: debit
        for debit in all_debits
        if debit.debit_kind is DayDiscoveryDebitKind.CARTESIAN_TOP_UP
    }
    expected_top_up_branches: set[int] = set()
    for event in events:
        if event.event_kind is not DayDiscoveryEventKind.BRANCH_PREPARED:
            continue
        try:
            payload = json.loads(event.payload_json)
            demand = payload["cartesian_demand"]
        except (KeyError, TypeError, ValueError):
            raise InvalidDayDiscoveryLedgerSourceError("prepared_payload_invalid") from None
        branch_index = event.branch_index
        if (
            branch_index is None
            or not isinstance(demand, int)
            or isinstance(demand, bool)
            or demand < 1
        ):
            raise InvalidDayDiscoveryLedgerSourceError("prepared_cartesian_demand_invalid")
        top_up = top_ups.get(branch_index)
        if demand == 1:
            if top_up is not None:
                raise InvalidDayDiscoveryLedgerSourceError("prepared_debit_invalid")
        elif (
            top_up is None
            or top_up.amount != demand - 1
            or top_up.debited_at != event.event_at
        ):
            raise InvalidDayDiscoveryLedgerSourceError("prepared_debit_invalid")
        if demand > 1:
            expected_top_up_branches.add(branch_index)
    if set(top_ups) != expected_top_up_branches:
        raise InvalidDayDiscoveryLedgerSourceError("prepared_debit_invalid")


def _require_next_event(connection: sqlite3.Connection, event: DayDiscoveryEvent) -> None:
    events = _events(connection, event.cycle_id)
    previous = events[-1] if events else None
    if previous is None or event.sequence != previous.sequence + 1 or event.previous_event_id != previous.event_id:
        raise InvalidDayDiscoveryLedgerSourceError("event_chain_invalid")
    if event.event_at < previous.event_at:
        raise InvalidDayDiscoveryLedgerSourceError("event_time_invalid")
    allowed = _ALLOWED_TRANSITIONS.get(previous.event_kind)
    if allowed is None or event.event_kind not in allowed:
        raise InvalidDayDiscoveryLedgerSourceError("event_transition_invalid")


def _previous_event(
    connection: sqlite3.Connection,
    event: DayDiscoveryEvent,
) -> DayDiscoveryEvent:
    events = _events(connection, event.cycle_id)
    if not events or event.previous_event_id != events[-1].event_id:
        raise InvalidDayDiscoveryLedgerSourceError("event_chain_invalid")
    return events[-1]


def _require_event_replay(connection: sqlite3.Connection, event: DayDiscoveryEvent) -> None:
    row: tuple[str] | None = connection.execute(
        "SELECT payload_json FROM day_discovery_events WHERE event_id=?",
        (event.event_id,),
    ).fetchone()
    if row is None or row[0] != canonical_experiment_ledger_json(event):
        raise DayDiscoveryLedgerConflictError("event_replay_conflict")


def _insert_event(connection: sqlite3.Connection, event: DayDiscoveryEvent) -> None:
    connection.execute(
        "INSERT INTO day_discovery_events VALUES (?,?,?,?,?,?,?,?)",
        (
            event.event_id,
            event.cycle_id,
            event.sequence,
            event.previous_event_id,
            event.branch_index,
            event.event_kind.value,
            event.event_at.isoformat(),
            canonical_experiment_ledger_json(event),
        ),
    )


def _insert_or_replay(
    connection: sqlite3.Connection,
    table: str,
    key_column: str,
    key: str,
    insert_sql: str,
    values: tuple[str | int, ...],
    payload: str,
) -> bool:
    row: tuple[str] | None = connection.execute(
        f"SELECT payload_json FROM {table} WHERE {key_column}=?",
        (key,),
    ).fetchone()
    if row is not None:
        if row[0] == payload:
            return False
        raise DayDiscoveryLedgerConflictError("immutable_identity_conflict")
    connection.execute(insert_sql, values)
    return True


__all__ = (
    "DayDiscoveryCycleState",
    "DayDiscoveryLedgerConflictError",
    "InvalidDayDiscoveryLedgerSourceError",
    "append_day_discovery_event",
    "open_day_discovery_cycle",
    "prepare_day_discovery_branch",
    "read_day_discovery_cycle_state",
    "record_day_discovery_call_response",
    "reserve_day_discovery_call",
)
