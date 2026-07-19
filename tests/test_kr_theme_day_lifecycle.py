from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_kr_theme_day_reviewer import (
    _completed_sources,
    _review_request,
)
from tests.test_kr_theme_day_shadow_entry import VERSION
from tests.test_kr_theme_day_trial import _calendar_evidence as prior_calendar_evidence
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import (
    KisKrSessionCalendarReceipt,
    KrSessionCalendarSnapshot,
)
from trading_agent.kr_theme_day_lifecycle_controller import (
    InvalidKrThemeDayLifecycleSourceError,
    KrThemeDayLifecycleRequest,
    control_kr_theme_day_lifecycle,
)
from trading_agent.kr_theme_day_lifecycle_models import (
    KrThemeDayLifecycleOutcome,
    decide_kr_theme_day_lifecycle,
)
from trading_agent.kr_theme_day_review_models import KrThemeDayReviewAction
from trading_agent.kr_theme_day_reviewer import review_kr_theme_day_strategy
from trading_agent.multi_market_experiment_keys import (
    multi_market_hypothesis_registration_key,
    multi_market_strategy_version_registration_key,
)
from trading_agent.multi_market_lifecycle_keys import multi_market_lifecycle_event_key
from trading_agent.multi_market_lifecycle_models import MultiMarketStrategyLifecycleEvent

ROOT = Path(__file__).resolve().parents[1]
CALENDAR_FIXTURE = ROOT / "tests" / "fixtures" / "kis_kr_session_calendar_20260720.json"
KST = ZoneInfo("Asia/Seoul")
DECIDED_AT = dt.datetime(2026, 7, 20, 15, 40, tzinfo=KST)


def _calendar_evidence() -> tuple[KisKrSessionCalendarReceipt, KrSessionCalendarSnapshot]:
    receipt = KisKrSessionCalendarReceipt(
        base_date=dt.date(2026, 7, 20),
        received_at=dt.datetime(2026, 7, 20, 15, 34, tzinfo=KST),
        status_code=200,
        content_type="application/json",
        raw_payload=CALENDAR_FIXTURE.read_bytes(),
    )
    return receipt, project_kis_kr_session_calendar(receipt)


def _request(snapshot: KrSessionCalendarSnapshot) -> KrThemeDayLifecycleRequest:
    return KrThemeDayLifecycleRequest(
        strategy_version=VERSION,
        as_of_session=dt.date(2026, 7, 20),
        decided_at=DECIDED_AT,
        calendar_snapshot=snapshot,
    )


def _reviewed_sources(tmp_path: Path, *, with_entry: bool = True):
    sources = _completed_sources(tmp_path, with_entry=with_entry)
    _ = review_kr_theme_day_strategy(sources, _review_request())
    return ExperimentLedgerStore(sources.experiment_ledger.path), sources


def _seed_registration(ledger: ExperimentLedgerStore) -> MultiMarketStrategyLifecycleEvent:
    hypothesis = ledger.multi_market_hypotheses()[0]
    version = ledger.multi_market_strategy_versions()[0]
    calendar_id = "d" * 64
    event = MultiMarketStrategyLifecycleEvent(
        strategy_version=version.registration.strategy_version,
        strategy_lane=version.registration.strategy_lane,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version="multi_market_lifecycle_v1",
        decision_session_date=dt.date(2026, 7, 19),
        effective_session_date=dt.date(2026, 7, 20),
        decided_at=dt.datetime(2026, 7, 19, 15, 40, tzinfo=KST),
        session_calendar_snapshot_id=calendar_id,
        evidence_keys=tuple(
            sorted(
                (
                    calendar_id,
                    hypothesis.registration.experiment_scope_key,
                    str(multi_market_hypothesis_registration_key(hypothesis.registration)),
                    str(multi_market_strategy_version_registration_key(version.registration)),
                )
            )
        ),
        reason_codes=("multi_market_strategy_registered",),
        previous_event_key=None,
    )
    with ledger.writer() as writer:
        assert writer.append_multi_market_lifecycle_event(event) is True
    return event


def test_controller_registers_next_open_session_and_exactly_replays(tmp_path: Path) -> None:
    ledger, sources = _reviewed_sources(tmp_path)
    snapshot = _calendar_evidence()[1]

    first = control_kr_theme_day_lifecycle(ledger, sources, _request(snapshot))
    replay = control_kr_theme_day_lifecycle(
        ledger,
        sources,
        _request(snapshot).model_copy(update={"decided_at": DECIDED_AT + dt.timedelta(minutes=5)}),
    )

    assert first.outcome is KrThemeDayLifecycleOutcome.REGISTERED
    assert first.created is True
    assert replay.created is False
    assert replay.event == first.event
    assert first.to_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
    assert first.event is not None
    assert first.event.effective_session_date == dt.date(2026, 7, 21)
    assert ledger.multi_market_lifecycle_state(VERSION, dt.date(2026, 7, 20)) is None
    assert ledger.multi_market_lifecycle_state(VERSION, dt.date(2026, 7, 21)) is not None


def test_censored_review_suspends_active_shadow_on_next_open_session(tmp_path: Path) -> None:
    ledger, sources = _reviewed_sources(tmp_path, with_entry=False)
    registration = _seed_registration(ledger)

    result = control_kr_theme_day_lifecycle(ledger, sources, _request(_calendar_evidence()[1]))

    assert result.outcome is KrThemeDayLifecycleOutcome.TRANSITIONED
    assert result.created is True
    assert result.from_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
    assert result.to_state is StrategyLifecycleState.SUSPENDED
    assert result.event is not None
    assert result.event.effective_session_date == dt.date(2026, 7, 21)
    assert result.event.previous_event_key == str(multi_market_lifecycle_event_key(registration))
    assert result.event.reason_codes == (
        "data_quality_review_required",
        "review_evidence_verified",
    )


def test_comparison_ready_can_only_create_a_challenger_decision() -> None:
    decision = decide_kr_theme_day_lifecycle(
        StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        KrThemeDayReviewAction.COMPARISON_READY,
    )

    assert decision.target_state is StrategyLifecycleState.CHALLENGER
    assert decision.blockers == (
        "allocation_change_forbidden",
        "independent_comparator_missing",
        "multiple_testing_evidence_missing",
        "paper_authority_forbidden",
        "shadow_champion_forbidden",
    )


def test_controller_rejects_non_current_calendar_without_lifecycle_append(tmp_path: Path) -> None:
    ledger, sources = _reviewed_sources(tmp_path)
    stale_snapshot = prior_calendar_evidence()[1]

    with pytest.raises(InvalidKrThemeDayLifecycleSourceError):
        _ = control_kr_theme_day_lifecycle(ledger, sources, _request(stale_snapshot))

    assert ledger.multi_market_lifecycle_events(VERSION) == ()
