from __future__ import annotations

import datetime as dt
import json
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_systematic_regime_engine import _source
from tests.test_systematic_regime_trial import CODE_VERSION, _extend_source
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.systematic_regime_engine import build_systematic_card, replay_systematic_regime
from trading_agent.systematic_regime_models import SystematicRecommendationCard
from trading_agent.systematic_regime_research import systematic_regime_strategy_version
from trading_agent.systematic_regime_review_models import SystematicRegimeReviewerAction
from trading_agent.systematic_regime_review_store import (
    SystematicRegimeReviewReader,
    SystematicRegimeReviewStore,
)
from trading_agent.systematic_regime_reviewer import (
    InvalidSystematicRegimeReviewError,
    review_systematic_regime_trial,
)
from trading_agent.systematic_regime_store import SystematicRegimeStore
from trading_agent.systematic_regime_trial import (
    censor_missed_systematic_regime_trial,
    finalize_systematic_regime_trial,
    register_systematic_regime_trial,
    start_systematic_regime_trial,
)
from trading_agent.us_equity_calendar import regular_session_bounds


def test_reviewer_rechecks_exact_terminal_source_and_outcome_without_authority(
    tmp_path: Path,
) -> None:
    experiment, systematic, card, target_source = _completed_trial(tmp_path)
    reviews = SystematicRegimeReviewStore(tmp_path / "reviews.sqlite3")
    reviewed_at = target_source.observed_at + dt.timedelta(minutes=1)

    result = review_systematic_regime_trial(
        experiment_ledger=ExperimentLedgerReader(experiment.path),
        systematic_store=systematic,
        daily_sources=(target_source,),
        reviews=reviews,
        card_id=card.card_id,
        reviewed_at=reviewed_at,
    )
    replay = review_systematic_regime_trial(
        experiment_ledger=ExperimentLedgerReader(experiment.path),
        systematic_store=systematic,
        daily_sources=(target_source,),
        reviews=reviews,
        card_id=card.card_id,
        reviewed_at=reviewed_at + dt.timedelta(hours=1),
    )

    assert result.created is True
    assert replay.created is False
    assert replay.event == result.event
    assert result.event.terminal_kind is TrialEventKind.COMPLETED
    assert result.event.reviewer_action is SystematicRegimeReviewerAction.CONTINUE_COLLECTION
    assert result.event.reasons == ("completed_shadow_position",)
    assert result.event.automatic_state_change_allowed is False
    assert result.event.order_authority_change_allowed is False
    assert result.event.allocation_change_allowed is False
    assert result.event.blockers == (
        "allocation_manager_forbidden",
        "automatic_state_change_forbidden",
        "executable_paper_champions:0/2",
        "forward_sample_insufficient",
        "paper_authority_forbidden",
    )
    assert len(SystematicRegimeReviewReader(reviews.path).events()) == 1


def test_reviewer_rejects_missing_or_changed_daily_source_without_creating_ledger(
    tmp_path: Path,
) -> None:
    experiment, systematic, card, target_source = _completed_trial(tmp_path)

    for sources in ((), (_extend_source(_source("risk_off"), card.target_session),)):
        reviews = SystematicRegimeReviewStore(tmp_path / f"reviews-{len(sources)}.sqlite3")
        with pytest.raises(InvalidSystematicRegimeReviewError):
            _ = review_systematic_regime_trial(
                experiment_ledger=ExperimentLedgerReader(experiment.path),
                systematic_store=systematic,
                daily_sources=sources,
                reviews=reviews,
                card_id=card.card_id,
                reviewed_at=target_source.observed_at + dt.timedelta(minutes=1),
            )
        assert not reviews.path.exists()


