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
from trading_agent import kis_watch_research_projection as watch_research
from trading_agent.orb_forward_trial import OrbTrialFailurePhase
from trading_agent.store import PaperStore

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_kis_paper_watch.py"
SESSION_DATE = dt.date(2026, 7, 10)
OPENED_AT = dt.datetime(2026, 7, 10, 10, 0, tzinfo=ZoneInfo("America/New_York"))
AFTER_CLOSE = dt.datetime(2026, 7, 10, 16, 1, tzinfo=ZoneInfo("America/New_York"))
_UV = shutil.which("uv")
assert _UV is not None
UV = Path(_UV)


def test_research_projection_watch_configuration_is_all_or_none(
    tmp_path: Path,
) -> None:
    assert watch._research_projection_watch_config(None, None, None) is None

    with pytest.raises(typer.BadParameter, match="research projection"):
        _ = watch._research_projection_watch_config(
            tmp_path / "scanner.sqlite3",
            tmp_path / "canonical",
            None,
        )

    assert watch._research_projection_watch_config(
        tmp_path / "scanner.sqlite3",
        tmp_path / "canonical",
        tmp_path / "security.sqlite3",
    ) == watch_research.ResearchProjectionWatchConfig(
        tmp_path / "scanner.sqlite3",
        tmp_path / "canonical",
        tmp_path / "security.sqlite3",
    )


def test_watch_scan_command_passes_operational_research_projection_paths(
    tmp_path: Path,
) -> None:
    research = watch_research.ResearchProjectionWatchConfig(
        tmp_path / "scanner.sqlite3",
        tmp_path / "canonical",
        tmp_path / "security.sqlite3",
    )

    command = watch._scan_command(
        tmp_path / "session",
        watch.WatchScanConfig(watch.StrategyMode.ORB, 7, 2, research),
    )

    assert command == (
        str(PROJECT / "run_kis_paper_scan.py"),
        "--output-dir",
        str(tmp_path / "session"),
        "--strategy",
        "orb",
        "--top",
        "7",
        "--max-pages",
        "2",
        "--research-projection-store",
        str(research.projection_store),
        "--research-canonical-root",
        str(research.canonical_root),
        "--research-security-master-store",
        str(research.security_master_store),
    )
    joined = " ".join(command).lower()
    for forbidden in ("--arm", "--credential", "--endpoint", "--fixture", "--force"):
        assert forbidden not in joined


def test_trial_configuration_is_opt_in_orb_only_and_requires_lane_paths(
    tmp_path: Path,
) -> None:
    lane = _lane_config(tmp_path)

    assert watch._orb_trial_config(watch.StrategyMode.ORB, None, lane) is None
    with pytest.raises(typer.BadParameter, match="lane forward"):
        _ = watch._orb_trial_config(
            watch.StrategyMode.ORB,
            tmp_path / "experiment.sqlite3",
            None,
        )
    config = watch._orb_trial_config(
        watch.StrategyMode.ORB,
        tmp_path / "experiment.sqlite3",
        lane,
    )
    assert config == _trial_config(tmp_path)
    with pytest.raises(typer.BadParameter, match="ORB"):
        _ = watch._orb_trial_config(
            watch.StrategyMode.VWAP_RECLAIM,
            tmp_path / "experiment.sqlite3",
            lane,
        )


