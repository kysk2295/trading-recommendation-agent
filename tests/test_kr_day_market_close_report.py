from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_kr_day_capsule_shadow import _advance, _entry_evaluation
from trading_agent.kis_kr_session_calendar_models import (
    KIS_CALENDAR_ADAPTER_VERSION,
    KIS_CALENDAR_SOURCE_COMMIT,
    KrSessionCalendarPayload,
    KrSessionDay,
    kr_session_calendar_snapshot,
)
from trading_agent.kr_day_capsule_outcomes import (
    KrDayCapsuleOutcome,
    KrDayCapsuleOutcomeAttempt,
    project_kr_day_capsule_outcome,
)
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowEvent
from trading_agent.kr_day_capsule_shadow_service import run_kr_day_capsule_shadow_tick
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_market_close_report import (
    InvalidKrDayMarketCloseReportError,
    KrDayMarketCloseRequest,
    publish_kr_day_market_close_report,
)

KST = ZoneInfo("Asia/Seoul")


def test_close_report_is_kr_read_only_immutable_and_replay_dedupes(tmp_path: Path) -> None:
    # Given: one exact finalized KR Shadow attempt and an official XKRX calendar.
    store = KrDayCapsuleShadowStore(tmp_path / "shadow" / "events.sqlite3")
    entry = _entry_evaluation()
    stopped = _advance(entry, low=Decimal("9900"), high=Decimal("10400"))
    _ = run_kr_day_capsule_shadow_tick(store, (entry,))
    _ = run_kr_day_capsule_shadow_tick(store, (stopped,))
    events = store.events()
    outcome = _outcome(events)
    request = _request(events, (outcome,))

    # When: the report is published twice from the exact same evidence.
    first = publish_kr_day_market_close_report(tmp_path / "reports", request)
    replay = publish_kr_day_market_close_report(tmp_path / "reports", request)

    # Then: publication is idempotent and labels KR Shadow without actual return or profit claim.
    assert first.created is True
    assert replay.created is False
    assert replay.report == first.report
    assert first.report.payload.execution.provider_read_only is True
    assert first.report.payload.execution.actual_return is None
    assert first.report.payload.profitability_claim is False
    assert first.report.payload.execution.censored_count == 0
    assert first.next_session_date == dt.date(2026, 8, 26)
    assert first.metrics.payload.daily_cost_adjusted_shadow_return == first.report.payload.execution.modeled_return
    assert (
        first.metrics.payload.cumulative_cost_adjusted_shadow_return
        == first.report.payload.lineage.cumulative_modeled_return
    )
    assert first.metrics.payload.mean_r is not None
    assert first.metrics.payload.profit_factor == 0.0
    assert first.metrics.payload.completed_count == 1
    assert first.metrics.payload.outcome_ids == (outcome.outcome_id,)
    assert replay.metrics.metrics_id == first.metrics.metrics_id


def test_late_evidence_creates_a_linked_revision(tmp_path: Path) -> None:
    # Given: an initial finalized no-signal report.
    store = KrDayCapsuleShadowStore(tmp_path / "shadow" / "events.sqlite3")
    entry = _entry_evaluation()
    stopped = _advance(entry, low=Decimal("9900"), high=Decimal("10400"))
    _ = run_kr_day_capsule_shadow_tick(store, (entry,))
    _ = run_kr_day_capsule_shadow_tick(store, (stopped,))
    events = store.events()
    outcome = _outcome(events)
    initial = publish_kr_day_market_close_report(tmp_path / "reports", _request(events, (outcome,)))

    # When: late KR-only incident evidence arrives after the initial close.
    revised_request = _request(events, (outcome,)).model_copy(update={"data_incident_ids": ("late-source-evidence",)})
    revised = publish_kr_day_market_close_report(tmp_path / "reports", revised_request)

    # Then: the prior report remains and the new immutable revision links to it.
    assert revised.created is True
    assert revised.report.payload.revision == 2
    assert revised.report.payload.previous_report_id == initial.report.report_id
    assert revised.metrics.payload.previous_metrics_id == initial.metrics.metrics_id
    assert revised.metrics.payload.data_incident_ids == ("late-source-evidence",)
    assert len(tuple((tmp_path / "reports").glob("market_close_report_*.json"))) == 2


def test_close_report_rejects_preclose_or_unlinked_outcome(tmp_path: Path) -> None:
    # Given: terminal events whose supplied outcome does not link to the terminal event.
    store = KrDayCapsuleShadowStore(tmp_path / "shadow" / "events.sqlite3")
    entry = _entry_evaluation()
    stopped = _advance(entry, low=Decimal("9900"), high=Decimal("10400"))
    _ = run_kr_day_capsule_shadow_tick(store, (entry,))
    _ = run_kr_day_capsule_shadow_tick(store, (stopped,))
    events = store.events()
    outcome = _outcome(events)
    bad = _request(events, (outcome,)).model_copy(
        update={"official_close_at": dt.datetime(2026, 8, 24, 15, 29, tzinfo=KST)}
    )

    # When / Then: publication fails before creating any report artifact.
    with pytest.raises(InvalidKrDayMarketCloseReportError):
        _ = publish_kr_day_market_close_report(tmp_path / "reports", bad)
    assert not tuple((tmp_path / "reports").glob("market_close_report_*.json"))


def _calendar():
    days = tuple(
        KrSessionDay(
            session_date=date,
            weekday_code=str(date.weekday()),
            business_day=open_day,
            trading_day=open_day,
            open_day=open_day,
            settlement_day=open_day,
        )
        for date, open_day in (
            (dt.date(2026, 8, 24), True),
            (dt.date(2026, 8, 25), False),
            (dt.date(2026, 8, 26), True),
        )
    )
    return kr_session_calendar_snapshot(
        KrSessionCalendarPayload(
            source_commit=KIS_CALENDAR_SOURCE_COMMIT,
            adapter_version=KIS_CALENDAR_ADAPTER_VERSION,
            base_date=dt.date(2026, 8, 24),
            observed_at=dt.datetime(2026, 8, 24, 8, 0, tzinfo=KST),
            receipt_sha256="c" * 64,
            days=days,
        )
    )


def _outcome(events: tuple[KrDayCapsuleShadowEvent, ...]) -> KrDayCapsuleOutcome:
    return project_kr_day_capsule_outcome(
        KrDayCapsuleOutcomeAttempt(
            attempt_id="attempt-1",
            capsule_id=events[0].capsule_id,
            hypothesis_version_id="b" * 64,
            trial_id="trial-1",
            session_date=events[0].session_date,
            events=events,
        )
    )


def _request(events, outcomes):
    return KrDayMarketCloseRequest(
        session_date=dt.date(2026, 8, 24),
        official_close_at=dt.datetime(2026, 8, 24, 15, 30, tzinfo=KST),
        finalized_at=dt.datetime(2026, 8, 24, 15, 31, tzinfo=KST),
        calendar_snapshot=_calendar(),
        expected_capsule_ids=tuple(sorted({event.capsule_id for event in events})),
        shadow_events=events,
        outcomes=outcomes,
        active_capsule_ids=tuple(sorted({event.capsule_id for event in events})),
        queued_capsule_ids=(),
        risk_incident_ids=(),
        data_incident_ids=(),
    )
