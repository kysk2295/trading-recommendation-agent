from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_theme_day_trial import (
    InvalidKrThemeDayTrialError,
    KrThemeDayTrialRegistrationRequest,
    kr_theme_day_trial_id,
    register_kr_theme_day_shadow_trial,
    start_kr_theme_day_shadow_trial,
)
from trading_agent.kr_theme_research_registration import (
    kr_theme_day_strategy_version,
    register_kr_theme_research_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
DAY_MANIFEST = ROOT / "examples" / "kr_theme_projection" / "day-research-registration.json"
KST = ZoneInfo("Asia/Seoul")
CODE_VERSION = "kr-theme-day-fixture-code-v1"
STRATEGY_VERSION = kr_theme_day_strategy_version(CODE_VERSION)
SESSION_DATE = dt.date(2026, 7, 20)
REGISTERED_AT = dt.datetime(2026, 7, 19, 8, 31, tzinfo=KST)
STARTED_AT = dt.datetime(2026, 7, 20, 9, tzinfo=KST)


def _request() -> KrThemeDayTrialRegistrationRequest:
    return KrThemeDayTrialRegistrationRequest(
        strategy_version=STRATEGY_VERSION,
        code_version=CODE_VERSION,
        session_date=SESSION_DATE,
        registered_at=REGISTERED_AT,
    )


def test_kr_theme_day_trial_registers_and_starts_exact_replay(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = register_kr_theme_research_manifest(DAY_MANIFEST, ledger)

    first = register_kr_theme_day_shadow_trial(ledger, _request())
    second = register_kr_theme_day_shadow_trial(ledger, _request())
    started = start_kr_theme_day_shadow_trial(ledger, first.registration.trial_id, STARTED_AT)
    replay = start_kr_theme_day_shadow_trial(ledger, first.registration.trial_id, STARTED_AT)

    assert first.created is True
    assert second.created is False
    assert first.registration.trial_id == kr_theme_day_trial_id(SESSION_DATE, STRATEGY_VERSION)
    assert first.registration.evidence_budget == (
        "cost_model:entry_ask_plus_20bps",
        "counterfactual:no_entry",
        "maximum_missing_evidence_rate:0",
        "minimum_completed_signals:30",
        "minimum_forward_sessions:20",
        "review_gates:fillability_drawdown_stability_multiple_testing",
    )
    assert started.created is True
    assert replay.created is False
    assert started.event.event_kind is TrialEventKind.STARTED


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
    _ = register_kr_theme_research_manifest(DAY_MANIFEST, source)
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
    _ = register_kr_theme_research_manifest(DAY_MANIFEST, target)
    with target.writer() as writer:
        assert writer.register_multi_market_trial(changed) is True

    with pytest.raises(InvalidKrThemeDayTrialError):
        _ = start_kr_theme_day_shadow_trial(target, changed.trial_id, STARTED_AT)