def test_trial_child_commands_are_exact_and_have_no_external_authority(
    tmp_path: Path,
) -> None:
    config = _trial_config(tmp_path)
    session = tmp_path / "session"
    audit = session / "post_session_metrics_cycles.csv"

    register = watch._orb_trial_register_command(OPENED_AT, config)
    start = watch._orb_trial_start_command(OPENED_AT, config)
    finalize = watch._orb_trial_finalize_command(session, AFTER_CLOSE, config)
    fail = watch._orb_trial_fail_command(
        AFTER_CLOSE,
        config,
        OrbTrialFailurePhase.PAPER_METRICS,
        audit,
    )

    base = (
        "--experiment-ledger",
        str(config.experiment_ledger),
        "--session-date",
        SESSION_DATE.isoformat(),
    )
    assert register == (
        str(PROJECT / "run_orb_forward_trial.py"),
        "register",
        *base[:2],
        "--lane-registry",
        str(config.lane_forward.lane_registry),
        *base[2:],
        "--output-dir",
        str(config.lane_forward.output_dir / "trials" / SESSION_DATE.isoformat() / "register"),
    )
    assert start == (
        str(PROJECT / "run_orb_forward_trial.py"),
        "start",
        *base,
        "--output-dir",
        str(config.lane_forward.output_dir / "trials" / SESSION_DATE.isoformat() / "start"),
    )
    assert finalize == (
        str(PROJECT / "run_orb_forward_trial.py"),
        "finalize",
        str(session),
        *base[:2],
        "--lane-registry",
        str(config.lane_forward.lane_registry),
        "--review-ledger",
        str(config.lane_forward.review_ledger),
        *base[2:],
        "--output-dir",
        str(config.lane_forward.output_dir / "trials" / SESSION_DATE.isoformat() / "finalize"),
    )
    assert fail == (
        str(PROJECT / "run_orb_forward_trial.py"),
        "fail",
        *base,
        "--phase",
        OrbTrialFailurePhase.PAPER_METRICS.value,
        "--audit",
        str(audit),
        "--output-dir",
        str(config.lane_forward.output_dir / "trials" / SESSION_DATE.isoformat() / "fail"),
    )
    joined = " ".join((*register, *start, *finalize, *fail)).lower()
    for forbidden in (
        "--arm",
        "--credential",
        "--endpoint",
        "--fixture",
        "--force",
        "paper-api.alpaca.markets",
        "api.alpaca.markets",
    ):
        assert forbidden not in joined


def test_open_watch_registers_and_starts_trial_before_first_provider_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    class FixedDatetime(dt.datetime):
        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> FixedDatetime:
            fixed = cls(
                2026,
                7,
                10,
                10,
                0,
                tzinfo=ZoneInfo("America/New_York"),
            )
            return fixed if tz is None else fixed.astimezone(tz)

    def run(command: tuple[str, ...], _audit: Path) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr(watch.dt, "datetime", FixedDatetime)
    monkeypatch.setattr(watch, "regular_session_is_open", lambda _value: True)
    monkeypatch.setattr(watch, "wait_for_eod_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(watch, "_run_and_audit", run)

    watch.main(
        output_dir=str(tmp_path / "session"),
        cycles=1,
        interval_seconds=1.0,
        lane_execution_database=tmp_path / "execution.sqlite3",
        lane_registry=tmp_path / "lane.sqlite3",
        lane_review_ledger=tmp_path / "review.sqlite3",
        lane_forward_output_dir=tmp_path / "control",
        experiment_ledger=tmp_path / "experiment.sqlite3",
    )

    assert [(Path(command[0]).name, command[1]) for command in calls[:3]] == [
        ("run_orb_forward_trial.py", "register"),
        ("run_orb_forward_trial.py", "start"),
        ("run_kis_paper_scan.py", "--output-dir"),
    ]


@pytest.mark.parametrize("failure_index", (0, 1))
def test_trial_setup_failure_stops_before_provider_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_index: int,
) -> None:
    calls: list[tuple[str, ...]] = []

    class FixedDatetime(dt.datetime):
        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> FixedDatetime:
            fixed = cls(
                2026,
                7,
                10,
                10,
                0,
                tzinfo=ZoneInfo("America/New_York"),
            )
            return fixed if tz is None else fixed.astimezone(tz)

    def run(command: tuple[str, ...], _audit: Path) -> int:
        calls.append(command)
        return 7 if len(calls) - 1 == failure_index else 0

    monkeypatch.setattr(watch.dt, "datetime", FixedDatetime)
    monkeypatch.setattr(watch, "regular_session_is_open", lambda _value: True)
    monkeypatch.setattr(watch, "_run_and_audit", run)

    with pytest.raises(typer.Exit) as raised:
        watch.main(
            output_dir=str(tmp_path / "session"),
            cycles=1,
            interval_seconds=1.0,
            lane_execution_database=tmp_path / "execution.sqlite3",
            lane_registry=tmp_path / "lane.sqlite3",
            lane_review_ledger=tmp_path / "review.sqlite3",
            lane_forward_output_dir=tmp_path / "control",
            experiment_ledger=tmp_path / "experiment.sqlite3",
        )

    assert raised.value.exit_code == 1
    assert len(calls) == failure_index + 1
    assert all(Path(command[0]).name == "run_orb_forward_trial.py" for command in calls)


