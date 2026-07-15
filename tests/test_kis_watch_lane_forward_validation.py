from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import typer

import run_kis_paper_watch as watch
from trading_agent.store import PaperStore

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_kis_paper_watch.py"
SESSION_DATE = dt.date(2026, 7, 10)
AFTER_CLOSE = dt.datetime(2026, 7, 10, 16, 1, tzinfo=ZoneInfo("America/New_York"))
_UV = shutil.which("uv")
assert _UV is not None
UV = Path(_UV)


def test_lane_forward_configuration_is_all_or_none_and_orb_only(tmp_path: Path) -> None:
    assert (
        watch._lane_forward_validation_config(
            watch.StrategyMode.ORB,
            None,
            None,
            None,
            None,
        )
        is None
    )
    with pytest.raises(typer.BadParameter, match="모두 함께"):
        _ = watch._lane_forward_validation_config(
            watch.StrategyMode.ORB,
            tmp_path / "execution.sqlite3",
            None,
            None,
            None,
        )

    config = _config(tmp_path)
    assert (
        watch._lane_forward_validation_config(
            watch.StrategyMode.ORB,
            config.execution_database,
            config.lane_registry,
            config.review_ledger,
            config.output_dir,
        )
        == config
    )
    with pytest.raises(typer.BadParameter, match="ORB"):
        _ = watch._lane_forward_validation_config(
            watch.StrategyMode.VWAP_RECLAIM,
            config.execution_database,
            config.lane_registry,
            config.review_ledger,
            config.output_dir,
        )


def test_lane_forward_child_command_is_exact_and_has_no_authority_options(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    command = watch._lane_forward_validation_command(
        tmp_path / "session",
        AFTER_CLOSE,
        config,
    )

    assert command == (
        str(PROJECT / "run_orb_lane_forward_validation.py"),
        str(tmp_path / "session"),
        "--session-date",
        SESSION_DATE.isoformat(),
        "--execution-database",
        str(config.execution_database),
        "--lane-registry",
        str(config.lane_registry),
        "--review-ledger",
        str(config.review_ledger),
        "--output-dir",
        str(config.output_dir),
    )
    joined = " ".join(command).lower()
    for forbidden in (
        "--arm",
        "--credential",
        "--endpoint",
        "--fixture",
        "--force",
        "mutation_smoke",
        "paper-api.alpaca.markets",
        "api.alpaca.markets",
    ):
        assert forbidden not in joined


def test_closed_orb_watch_runs_research_then_lane_forward_validation(
    tmp_path: Path,
) -> None:
    _ = PaperStore(tmp_path / "paper_recommendations.sqlite3")
    config = _config(tmp_path / "control")
    calls: list[tuple[tuple[str, ...], Path]] = []

    def run(command: tuple[str, ...], audit_path: Path) -> int:
        calls.append((command, audit_path))
        return 0

    result = watch.run_session_metrics(
        tmp_path,
        AFTER_CLOSE,
        run,
        strategy=watch.StrategyMode.ORB,
        lane_forward_validation=config,
    )

    assert result == 0
    assert [Path(command[0]).name for command, _audit in calls] == [
        "run_paper_metrics.py",
        "run_daily_research_record.py",
        "run_adaptive_strategy_evaluation.py",
        "run_orb_lane_forward_validation.py",
    ]
    assert calls[-1] == (
        watch._lane_forward_validation_command(tmp_path, AFTER_CLOSE, config),
        tmp_path / "post_session_lane_forward_validation_cycles.csv",
    )


@pytest.mark.parametrize("failure_index", range(4))
def test_post_session_failure_never_starts_a_later_phase(
    tmp_path: Path,
    failure_index: int,
) -> None:
    _ = PaperStore(tmp_path / "paper_recommendations.sqlite3")
    config = _config(tmp_path / "control")
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], _audit_path: Path) -> int:
        calls.append(command)
        return 7 if len(calls) - 1 == failure_index else 0

    result = watch.run_session_metrics(
        tmp_path,
        AFTER_CLOSE,
        run,
        strategy=watch.StrategyMode.ORB,
        lane_forward_validation=config,
    )

    assert result == 7
    assert len(calls) == failure_index + 1


def test_watch_help_exposes_only_path_configuration_for_lane_forward() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 0, completed.stderr
    for option in (
        "--lane-execution-database",
        "--lane-registry",
        "--lane-review-ledger",
        "--lane-forward-output-dir",
    ):
        assert option in completed.stdout
    for forbidden in ("--arm", "credential", "endpoint", "fixture", "force"):
        assert forbidden not in completed.stdout.lower()


def test_watch_rejects_partial_or_non_orb_lane_configuration_before_session() -> None:
    partial = subprocess.run(
        (str(SCRIPT), "--lane-execution-database", "execution.sqlite3"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )
    non_orb = subprocess.run(
        (
            str(SCRIPT),
            "--strategy",
            "vwap_reclaim",
            "--lane-execution-database",
            "execution.sqlite3",
            "--lane-registry",
            "registry.sqlite3",
            "--lane-review-ledger",
            "review.sqlite3",
            "--lane-forward-output-dir",
            "forward-output",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert partial.returncode == 2
    assert "모두 함께" in f"{partial.stdout}\n{partial.stderr}"
    assert non_orb.returncode == 2
    assert "ORB" in f"{non_orb.stdout}\n{non_orb.stderr}"


def _config(root: Path) -> watch.LaneForwardValidationConfig:
    return watch.LaneForwardValidationConfig(
        execution_database=root / "execution.sqlite3",
        lane_registry=root / "lane_registry.sqlite3",
        review_ledger=root / "review.sqlite3",
        output_dir=root / "forward-output",
    )


def _direct_execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    environment["COLUMNS"] = "220"
    return environment
