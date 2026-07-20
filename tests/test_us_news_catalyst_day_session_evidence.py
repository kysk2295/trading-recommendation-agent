from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_us_news_catalyst_day_session_commands import _manifest
from tests.us_news_catalyst_trial_fixtures import (
    CODE_VERSION,
    OBSERVED,
    SESSION_DATE,
    STRATEGY_VERSION,
    projected_evidence,
    registered_ledger,
)
from trading_agent.us_news_catalyst_day_session_audit import UsNewsCatalystDaySessionPhase
from trading_agent.us_news_catalyst_day_session_evidence import (
    resolve_us_news_catalyst_day_session_evidence,
)
from trading_agent.us_news_catalyst_trial import (
    register_us_news_catalyst_daily_trial,
    start_us_news_catalyst_trial,
)
from trading_agent.us_news_catalyst_trial_models import UsNewsCatalystDailyTrialRegistrationRequest


def test_domain_evidence_recovers_start_and_marks_missed_setup_windows(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    ledger = registered_ledger(tmp_path)
    registration = register_us_news_catalyst_daily_trial(
        ledger,
        UsNewsCatalystDailyTrialRegistrationRequest(
            strategy_version=STRATEGY_VERSION,
            code_version=CODE_VERSION,
            session_date=SESSION_DATE,
            registered_at=dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC),
        ),
    )
    projection, evidence = projected_evidence(ledger)

    registered = resolve_us_news_catalyst_day_session_evidence(
        manifest,
        UsNewsCatalystDaySessionPhase.REGISTER,
        OBSERVED,
    )
    not_started = resolve_us_news_catalyst_day_session_evidence(
        manifest,
        UsNewsCatalystDaySessionPhase.START,
        OBSERVED,
    )
    _ = start_us_news_catalyst_trial(
        ledger,
        registration.registration.trial_id,
        projection,
        evidence,
        manifest.paths.artifact_root,
        started_at=OBSERVED + dt.timedelta(seconds=1),
    )
    started = resolve_us_news_catalyst_day_session_evidence(
        manifest,
        UsNewsCatalystDaySessionPhase.START,
        OBSERVED,
    )
    expired = OBSERVED + dt.timedelta(minutes=33)
    collection = resolve_us_news_catalyst_day_session_evidence(
        manifest,
        UsNewsCatalystDaySessionPhase.COLLECT,
        expired,
    )
    observation = resolve_us_news_catalyst_day_session_evidence(
        manifest,
        UsNewsCatalystDaySessionPhase.OBSERVE,
        expired,
    )

    assert registered is not None
    assert not_started is None
    assert started is not None
    assert collection is not None and collection.skipped_reason == "collection_window_missed"
    assert observation is not None and observation.skipped_reason == "observation_window_missed"
