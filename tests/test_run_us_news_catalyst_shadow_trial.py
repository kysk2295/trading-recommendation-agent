from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

from run_us_news_catalyst_shadow_trial import REPORT_NAME, main
from tests.test_us_news_catalyst_shadow_trial import _observation_manifest
from tests.us_news_catalyst_trial_fixtures import (
    OBSERVED,
    PROJECT,
    REGISTRATION_MANIFEST,
    SESSION_DATE,
    STRATEGY_VERSION,
    projected_evidence,
    registered_ledger,
)
from trading_agent.alpaca_news_opportunity_evidence_artifact import (
    publish_alpaca_news_opportunity_evidence,
)
from trading_agent.us_news_catalyst_opportunity_artifact import (
    publish_us_news_catalyst_opportunity_projection,
)
from trading_agent.us_news_catalyst_trial_artifact import (
    publish_us_news_catalyst_setup_observation_manifest,
)


def test_shadow_trial_cli_help_is_executable() -> None:
    completed = subprocess.run(
        ["uv", "run", "python", "run_us_news_catalyst_shadow_trial.py", "--help"],
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "register" in completed.stdout
    assert "finalize" in completed.stdout
    assert "review" in completed.stdout


def test_shadow_trial_cli_register_start_finalize_review_and_replay(tmp_path: Path) -> None:
    ledger = registered_ledger(tmp_path)
    projection, evidence = projected_evidence(ledger)
    projection_path, _ = publish_us_news_catalyst_opportunity_projection(
        tmp_path / "inputs",
        projection,
    )
    evidence_path, _ = publish_alpaca_news_opportunity_evidence(tmp_path / "inputs", evidence)
    output = tmp_path / "reports"
    artifacts = tmp_path / "artifacts"
    base = ["--experiment-ledger", str(ledger.path), "--output-dir", str(output)]

    assert main(
        [
            "register",
            "--registration-manifest",
            str(REGISTRATION_MANIFEST),
            "--session-date",
            SESSION_DATE.isoformat(),
            *base,
        ],
        clock=lambda: dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC),
    ) == 0
    trial_id = ledger.multi_market_trials()[0].registration.trial_id
    start_args = [
        "start",
        "--trial-id",
        trial_id,
        "--projection",
        str(projection_path),
        "--evidence",
        str(evidence_path),
        "--artifact-root",
        str(artifacts),
        *base,
    ]
    assert main(start_args, clock=lambda: OBSERVED + dt.timedelta(seconds=1)) == 0
    assert main(start_args, clock=lambda: OBSERVED + dt.timedelta(seconds=2)) == 0
    cohort_path = next(artifacts.glob("us_news_catalyst_cohort_*.json"))
    cohort_id = cohort_path.stem.removeprefix("us_news_catalyst_cohort_")
    observations, _ = publish_us_news_catalyst_setup_observation_manifest(
        tmp_path / "observations",
        _observation_manifest(cohort_id, trial_id),
    )
    finalize_args = [
        "finalize",
        "--trial-id",
        trial_id,
        "--cohort",
        str(cohort_path),
        "--observation-manifest",
        str(observations),
        "--artifact-root",
        str(artifacts),
        *base,
    ]
    assert main(finalize_args, clock=lambda: OBSERVED + dt.timedelta(minutes=31)) == 0
    review_args = [
        "review",
        "--strategy-version",
        STRATEGY_VERSION,
        "--as-of-session",
        SESSION_DATE.isoformat(),
        "--artifact-root",
        str(artifacts),
        "--review-root",
        str(tmp_path / "reviews"),
        *base,
    ]
    close = dt.datetime(2026, 7, 21, 20, 1, tzinfo=dt.UTC)
    assert main(review_args, clock=lambda: close) == 2
    assert main(review_args, clock=lambda: close + dt.timedelta(minutes=1)) == 2
    report = (output / REPORT_NAME).read_text()
    assert "continue_collection" in report
    assert "order mutation: 0" in report


def test_shadow_trial_cli_bad_input_is_redacted_and_blocked(tmp_path: Path) -> None:
    output = tmp_path / "reports"

    result = main(
        [
            "register",
            "--registration-manifest",
            str(tmp_path / "missing-secret-name.json"),
            "--session-date",
            SESSION_DATE.isoformat(),
            "--experiment-ledger",
            str(tmp_path / "ledger.sqlite3"),
            "--output-dir",
            str(output),
        ],
        clock=lambda: dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC),
    )

    report = (output / REPORT_NAME).read_text()
    assert result == 1
    assert "blocked" in report
    assert "missing-secret-name" not in report
