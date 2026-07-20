from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.us_news_catalyst_trial_fixtures import (
    OBSERVED,
    REGISTRATION_MANIFEST,
    SESSION_DATE,
    STRATEGY_VERSION,
    projected_evidence,
    registered_ledger,
)
from trading_agent.alpaca_news_opportunity_evidence_artifact import (
    publish_alpaca_news_opportunity_evidence,
)
from trading_agent.us_news_catalyst_day_session_audit import UsNewsCatalystDaySessionPhase
from trading_agent.us_news_catalyst_day_session_commands import us_news_catalyst_day_session_action
from trading_agent.us_news_catalyst_day_session_manifest import (
    UsNewsCatalystDaySessionIdentity,
    UsNewsCatalystDaySessionManifest,
    UsNewsCatalystDaySessionPaths,
    build_us_news_catalyst_day_session_manifest,
)
from trading_agent.us_news_catalyst_day_session_supervisor import UsNewsCatalystDaySessionActionStatus
from trading_agent.us_news_catalyst_opportunity_artifact import (
    publish_us_news_catalyst_opportunity_projection,
)
from trading_agent.us_news_catalyst_trial import (
    register_us_news_catalyst_daily_trial,
    start_us_news_catalyst_trial,
)
from trading_agent.us_news_catalyst_trial_models import UsNewsCatalystDailyTrialRegistrationRequest


def test_actions_follow_preopen_start_and_setup_windows(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    ledger = registered_ledger(tmp_path)
    projection, evidence = projected_evidence(ledger)
    projection_path, _ = publish_us_news_catalyst_opportunity_projection(
        manifest.paths.projection_root,
        projection,
    )
    _ = publish_alpaca_news_opportunity_evidence(manifest.paths.evidence_root, evidence)

    register = us_news_catalyst_day_session_action(
        manifest, UsNewsCatalystDaySessionPhase.REGISTER, dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)
    )
    start = us_news_catalyst_day_session_action(
        manifest, UsNewsCatalystDaySessionPhase.START, OBSERVED + dt.timedelta(seconds=1)
    )

    assert register.status is UsNewsCatalystDaySessionActionStatus.EXECUTE
    assert register.command is not None and register.command[1] == "register"
    assert start.status is UsNewsCatalystDaySessionActionStatus.EXECUTE
    assert start.command is not None and str(projection_path) in start.command

    trial = register_us_news_catalyst_daily_trial(
        ledger,
        UsNewsCatalystDailyTrialRegistrationRequest(
            strategy_version=STRATEGY_VERSION,
            code_version="us-news-catalyst-baseline-fixture-v1",
            session_date=SESSION_DATE,
            registered_at=dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC),
        ),
    )
    _ = start_us_news_catalyst_trial(
        ledger,
        trial.registration.trial_id,
        projection,
        evidence,
        manifest.paths.artifact_root,
        started_at=OBSERVED + dt.timedelta(seconds=1),
    )
    early = us_news_catalyst_day_session_action(
        manifest, UsNewsCatalystDaySessionPhase.COLLECT, OBSERVED + dt.timedelta(minutes=29)
    )
    boundary = us_news_catalyst_day_session_action(
        manifest, UsNewsCatalystDaySessionPhase.COLLECT, OBSERVED + dt.timedelta(minutes=30)
    )
    collect = us_news_catalyst_day_session_action(
        manifest,
        UsNewsCatalystDaySessionPhase.COLLECT,
        OBSERVED + dt.timedelta(minutes=30, seconds=1),
    )

    assert early.status is UsNewsCatalystDaySessionActionStatus.WAITING
    assert boundary.status is UsNewsCatalystDaySessionActionStatus.WAITING
    assert collect.status is UsNewsCatalystDaySessionActionStatus.EXECUTE
    assert collect.command is not None and "run_us_news_catalyst_cohort_collect.py" in collect.command[0]


def _manifest(tmp_path: Path) -> UsNewsCatalystDaySessionManifest:
    root = tmp_path.absolute()
    ledger = root / "experiment-ledger.sqlite3"
    paths = UsNewsCatalystDaySessionPaths(
        experiment_ledger=ledger,
        registration_manifest=REGISTRATION_MANIFEST.absolute(),
        projection_root=root / "projections",
        evidence_root=root / "evidence",
        security_master_store=root / "security.sqlite3",
        artifact_root=root / "artifacts",
        plan_root=root / "plans",
        profile_root=root / "profiles",
        runtime_root=root / "runtime",
        canonical_root=root / "canonical",
        feature_root=root / "features",
        receipt_root=root / "receipts",
        review_root=root / "reviews",
        audit_store=root / "audit.sqlite3",
        output_root=root / "reports",
        secret_path=root / "alpaca.env",
    )
    return build_us_news_catalyst_day_session_manifest(
        UsNewsCatalystDaySessionIdentity(
            strategy_version=STRATEGY_VERSION,
            code_version="us-news-catalyst-baseline-fixture-v1",
            session_date=SESSION_DATE,
            created_at=dt.datetime(2026, 7, 21, 12, tzinfo=dt.UTC),
            paths=paths,
        )
    )
