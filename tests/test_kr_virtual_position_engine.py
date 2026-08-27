from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_kr_autonomous_trade_planner import _request
from trading_agent.kr_autonomous_trade_models import KrTradeRecommendation
from trading_agent.kr_autonomous_trade_planner import plan_kr_autonomous_trade
from trading_agent.kr_theme_day_setup_progress import KrCompletedMinuteBar
from trading_agent.kr_virtual_position_engine import advance_kr_virtual_position, arm_kr_virtual_position
from trading_agent.kr_virtual_position_models import (
    InvalidKrVirtualPositionError,
    KrVirtualPositionEvent,
    KrVirtualPositionReason,
    KrVirtualPositionState,
    virtual_position_event_id,
)
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore
from trading_agent.signal_contract_models import EvidenceRef

KST = dt.timezone(dt.timedelta(hours=9))


def test_entry_stop_target_collision_resolves_stopped_on_future_bar() -> None:
    # Given: an armed recommendation and one later completed bar crossing every level.
    recommendation = _recommendation()
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    bar = _bar(
        recommendation.timestamp.astimezone(KST).replace(second=0, microsecond=0) + dt.timedelta(minutes=1),
        1,
        low="100",
        high="108",
    )

    # When: the completed bar is applied.
    events = advance_kr_virtual_position(recommendation, armed, (bar,), bar.observed_at)

    # Then: the conservative stop wins without an intermediate active event.
    assert tuple(event.state for event in events) == (KrVirtualPositionState.STOPPED,)
    assert events[0].reason is KrVirtualPositionReason.STOP_FIRST
    assert events[0].fill_price == recommendation.entry
    assert events[0].exit_price == recommendation.stop


def test_pending_entry_expires_and_active_position_uses_first_target_only() -> None:
    # Given: separate armed recommendations for expiry and target handling.
    recommendation = _recommendation()
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    start = recommendation.timestamp.astimezone(KST).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)

    # When: an entry remains pending through validity and another enters then reaches both targets.
    expired = advance_kr_virtual_position(
        recommendation,
        armed,
        (
            _bar(start, 1, low="101", high="102", close="102"),
            _bar(start + dt.timedelta(minutes=1), 2, low="101", high="102", close="102"),
        ),
        _bar(start + dt.timedelta(minutes=1), 2, low="101", high="102", close="102").observed_at,
    )
    active = advance_kr_virtual_position(
        recommendation,
        armed,
        (_bar(start, 3, low="102", high="104"),),
        _bar(start, 3, low="102", high="104").observed_at,
    )[-1]
    targeted = advance_kr_virtual_position(
        recommendation,
        active,
        (_bar(start + dt.timedelta(minutes=1), 4, low="102", high="108"),),
        _bar(start + dt.timedelta(minutes=1), 4, low="102", high="108").observed_at,
    )

    # Then: pending entry expires and the first target is terminal.
    assert expired[-1].state is KrVirtualPositionState.EXPIRED
    assert targeted[-1].state is KrVirtualPositionState.TARGETED
    assert targeted[-1].exit_price == recommendation.targets[0]


def test_close_bar_exits_at_close_and_gap_censors_without_price() -> None:
    # Given: an active position and a completed close bar or a discontinuous next bar.
    recommendation = _recommendation()
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    start = recommendation.timestamp.astimezone(KST).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    active = advance_kr_virtual_position(
        recommendation,
        armed,
        (_bar(start, 1, low="102", high="104"),),
        _bar(start, 1, low="102", high="104").observed_at,
    )[-1]
    close_start = start.replace(hour=15, minute=29)
    active = _with_cursor(active, close_start)

    # When: the engine receives the 15:30 completed bar and, separately, a gapped bar.
    closed = advance_kr_virtual_position(
        recommendation,
        active,
        (_bar(close_start, 2, low="102", high="104", close="104"),),
        _bar(close_start, 2, low="102", high="104", close="104").observed_at,
    )
    censored = advance_kr_virtual_position(
        recommendation,
        active,
        (_bar(start + dt.timedelta(minutes=2), 3, low="102", high="104"),),
        _bar(start + dt.timedelta(minutes=2), 3, low="102", high="104").observed_at,
    )

    # Then: close uses observed close; the gap fabricates no exit price.
    assert closed[-1].reason is KrVirtualPositionReason.SESSION_CLOSE
    assert closed[-1].exit_price == Decimal("104")
    assert censored[-1].state is KrVirtualPositionState.CENSORED
    assert censored[-1].exit_price is None