def test_successful_post_session_chain_finalizes_trial_last(tmp_path: Path) -> None:
    _ = PaperStore(tmp_path / "paper_recommendations.sqlite3")
    lane = _lane_config(tmp_path / "control")
    trial = watch.OrbTrialConfig(tmp_path / "experiment.sqlite3", lane)
    calls: list[tuple[tuple[str, ...], Path]] = []

    def run(command: tuple[str, ...], audit: Path) -> int:
        calls.append((command, audit))
        return 0

    result = watch.run_session_metrics(
        tmp_path,
        AFTER_CLOSE,
        run,
        strategy=watch.StrategyMode.ORB,
        lane_forward_validation=lane,
        orb_trial=trial,
    )

    assert result == 0
    assert [Path(command[0]).name for command, _ in calls] == [
        "run_paper_metrics.py",
        "run_daily_research_record.py",
        "run_adaptive_strategy_evaluation.py",
        "run_orb_lane_forward_validation.py",
        "run_orb_forward_trial.py",
    ]
    assert calls[-1] == (
        watch._orb_trial_finalize_command(tmp_path, AFTER_CLOSE, trial),
        tmp_path / "post_session_orb_trial_terminal_cycles.csv",
    )


def test_session_metrics_rejects_trial_without_its_exact_lane_config(tmp_path: Path) -> None:
    trial = _trial_config(tmp_path)

    with pytest.raises(ValueError, match="exact ORB lane"):
        _ = watch.run_session_metrics(
            tmp_path,
            AFTER_CLOSE,
            lane_forward_validation=None,
            orb_trial=trial,
        )


@pytest.mark.parametrize(
    ("failure_index", "phase"),
    (
        (0, OrbTrialFailurePhase.PAPER_METRICS),
        (1, OrbTrialFailurePhase.DAILY_RESEARCH_RECORD),
        (2, OrbTrialFailurePhase.ADAPTIVE_EVALUATION),
        (3, OrbTrialFailurePhase.LANE_FORWARD_VALIDATION),
    ),
)
def test_post_session_failure_appends_audited_failed_terminal_then_stops(
    tmp_path: Path,
    failure_index: int,
    phase: OrbTrialFailurePhase,
) -> None:
    _ = PaperStore(tmp_path / "paper_recommendations.sqlite3")
    lane = _lane_config(tmp_path / "control")
    trial = watch.OrbTrialConfig(tmp_path / "experiment.sqlite3", lane)
    calls: list[tuple[tuple[str, ...], Path]] = []

    def run(command: tuple[str, ...], audit: Path) -> int:
        calls.append((command, audit))
        return 7 if len(calls) - 1 == failure_index else 0

    result = watch.run_session_metrics(
        tmp_path,
        AFTER_CLOSE,
        run,
        strategy=watch.StrategyMode.ORB,
        lane_forward_validation=lane,
        orb_trial=trial,
    )

    assert result == 7
    assert len(calls) == failure_index + 2
    failure_command, _ = calls[-1]
    assert Path(failure_command[0]).name == "run_orb_forward_trial.py"
    assert failure_command[1] == "fail"
    assert failure_command[failure_command.index("--phase") + 1] == phase.value
    failed_phase_audit = calls[failure_index][1]
    assert failure_command[failure_command.index("--audit") + 1] == str(failed_phase_audit)


def test_watch_help_and_bad_configuration_expose_only_trial_ledger_path() -> None:
    help_result = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )
    partial = subprocess.run(
        (str(SCRIPT), "--experiment-ledger", "experiment.sqlite3"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert help_result.returncode == 0
    assert "--experiment-ledger" in help_result.stdout
    for forbidden in ("--arm", "credential", "endpoint", "fixture", "force"):
        assert forbidden not in help_result.stdout.lower()
    assert partial.returncode == 2
    assert "lane forward" in f"{partial.stdout}\n{partial.stderr}"


def _lane_config(root: Path) -> watch.LaneForwardValidationConfig:
    return watch.LaneForwardValidationConfig(
        execution_database=root / "execution.sqlite3",
        lane_registry=root / "lane.sqlite3",
        review_ledger=root / "review.sqlite3",
        output_dir=root / "forward",
    )


def _trial_config(root: Path) -> watch.OrbTrialConfig:
    return watch.OrbTrialConfig(root / "experiment.sqlite3", _lane_config(root))


def _direct_execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    environment["COLUMNS"] = "220"
    return environment
