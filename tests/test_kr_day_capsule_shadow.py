from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Literal, assert_never

import pytest

from tests.test_kr_day_capsule_adapter import _request
from trading_agent.kr_day_capsule_adapter import adapt_kr_day_capsule_evaluation
from trading_agent.kr_day_capsule_models import (
    KrDayCapsuleEvaluation,
    KrDayCapsuleEvaluationPayload,
    KrDayCapsuleEvaluationRequest,
)
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowEventPayload,
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_capsule_shadow_service import (
    InvalidKrDayCapsuleShadowServiceError,
    run_kr_day_capsule_shadow_tick,
)
from trading_agent.kr_day_capsule_shadow_store import (
    InvalidKrDayCapsuleShadowStoreError,
    KrDayCapsuleShadowStore,
)
from trading_agent.kr_intraday_market_gate import KrMarketConstraintSnapshot
from trading_agent.kr_theme_day_setup import KrCompletedMinuteBar, KrThemeDaySetupInput
from trading_agent.signal_contract_models import EvidenceRef


def test_entry_restart_and_same_bar_stop_first_are_append_only(tmp_path: Path) -> None:
    store = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    entry_evaluation = _entry_evaluation()

    entry = run_kr_day_capsule_shadow_tick(store, (entry_evaluation,)).results[0]
    replay = run_kr_day_capsule_shadow_tick(store, (entry_evaluation,)).results[0]
    collision = run_kr_day_capsule_shadow_tick(
        store,
        (_advance(entry_evaluation, low=Decimal("9900"), high=Decimal("10400")),),
    ).results[0]

    assert entry.created is True
    assert entry.event.status is KrDayCapsuleShadowStatus.ACTIVE
    assert entry.event.entry_price == Decimal("10180.320")
    assert entry.event.trading_authority is False
    assert replay.created is False
    assert replay.event.event_id == entry.event.event_id
    assert collision.event.status is KrDayCapsuleShadowStatus.STOPPED
    assert collision.event.reason is KrDayCapsuleShadowReason.STOP_FIRST
    assert len(store.events()) == 2


def test_no_signal_registers_and_target_is_terminal_noop(tmp_path: Path) -> None:
    no_signal_store = KrDayCapsuleShadowStore(tmp_path / "registered.sqlite3")
    active_store = KrDayCapsuleShadowStore(tmp_path / "active.sqlite3")

    registered = run_kr_day_capsule_shadow_tick(no_signal_store, (_plain_evaluation(),)).results[0]
    _ = run_kr_day_capsule_shadow_tick(active_store, (_entry_evaluation(),))
    targeted_evaluation = _advance(_entry_evaluation(), low=Decimal("10000"), high=Decimal("10400"))
    targeted = run_kr_day_capsule_shadow_tick(active_store, (targeted_evaluation,)).results[0]
    terminal_noop = run_kr_day_capsule_shadow_tick(
        active_store,
        (_advance(targeted_evaluation, low=Decimal("10000"), high=Decimal("10400")),),
    ).results[0]

    assert registered.event.status is KrDayCapsuleShadowStatus.REGISTERED
    assert targeted.event.status is KrDayCapsuleShadowStatus.TARGETED
    assert terminal_noop.created is False
    assert terminal_noop.event.event_id == targeted.event.event_id
    assert len(active_store.events()) == 2


def test_gap_censors_without_advancing_cursor(tmp_path: Path) -> None:
    store = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    evaluation = _entry_evaluation()
    active = run_kr_day_capsule_shadow_tick(store, (evaluation,)).results[0].event

    censored = run_kr_day_capsule_shadow_tick(store, (_advance(evaluation, count=2),)).results[0].event

    assert censored.status is KrDayCapsuleShadowStatus.CENSORED
    assert censored.reason is KrDayCapsuleShadowReason.BAR_GAP
    assert censored.accepted_bar_cursor == active.accepted_bar_cursor
    assert censored.accepted_bar_cursor != censored.attempted_bar_cursor


@pytest.mark.parametrize(
    "field",
    ("symbol", "collection_cycle_id", "calendar_snapshot_id"),
)
def test_active_management_rejects_substituted_lineage_without_append(
    field: Literal["symbol", "collection_cycle_id", "calendar_snapshot_id"],
    tmp_path: Path,
) -> None:
    # Given: an ACTIVE position and a next-bar evaluation with substituted lineage.
    store = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    entry = _entry_evaluation()
    _ = run_kr_day_capsule_shadow_tick(store, (entry,))
    match field:
        case "symbol":
            substituted = _rebuild(_advance(entry), symbol="000660")
        case "collection_cycle_id":
            substituted = _rebuild(
                _advance(entry),
                collection_cycle_id="kr-cycle-20260824-substitute",
            )
        case "calendar_snapshot_id":
            substituted = _rebuild(_advance(entry), calendar_snapshot_id="f" * 64)
        case unreachable:
            assert_never(unreachable)

    # When / Then: management rejects before appending any lifecycle event.
    with pytest.raises(InvalidKrDayCapsuleShadowServiceError):
        _ = run_kr_day_capsule_shadow_tick(store, (substituted,))
    assert len(store.events()) == 1