def test_replay_is_exact_or_terminally_censored_when_evidence_diverges() -> None:
    # Given: a previously accepted completed bar.
    recommendation = _recommendation()
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    start = recommendation.timestamp.astimezone(KST).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    bar = _bar(start, 1, low="102", high="104")
    active = advance_kr_virtual_position(recommendation, armed, (bar,), bar.observed_at)[-1]

    # When: restart replays the exact bar and then a changed artifact at the same cursor.
    exact = advance_kr_virtual_position(recommendation, active, (bar,), bar.observed_at)
    divergent = advance_kr_virtual_position(
        recommendation, active, (_bar(start, 9, low="102", high="104"),), bar.observed_at
    )

    # Then: exact replay is a no-op while divergence is explicit censorship.
    assert exact == ()
    assert divergent[-1].reason is KrVirtualPositionReason.DIVERGENT_REPLAY
    assert divergent[-1].accepted_completed_bar_cursor == active.accepted_completed_bar_cursor


@pytest.mark.parametrize("future_kind", ["end", "observation"])
def test_future_or_unobserved_bar_is_rejected_without_transition(tmp_path: Path, future_kind: str) -> None:
    # Given: an armed position and a bar whose end or observation is still in the future.
    recommendation = _recommendation()
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    start = recommendation.timestamp.astimezone(KST).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    bar = _bar(start, 7, low="102", high="104")
    now = (bar.end_at if future_kind == "end" else bar.observed_at) - dt.timedelta(seconds=1)
    store = KrVirtualPositionStore(tmp_path / "positions.sqlite3")
    assert store.append(armed)

    # When/Then: public engine admission fails before producing any immutable event.
    with pytest.raises(InvalidKrVirtualPositionError):
        _ = advance_kr_virtual_position(recommendation, armed, (bar,), now)
    assert store.events(armed.position_id) == (armed,)


def test_active_empty_poll_censors_only_after_missing_session_close_bar() -> None:
    # Given: an active position reconstructed at the 15:29 accepted cursor.
    recommendation = _recommendation()
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    start = recommendation.timestamp.astimezone(KST).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    active = advance_kr_virtual_position(
        recommendation,
        armed,
        (_bar(start, 8, low="102", high="104"),),
        start + dt.timedelta(minutes=1, seconds=1),
    )[-1]
    close_start = start.replace(hour=15, minute=29)
    active = _with_cursor(active, close_start)

    # When: an ordinary intraday poll and an after-close poll both have no fresh bar.
    intraday = advance_kr_virtual_position(recommendation, active, (), close_start)
    after_close = advance_kr_virtual_position(
        recommendation,
        active,
        (),
        close_start + dt.timedelta(minutes=1, seconds=1),
    )

    # Then: intraday remains unchanged while the missing 15:30 bar is terminally censored.
    assert intraday == ()
    assert after_close[-1].state is KrVirtualPositionState.CENSORED
    assert after_close[-1].exit_price is None
    assert after_close[-1].exit_time is None


def _recommendation() -> KrTradeRecommendation:
    result = plan_kr_autonomous_trade(_request())
    assert isinstance(result, KrTradeRecommendation)
    return result


def _bar(start: dt.datetime, marker: int, *, low: str, high: str, close: str = "103") -> KrCompletedMinuteBar:
    observed = start + dt.timedelta(minutes=1, seconds=1)
    return KrCompletedMinuteBar(
        symbol="005930",
        start_at=start,
        end_at=start + dt.timedelta(minutes=1),
        observed_at=observed,
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=100,
        trading_value_krw=Decimal(close) * 100,
        evidence_ref=EvidenceRef(namespace="kr/virtual_bar", record_id=str(marker), observed_at=observed),
    )


def _with_cursor(event: KrVirtualPositionEvent, cursor: dt.datetime) -> KrVirtualPositionEvent:
    draft = event.model_copy(
        update={
            "attempted_completed_bar_cursor": cursor,
            "accepted_completed_bar_cursor": cursor,
            "event_id": "",
        }
    )
    return KrVirtualPositionEvent.model_validate(
        draft.model_copy(update={"event_id": virtual_position_event_id(draft)}).model_dump(mode="python")
    )
