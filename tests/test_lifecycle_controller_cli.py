from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

import run_lifecycle_controller as controller_cli
from tests.test_lifecycle_controller import (
    DECIDED_AT,
    ORB_CONTRACT,
    SESSION_DATE,
    _append_day_sources,
    _seed_base_sources,
)
from trading_agent.adaptive_evaluation_models import AdaptiveAction
from trading_agent.lane_contract_keys import lane_daily_snapshot_key
from trading_agent.lane_review_keys import lane_review_event_key
from trading_agent.lifecycle_controller import PROMOTION_BLOCKERS

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_lifecycle_controller.py"
REPORT_NAME = "lifecycle_controller_ko.md"
UV = shutil.which("uv")
assert UV is not None
UV_DIRECTORY = Path(UV).parent


def _arguments(tmp_path: Path) -> tuple[str, ...]:
    return (
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--lane-registry",
        str(tmp_path / "lane.sqlite3"),
        "--review-ledger",
        str(tmp_path / "review.sqlite3"),
        "--session-date",
        SESSION_DATE.isoformat(),
        "--output-dir",
        str(tmp_path / "report"),
    )


def _seed_cli_sources(tmp_path: Path, action: AdaptiveAction):
    lanes, reviews, experiments = _seed_base_sources(tmp_path)
    snapshot, review = _append_day_sources(
        lanes,
        reviews,
        session_date=SESSION_DATE,
        action=action,
        finalized_at=dt.datetime(2026, 7, 15, 20, 10, tzinfo=dt.UTC),
        reviewed_at=dt.datetime(2026, 7, 15, 20, 20, tzinfo=dt.UTC),
    )
    return experiments, snapshot, review


def test_lifecycle_controller_help_is_local_only() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 0
    assert "--experiment-ledger" in completed.stdout
    assert "--lane-registry" in completed.stdout
    assert "--review-ledger" in completed.stdout
    assert "--session-date" in completed.stdout
    assert "--output-dir" in completed.stdout
    for forbidden in ("--credential", "--endpoint", "--arm", "--strategy", "--force"):
        assert forbidden not in completed.stdout


def test_lifecycle_controller_unknown_option_creates_nothing(tmp_path: Path) -> None:
    completed = subprocess.run(
        (*((str(SCRIPT), *_arguments(tmp_path))), "--unknown-option"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 2
    assert not (tmp_path / "experiment.sqlite3").exists()
    assert not (tmp_path / "lane.sqlite3").exists()
    assert not (tmp_path / "review.sqlite3").exists()
    assert not (tmp_path / "report").exists()


def test_lifecycle_controller_missing_sources_are_redacted_and_non_mutating(
    tmp_path: Path,
) -> None:
    return_code = controller_cli.main(_arguments(tmp_path), decided_at=DECIDED_AT)

    assert return_code == 1
    assert not (tmp_path / "experiment.sqlite3").exists()
    assert not (tmp_path / "lane.sqlite3").exists()
    assert not (tmp_path / "review.sqlite3").exists()
    report = _report(tmp_path)
    assert "result: blocked_source" in report
    assert "external broker mutation: 0" in report
    _assert_redacted(report, tmp_path)


def test_lifecycle_controller_collecting_is_a_successful_no_change(tmp_path: Path) -> None:
    experiments, _, _ = _seed_cli_sources(tmp_path, AdaptiveAction.COLLECTING)

    return_code = controller_cli.main(_arguments(tmp_path), decided_at=DECIDED_AT)

    assert return_code == 0
    report = _report(tmp_path)
    assert "result: completed" in report
    assert "outcome: no_change" in report
    assert "created: false" in report
    assert "to_state: none" in report
    assert "policy_blockers: none" in report
    assert len(experiments.lifecycle_events(ORB_CONTRACT.strategy_version)) == 1
    _assert_redacted(report, tmp_path)


def test_lifecycle_controller_suspend_and_replay_are_redacted(tmp_path: Path) -> None:
    experiments, snapshot, review = _seed_cli_sources(tmp_path, AdaptiveAction.SUSPEND)

    first = controller_cli.main(_arguments(tmp_path), decided_at=DECIDED_AT)
    first_report = _report(tmp_path)
    replay = controller_cli.main(
        _arguments(tmp_path),
        decided_at=DECIDED_AT + dt.timedelta(minutes=5),
    )
    replay_report = _report(tmp_path)

    assert first == 0
    assert replay == 0
    assert "outcome: transitioned" in first_report
    assert "created: true" in first_report
    assert "from_state: experimental_shadow" in first_report
    assert "to_state: suspended" in first_report
    assert "outcome: transitioned" in replay_report
    assert "created: false" in replay_report
    assert len(experiments.lifecycle_events(ORB_CONTRACT.strategy_version)) == 2
    for report in (first_report, replay_report):
        _assert_redacted(
            report,
            tmp_path,
            str(lane_daily_snapshot_key(snapshot)),
            str(lane_review_event_key(review)),
        )


def test_lifecycle_controller_promotion_is_blocked_without_transition(
    tmp_path: Path,
) -> None:
    experiments, _, _ = _seed_cli_sources(tmp_path, AdaptiveAction.PROMOTION_REVIEW)

    return_code = controller_cli.main(_arguments(tmp_path), decided_at=DECIDED_AT)

    assert return_code == 0
    report = _report(tmp_path)
    assert "outcome: blocked" in report
    assert "created: false" in report
    assert f"policy_blockers: {','.join(PROMOTION_BLOCKERS)}" in report
    assert len(experiments.lifecycle_events(ORB_CONTRACT.strategy_version)) == 1
    _assert_redacted(report, tmp_path)


def _report(tmp_path: Path) -> str:
    return (tmp_path / "report" / REPORT_NAME).read_text(encoding="utf-8")


def _assert_redacted(report: str, tmp_path: Path, *keys: str) -> None:
    forbidden = (
        str(tmp_path),
        ORB_CONTRACT.strategy_version,
        "five_day_clear_degradation",
        "daily_record_id",
        "snapshot_key",
        "review_key",
        "account",
        "broker_order",
        "APCA_API",
        "https://",
        *keys,
    )
    assert all(value not in report for value in forbidden)


def _direct_execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV_DIRECTORY}:/usr/bin:/bin"
    return environment