def test_batch_orders_capsules_and_isolates_failed_sibling(tmp_path: Path) -> None:
    store = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    valid = _entry_evaluation()
    failed = _invalid_signal_projection(_reidentify(valid, "f" * 64))

    result = run_kr_day_capsule_shadow_tick(store, (failed, valid))

    assert tuple(item.event.capsule_id for item in result.results) == tuple(
        sorted((valid.capsule_id, failed.capsule_id))
    )
    assert result.results[0].event.status is KrDayCapsuleShadowStatus.ACTIVE
    assert result.results[1].event.status is KrDayCapsuleShadowStatus.FAILED
    assert len(store.events()) == 2


def test_active_management_ignores_divergent_nonactive_batch_anchor(tmp_path: Path) -> None:
    # Given: a lower-sorting non-ACTIVE sibling with a different calendar snapshot.
    store = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    active_capsule_id = "f" * 64
    sibling_capsule_id = "0" * 64
    active_entry = _reidentify(_entry_evaluation(), active_capsule_id)
    _ = run_kr_day_capsule_shadow_tick(store, (active_entry,))
    active_next = _reidentify(_advance(_entry_evaluation()), active_capsule_id)
    sibling_next = _rebuild(
        _reidentify(_advance(_plain_evaluation()), sibling_capsule_id),
        calendar_snapshot_id="e" * 64,
    )

    # When: both evaluations share the session and completed bar but not the calendar.
    batch = run_kr_day_capsule_shadow_tick(store, (active_next, sibling_next))

    # Then: sibling divergence cannot terminate or block exact-lineage ACTIVE management.
    managed = next(item.event for item in batch.results if item.event.capsule_id == active_capsule_id)
    assert managed.status is KrDayCapsuleShadowStatus.ACTIVE
    assert managed.reason is KrDayCapsuleShadowReason.ACTIVE
    assert store.latest(active_capsule_id, managed.session_date.isoformat()) == managed


def test_divergent_and_stale_evaluation_block_without_cursor_advance(tmp_path: Path) -> None:
    stale_store = KrDayCapsuleShadowStore(tmp_path / "stale.sqlite3")
    batch_store = KrDayCapsuleShadowStore(tmp_path / "batch.sqlite3")
    valid = _entry_evaluation()
    stale = _rebuild(valid, evaluated_at=valid.evaluated_at + dt.timedelta(seconds=10))
    divergent = _reidentify(_advance(valid), "f" * 64)
    future_cursor = _rebuild(valid, completed_bar_cursor=valid.completed_bar_cursor + dt.timedelta(minutes=1))
    divergent_session = _rebuild(valid, session_date=valid.session_date + dt.timedelta(days=1))

    stale_result = run_kr_day_capsule_shadow_tick(stale_store, (stale,)).results[0].event
    batch = run_kr_day_capsule_shadow_tick(batch_store, (divergent, valid)).results

    assert stale_result.status is KrDayCapsuleShadowStatus.BLOCKED
    assert stale_result.accepted_bar_cursor is None
    assert tuple(item.event.status for item in batch) == (
        KrDayCapsuleShadowStatus.ACTIVE,
        KrDayCapsuleShadowStatus.BLOCKED,
    )
    assert batch[1].event.accepted_bar_cursor is None
    for index, invalid in enumerate((future_cursor, divergent_session)):
        invalid_store = KrDayCapsuleShadowStore(tmp_path / f"invalid-{index}.sqlite3")
        invalid_result = run_kr_day_capsule_shadow_tick(invalid_store, (invalid,)).results[0].event
        assert invalid_result.status is KrDayCapsuleShadowStatus.BLOCKED
        assert invalid_result.accepted_bar_cursor is None


def test_more_than_three_capsules_and_conflicting_replay_reject(tmp_path: Path) -> None:
    store = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    evaluation = _entry_evaluation()
    entry = run_kr_day_capsule_shadow_tick(store, (evaluation,)).results[0].event
    payload = KrDayCapsuleShadowEventPayload.model_validate(
        entry.model_dump(mode="python", exclude={"event_id"})
        | {"reason": KrDayCapsuleShadowReason.ACTIVE}
    )
    conflict = KrDayCapsuleShadowEvent.model_validate(
        payload.model_dump(mode="python")
        | {"event_id": KrDayCapsuleShadowEvent.canonical_id_for(payload)}
    )

    with pytest.raises(InvalidKrDayCapsuleShadowServiceError):
        _ = run_kr_day_capsule_shadow_tick(store, (evaluation,) * 4)
    with pytest.raises(InvalidKrDayCapsuleShadowStoreError):
        _ = store.append(conflict)