def test_reviewer_preserves_missed_session_as_censored_data_quality_evidence(
    tmp_path: Path,
) -> None:
    source = _source("mixed")
    version = systematic_regime_strategy_version(CODE_VERSION)
    card = build_systematic_card(source, replay_systematic_regime(source), version)
    experiment = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    systematic = SystematicRegimeStore(tmp_path / "systematic.sqlite3")
    with systematic.writer() as writer:
        _ = writer.stage_card(card)
    _ = register_systematic_regime_trial(experiment, card, CODE_VERSION)
    terminal = censor_missed_systematic_regime_trial(experiment, card)
    with systematic.writer() as writer:
        _ = writer.expire_card(card)
    reviews = SystematicRegimeReviewStore(tmp_path / "reviews.sqlite3")

    result = review_systematic_regime_trial(
        experiment_ledger=ExperimentLedgerReader(experiment.path),
        systematic_store=systematic,
        daily_sources=(),
        reviews=reviews,
        card_id=card.card_id,
        reviewed_at=terminal.event.occurred_at + dt.timedelta(minutes=1),
    )

    assert result.created is True
    assert result.event.terminal_kind is TrialEventKind.CENSORED
    assert result.event.reviewer_action is SystematicRegimeReviewerAction.DATA_QUALITY_REVIEW
    assert result.event.artifact_sha256s == ()
    assert result.event.outcome_artifact_sha256 is None
    assert result.event.reasons == ("censored_missed_target_session",)


def test_review_store_is_private_append_only_and_query_only(tmp_path: Path) -> None:
    experiment, systematic, card, target_source = _completed_trial(tmp_path)
    reviews = SystematicRegimeReviewStore(tmp_path / "reviews.sqlite3")
    result = review_systematic_regime_trial(
        experiment_ledger=ExperimentLedgerReader(experiment.path),
        systematic_store=systematic,
        daily_sources=(target_source,),
        reviews=reviews,
        card_id=card.card_id,
        reviewed_at=target_source.observed_at + dt.timedelta(minutes=1),
    )

    assert result.created is True
    assert stat.S_IMODE(reviews.path.stat().st_mode) == 0o600
    with sqlite3.connect(reviews.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute("UPDATE systematic_regime_review_events SET payload_json = '{}'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute("DELETE FROM systematic_regime_review_events")
    with (
        SystematicRegimeReviewReader(reviews.path).reader_connection() as connection,
        pytest.raises(sqlite3.OperationalError),
    ):
        _ = connection.execute("DELETE FROM systematic_regime_review_events")


def test_reviewer_import_closure_excludes_broker_and_allocation_modules() -> None:
    script = """
import json
import sys
import trading_agent.systematic_regime_reviewer
print(json.dumps(sorted(name for name in sys.modules if name.startswith('trading_agent.'))))
"""
    completed = subprocess.run(
        (sys.executable, "-c", script),
        check=True,
        capture_output=True,
        text=True,
    )
    loaded_modules = json.loads(completed.stdout)
    forbidden = (
        "alpaca",
        "paper",
        "broker",
        "execution",
        "credential",
        "provider",
        "allocation_manager",
        "portfolio_manager",
    )

    assert not {module for module in loaded_modules if any(marker in module for marker in forbidden)}


def _completed_trial(
    tmp_path: Path,
) -> tuple[
    ExperimentLedgerStore,
    SystematicRegimeStore,
    SystematicRecommendationCard,
    SwingDailySource,
]:
    source = _source("risk_on")
    version = systematic_regime_strategy_version(CODE_VERSION)
    card = build_systematic_card(source, replay_systematic_regime(source), version)
    experiment = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    systematic = SystematicRegimeStore(tmp_path / "systematic.sqlite3")
    with systematic.writer() as writer:
        _ = writer.append_card(card)
    registration = register_systematic_regime_trial(experiment, card, CODE_VERSION)
    bounds = regular_session_bounds(card.target_session)
    assert bounds is not None
    _ = start_systematic_regime_trial(
        experiment,
        card,
        bounds[0] + dt.timedelta(minutes=1),
    )
    target_source = _extend_source(source, card.target_session)
    finalized = finalize_systematic_regime_trial(
        experiment,
        systematic,
        card,
        target_source,
    )
    assert finalized.event.trial_id == registration.registration.trial_id
    return experiment, systematic, card, target_source
