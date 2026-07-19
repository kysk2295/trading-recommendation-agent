from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import (
    KisKrSessionCalendarReceipt,
    KrSessionCalendarSnapshot,
)
from trading_agent.kr_theme_day_composite import (
    KrThemeDayCompositeAuthority,
    KrThemeDayCompositeRegistrationRequest,
    register_kr_theme_day_composite,
)
from trading_agent.kr_theme_day_trial import (
    InvalidKrThemeDayTrialError,
    KrThemeDayTrialRegistrationRequest,
    kr_theme_day_trial_id,
    register_kr_theme_day_shadow_trial,
    start_kr_theme_day_shadow_trial,
)
from trading_agent.kr_theme_research_registration import (
    kr_theme_day_strategy_version,
    kr_theme_strategy_version,
    register_kr_theme_research_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
DAY_MANIFEST = ROOT / "examples" / "kr_theme_projection" / "day-research-registration.json"
OPPORTUNITY_MANIFEST = ROOT / "examples" / "kr_theme_projection" / "research-registration.json"
CALENDAR_FIXTURE = ROOT / "tests" / "fixtures" / "kis_kr_session_calendar_20260719.json"
KST = ZoneInfo("Asia/Seoul")
CODE_VERSION = "kr-theme-day-fixture-code-v1"
STRATEGY_VERSION = kr_theme_day_strategy_version(CODE_VERSION)
OPPORTUNITY_VERSION = kr_theme_strategy_version("kr-theme-fixture-code-v1")
SESSION_DATE = dt.date(2026, 7, 20)
REGISTERED_AT = dt.datetime(2026, 7, 19, 8, 31, tzinfo=KST)
CALENDAR_OBSERVED_AT = dt.datetime(2026, 7, 19, 8, 30, tzinfo=KST)
STARTED_AT = dt.datetime(2026, 7, 20, 9, tzinfo=KST)


def _request() -> KrThemeDayTrialRegistrationRequest:
    return KrThemeDayTrialRegistrationRequest(
        strategy_version=STRATEGY_VERSION,
        code_version=CODE_VERSION,
        session_date=SESSION_DATE,
        registered_at=REGISTERED_AT,
        calendar_snapshot=_calendar_evidence()[1],
        opportunity_strategy_version=OPPORTUNITY_VERSION,
    )


def _calendar_evidence() -> tuple[KisKrSessionCalendarReceipt, KrSessionCalendarSnapshot]:
    receipt = KisKrSessionCalendarReceipt(
        base_date=REGISTERED_AT.date(),
        received_at=CALENDAR_OBSERVED_AT,
        status_code=200,
        content_type="application/json",
        raw_payload=CALENDAR_FIXTURE.read_bytes(),
    )
    return receipt, project_kis_kr_session_calendar(receipt)


def _register_authority(ledger: ExperimentLedgerStore) -> KrThemeDayCompositeAuthority:
    _ = register_kr_theme_research_manifest(OPPORTUNITY_MANIFEST, ledger)
    _ = register_kr_theme_research_manifest(DAY_MANIFEST, ledger)
    result = register_kr_theme_day_composite(
        ledger,
        KrThemeDayCompositeRegistrationRequest(
            day_strategy_version=STRATEGY_VERSION,
            opportunity_strategy_version=OPPORTUNITY_VERSION,
            registered_at=REGISTERED_AT - dt.timedelta(seconds=30),
        ),
    )
    return result.authority


def test_kr_theme_day_trial_registers_and_starts_exact_replay(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    authority = _register_authority(ledger)

    first = register_kr_theme_day_shadow_trial(ledger, _request())
    second = register_kr_theme_day_shadow_trial(ledger, _request())
    started = start_kr_theme_day_shadow_trial(ledger, first.registration.trial_id, STARTED_AT)
    replay = start_kr_theme_day_shadow_trial(ledger, first.registration.trial_id, STARTED_AT)

    assert first.created is True
    assert second.created is False
    assert first.registration.trial_id == kr_theme_day_trial_id(SESSION_DATE, STRATEGY_VERSION)
    assert first.registration.evidence_budget == tuple(
        sorted(
            (
                f"calendar_snapshot:{_request().calendar_snapshot.snapshot_id}",
                "cost_model:entry_ask_plus_20bps",
                "counterfactual:no_entry",
                "maximum_missing_evidence_rate:0",
                "minimum_completed_signals:30",
                "minimum_forward_sessions:20",
                "review_gates:fillability_drawdown_stability_multiple_testing",
                f"composite_hypothesis:{authority.hypothesis_id}",
                f"composite_registration:{authority.registration_key}",
                f"opportunity_strategy:{authority.opportunity_strategy_version}",
            )
        )
    )
    assert started.created is True
    assert replay.created is False
    assert started.event.event_kind is TrialEventKind.STARTED


def test_kr_theme_day_trial_rejects_closed_or_stale_calendar_evidence(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_authority(ledger)

    with pytest.raises(InvalidKrThemeDayTrialError):
        _ = register_kr_theme_day_shadow_trial(
            ledger,
            _request().model_copy(update={"session_date": dt.date(2026, 7, 19)}),
        )
    with pytest.raises(InvalidKrThemeDayTrialError):
        _ = register_kr_theme_day_shadow_trial(
            ledger,
            _request().model_copy(update={"registered_at": REGISTERED_AT + dt.timedelta(minutes=5)}),
        )
    assert ledger.multi_market_trials() == ()


def test_kr_theme_day_trial_rejects_noncanonical_start_time(tmp_path: Path) -> None:
    # Given
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_authority(ledger)
    registration = register_kr_theme_day_shadow_trial(ledger, _request())

    # When / Then
    with pytest.raises(InvalidKrThemeDayTrialError):
        _ = start_kr_theme_day_shadow_trial(
            ledger,
            registration.registration.trial_id,
            STARTED_AT + dt.timedelta(minutes=1),
        )


def test_kr_theme_day_trial_rejects_unregistered_strategy(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    with pytest.raises(InvalidKrThemeDayTrialError):
        _ = register_kr_theme_day_shadow_trial(ledger, _request())


def test_kr_theme_day_trial_rejects_unknown_start(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    with pytest.raises(InvalidKrThemeDayTrialError):
        _ = start_kr_theme_day_shadow_trial(ledger, "missing-trial", STARTED_AT)


def test_kr_theme_day_trial_rejects_generic_changed_budget(tmp_path: Path) -> None:
    source = ExperimentLedgerStore(tmp_path / "source.sqlite3")
    _register_authority(source)
    exact = register_kr_theme_day_shadow_trial(source, _request()).registration
    changed = type(exact).model_validate(
        exact.model_dump(mode="python")
        | {
            "evidence_budget": (
                "counterfactual:no_entry",
                "minimum_forward_sessions:1",
            )
        }
    )
    target = ExperimentLedgerStore(tmp_path / "target.sqlite3")
    _register_authority(target)
    with target.writer() as writer:
        assert writer.register_multi_market_trial(changed) is True

    with pytest.raises(InvalidKrThemeDayTrialError):
        _ = start_kr_theme_day_shadow_trial(target, changed.trial_id, STARTED_AT)