def _plain_evaluation() -> KrDayCapsuleEvaluation:
    return adapt_kr_day_capsule_evaluation(_request())


def _entry_evaluation() -> KrDayCapsuleEvaluation:
    request = _request()
    bars = list(request.bars)
    replacements = {
        50: ("10050", "10120", "10040", "10110", 100, "1008000"),
        60: ("10020", "10050", "9990", "10010", 100, "1001000"),
        61: ("10020", "10180", "10010", "10150", 200, "2020000"),
    }
    for index, values in replacements.items():
        open_price, high, low, close, volume, trading_value = values
        bars[index] = bars[index].model_copy(
            update={
                "open": Decimal(open_price),
                "high": Decimal(high),
                "low": Decimal(low),
                "close": Decimal(close),
                "volume": volume,
                "trading_value_krw": Decimal(trading_value),
            }
        )
    market = request.market.model_copy(
        update={"last_price": Decimal("10155"), "bid_price": Decimal("10150"), "ask_price": Decimal("10160")}
    )
    return adapt_kr_day_capsule_evaluation(
        KrDayCapsuleEvaluationRequest.model_validate(
            request.model_dump(mode="python") | {"bars": tuple(bars), "market": market}
        )
    )


def _advance(
    evaluation: KrDayCapsuleEvaluation,
    *,
    count: int = 1,
    low: Decimal = Decimal("10000"),
    high: Decimal = Decimal("10200"),
) -> KrDayCapsuleEvaluation:
    bars = list(evaluation.setup_input.bars)
    for offset in range(count):
        start = bars[-1].end_at
        observed = start + dt.timedelta(minutes=1)
        bars.append(
            KrCompletedMinuteBar(
                symbol=evaluation.symbol,
                start_at=start,
                end_at=observed,
                observed_at=observed,
                open=Decimal("10150"),
                high=high,
                low=low,
                close=Decimal("10160"),
                volume=100,
                trading_value_krw=Decimal("1015000"),
                evidence_ref=EvidenceRef(
                    namespace="bar/kis-kr",
                    record_id=f"bar-next-{offset}-{observed.isoformat()}",
                    observed_at=observed,
                ),
            )
        )
    evaluated_at = bars[-1].end_at + dt.timedelta(seconds=2)
    request = _request()
    market = evaluation.market.model_copy(update={"observed_at": evaluated_at - dt.timedelta(seconds=1)})
    return adapt_kr_day_capsule_evaluation(
        KrDayCapsuleEvaluationRequest.model_validate(
            request.model_dump(mode="python")
            | {"bars": tuple(bars), "evaluated_at": evaluated_at, "market": market}
        )
    )


def _reidentify(evaluation: KrDayCapsuleEvaluation, capsule_id: str) -> KrDayCapsuleEvaluation:
    setup_input = evaluation.setup_input.model_copy(update={"producer_strategy_version": capsule_id})
    return _rebuild(evaluation, capsule_id=capsule_id, setup_input=setup_input)


def _invalid_signal_projection(evaluation: KrDayCapsuleEvaluation) -> KrDayCapsuleEvaluation:
    market = evaluation.market.model_copy(
        update={"bid_price": Decimal("10399"), "ask_price": Decimal("10400")}
    )
    return _rebuild(evaluation, market=market)


def _rebuild(
    evaluation: KrDayCapsuleEvaluation,
    *,
    capsule_id: str | None = None,
    evaluated_at: dt.datetime | None = None,
    setup_input: KrThemeDaySetupInput | None = None,
    session_date: dt.date | None = None,
    completed_bar_cursor: dt.datetime | None = None,
    market: KrMarketConstraintSnapshot | None = None,
    symbol: str | None = None,
    collection_cycle_id: str | None = None,
    calendar_snapshot_id: str | None = None,
) -> KrDayCapsuleEvaluation:
    values = evaluation.model_dump(mode="python", exclude={"evaluation_id"})
    if capsule_id is not None:
        values["capsule_id"] = capsule_id
    if evaluated_at is not None:
        values["evaluated_at"] = evaluated_at
    if setup_input is not None:
        values["setup_input"] = setup_input
    if session_date is not None:
        values["session_date"] = session_date
    if completed_bar_cursor is not None:
        values["completed_bar_cursor"] = completed_bar_cursor
    if market is not None:
        values["market"] = market
    if symbol is not None:
        values["symbol"] = symbol
    if collection_cycle_id is not None:
        values["collection_cycle_id"] = collection_cycle_id
    if calendar_snapshot_id is not None:
        values["calendar_snapshot_id"] = calendar_snapshot_id
    payload = KrDayCapsuleEvaluationPayload.model_validate(values)
    return KrDayCapsuleEvaluation.model_validate(
        values | {"evaluation_id": KrDayCapsuleEvaluation.canonical_id_for(payload)}
    )
