from __future__ import annotations

import datetime as dt
import json
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_kr_theme_day_shadow_entry import VERSION, _ledger
from tests.test_kr_theme_day_trial_terminal import _request, _trial_stores
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.kr_theme_day_review_models import (
    KrThemeDayReviewAction,
    KrThemeDayReviewCounts,
    decide_kr_theme_day_review,
)
from trading_agent.kr_theme_day_review_store import (
    InvalidKrThemeDayReviewStoreError,
    KrThemeDayReviewStore,
)
from trading_agent.kr_theme_day_reviewer import (
    InvalidKrThemeDayReviewError,
    KrThemeDayReviewRequest,
    KrThemeDayReviewSources,
    review_kr_theme_day_strategy,
)
from trading_agent.kr_theme_day_trial_terminal import finalize_kr_theme_day_shadow_trial

KST = ZoneInfo("Asia/Seoul")
REVIEWED_AT = dt.datetime(2026, 7, 20, 15, 35, tzinfo=KST)


def _completed_sources(tmp_path: Path, *, with_entry: bool = True) -> KrThemeDayReviewSources:
    terminal_sources, trial_id = _trial_stores(tmp_path, with_entry=with_entry, with_exit=with_entry)
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    _ = finalize_kr_theme_day_shadow_trial(ledger, terminal_sources, _request(trial_id))
    return KrThemeDayReviewSources(
        ExperimentLedgerReader(ledger.path),
        terminal_sources.entry_store,
        terminal_sources.exit_store,
        terminal_sources.terminal_store,
        KrThemeDayReviewStore(tmp_path / "reviews.sqlite3"),
    )


def _review_request() -> KrThemeDayReviewRequest:
    return KrThemeDayReviewRequest(
        strategy_version=VERSION,
        as_of_session=dt.date(2026, 7, 20),
        reviewed_at=REVIEWED_AT,
    )


def test_completed_terminal_is_reviewed_with_exact_metrics_and_replay(tmp_path: Path) -> None:
    sources = _completed_sources(tmp_path)

    first = review_kr_theme_day_strategy(sources, _review_request())
    replay = review_kr_theme_day_strategy(
        sources,
        KrThemeDayReviewRequest(
            strategy_version=VERSION,
            as_of_session=dt.date(2026, 7, 20),
            reviewed_at=REVIEWED_AT + dt.timedelta(hours=1),
        ),
    )

    assert first.created is True
    assert replay.created is False
    assert replay.event == first.event
    assert first.event.action is KrThemeDayReviewAction.CONTINUE_COLLECTION
    assert first.event.completed_sessions == 1
    assert first.event.censored_sessions == 0
    assert first.event.failed_sessions == 0
    assert first.event.completed_trades == 1
    assert len(first.event.trade_exit_ids) == 1
    assert first.event.compounded_return > 0
    assert first.event.mean_realized_r > 0
    assert first.event.win_rate == 1
    assert first.event.max_drawdown == 0
    assert first.event.blockers == (
        "allocation_change_forbidden",
        "automatic_state_change_forbidden",
        "minimum_completed_signals:1/30",
        "minimum_forward_sessions:1/20",
        "paper_authority_forbidden",
    )
    assert first.event.automatic_state_change_allowed is False
    assert first.event.order_authority_change_allowed is False
    assert first.event.allocation_change_allowed is False
    assert stat.S_IMODE(sources.review_store.path.stat().st_mode) == 0o600


def test_censored_session_is_data_quality_review_not_zero_return(tmp_path: Path) -> None:
    sources = _completed_sources(tmp_path, with_entry=False)

    result = review_kr_theme_day_strategy(sources, _review_request())

    assert result.event.action is KrThemeDayReviewAction.DATA_QUALITY_REVIEW
    assert result.event.completed_sessions == 0
    assert result.event.censored_sessions == 1
    assert result.event.completed_trades == 0
    assert result.event.compounded_return == 0
    assert result.event.reasons == ("censored_evidence_present",)
    assert "censored_sessions:1" in result.event.blockers


def test_threshold_decision_is_comparison_ready_but_never_promotes() -> None:
    decision = decide_kr_theme_day_review(
        KrThemeDayReviewCounts(
            completed_sessions=20,
            censored_sessions=0,
            failed_sessions=0,
            completed_trades=30,
        )
    )

    assert decision.action is KrThemeDayReviewAction.COMPARISON_READY
    assert decision.reasons == ("minimum_forward_evidence_satisfied",)
    assert decision.blockers == (
        "allocation_change_forbidden",
        "automatic_state_change_forbidden",
        "independent_comparator_missing",
        "multiple_testing_evidence_missing",
        "paper_authority_forbidden",
    )


def test_review_event_rejects_action_that_disagrees_with_evidence_counts(tmp_path: Path) -> None:
    sources = _completed_sources(tmp_path)
    event = review_kr_theme_day_strategy(sources, _review_request()).event

    with pytest.raises(ValueError):
        _ = type(event).model_validate(
            event.model_dump(mode="python") | {"action": KrThemeDayReviewAction.COMPARISON_READY}
        )


def test_terminal_store_tamper_blocks_review_without_creating_store(tmp_path: Path) -> None:
    sources = _completed_sources(tmp_path)
    with sqlite3.connect(sources.terminal_store.path) as connection:
        _ = connection.execute("DROP TRIGGER kr_theme_day_trial_terminals_no_update")
        connection.commit()

    with pytest.raises(InvalidKrThemeDayReviewError):
        _ = review_kr_theme_day_strategy(sources, _review_request())
    assert not sources.review_store.path.exists()


def test_review_store_detects_tamper_and_imports_no_operational_authority(tmp_path: Path) -> None:
    sources = _completed_sources(tmp_path)
    _ = review_kr_theme_day_strategy(sources, _review_request())
    with sqlite3.connect(sources.review_store.path) as connection:
        _ = connection.execute("DROP TRIGGER kr_theme_day_reviews_no_update")
        _ = connection.execute("UPDATE kr_theme_day_reviews SET payload_json = '{}' ")
        connection.commit()
    with pytest.raises(InvalidKrThemeDayReviewStoreError):
        _ = sources.review_store.events()

    script = """
import json
import sys
import trading_agent.kr_theme_day_reviewer
print(json.dumps(sorted(name for name in sys.modules if name.startswith('trading_agent.'))))
"""
    completed = subprocess.run(
        (sys.executable, "-c", script),
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = json.loads(completed.stdout)
    forbidden = (
        "alpaca",
        "paper",
        "broker",
        "execution",
        "credential",
        "provider",
        "lifecycle_controller",
        "portfolio_manager",
    )
    assert not {module for module in loaded if any(marker in module for marker in forbidden)}
