#!/usr/bin/env -S uv run --python 3.12 --with httpx2[http2,brotli,zstd] --with pydantic --with rich --with typer python

from __future__ import annotations

import datetime as dt
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from rich import print as rprint

from trading_agent.engine import finalize_due_recommendations
from trading_agent.kis_eod_watch import EodWaitConfig, eod_catchup_command, wait_for_eod_ready
from trading_agent.kis_live import (
    regular_session_is_open,
)
from trading_agent.kis_watch_wait import (
    PremarketWaitConfig,
    SessionWaitConfig,
    collect_premarket_until_regular_open,
    wait_for_session_open,
)
from trading_agent.orb_forward_trial import OrbTrialFailurePhase
from trading_agent.replay import write_report
from trading_agent.scan_cycle import (
    CycleRuntime,
    WatchConfig,
    append_cycle_audit,
    run_cycles,
)
from trading_agent.store import PaperStore
from trading_agent.strategy_factory import StrategyMode


@dataclass(frozen=True, slots=True)
class LaneForwardValidationConfig:
    execution_database: Path
    lane_registry: Path
    review_ledger: Path
    output_dir: Path


@dataclass(frozen=True, slots=True)
class OrbTrialConfig:
    experiment_ledger: Path
    lane_forward: LaneForwardValidationConfig


def _lane_forward_validation_config(
    strategy: StrategyMode,
    execution_database: Path | None,
    lane_registry: Path | None,
    review_ledger: Path | None,
    output_dir: Path | None,
) -> LaneForwardValidationConfig | None:
    if execution_database is None and lane_registry is None and review_ledger is None and output_dir is None:
        return None
    if execution_database is None or lane_registry is None or review_ledger is None or output_dir is None:
        raise typer.BadParameter("lane forward 경로 네 개는 모두 함께 지정해야 합니다")
    if strategy is not StrategyMode.ORB:
        raise typer.BadParameter("lane forward validation은 ORB 전략에서만 사용할 수 있습니다")
    return LaneForwardValidationConfig(
        execution_database,
        lane_registry,
        review_ledger,
        output_dir,
    )


def _orb_trial_config(
    strategy: StrategyMode,
    experiment_ledger: Path | None,
    lane_forward: LaneForwardValidationConfig | None,
) -> OrbTrialConfig | None:
    if experiment_ledger is None:
        return None
    if lane_forward is None:
        raise typer.BadParameter("ORB trial에는 lane forward 경로가 필요합니다")
    if strategy is not StrategyMode.ORB:
        raise typer.BadParameter("ORB trial은 ORB 전략에서만 사용할 수 있습니다")
    return OrbTrialConfig(experiment_ledger, lane_forward)


def finalize_session_output(output: Path, observed_at: dt.datetime) -> int:
    database = output / "paper_recommendations.sqlite3"
    if not database.is_file():
        return 0
    store = PaperStore(database)
    finalized = finalize_due_recommendations(store, observed_at)
    if finalized:
        write_report(output / "recommendations_ko.md", store)
    return finalized


def _scan_command(
    output: Path,
    strategy: StrategyMode,
    top: int,
    max_pages: int,
) -> tuple[str, ...]:
    return (
        str(Path(__file__).with_name("run_kis_paper_scan.py")),
        "--output-dir",
        str(output),
        "--strategy",
        strategy.value,
        "--top",
        str(top),
        "--max-pages",
        str(max_pages),
    )


def _premarket_scan_command(output: Path, top: int) -> tuple[str, ...]:
    return (
        str(Path(__file__).with_name("run_kis_premarket_scan.py")),
        "--output-dir",
        str(output),
        "--top",
        str(top),
    )


def _paper_metrics_command(output: Path) -> tuple[str, ...]:
    return (
        str(Path(__file__).with_name("run_paper_metrics.py")),
        str(output / "paper_recommendations.sqlite3"),
        "--output-dir",
        str(output / "paper_metrics"),
    )


def _daily_research_command(
    output: Path,
    observed_at: dt.datetime,
    strategy: StrategyMode,
) -> tuple[str, ...]:
    session_date = observed_at.astimezone(ZoneInfo("America/New_York")).date()
    return (
        str(Path(__file__).with_name("run_daily_research_record.py")),
        str(output),
        "--session-date",
        session_date.isoformat(),
        "--strategy",
        strategy.value,
    )


def _adaptive_evaluation_command(output: Path) -> tuple[str, ...]:
    return (
        str(Path(__file__).with_name("run_adaptive_strategy_evaluation.py")),
        str(output),
    )


def _lane_forward_validation_command(
    output: Path,
    observed_at: dt.datetime,
    config: LaneForwardValidationConfig,
) -> tuple[str, ...]:
    session_date = observed_at.astimezone(ZoneInfo("America/New_York")).date()
    return (
        str(Path(__file__).with_name("run_orb_lane_forward_validation.py")),
        str(output),
        "--session-date",
        session_date.isoformat(),
        "--execution-database",
        str(config.execution_database),
        "--lane-registry",
        str(config.lane_registry),
        "--review-ledger",
        str(config.review_ledger),
        "--output-dir",
        str(config.output_dir),
    )


def _orb_trial_output_dir(
    observed_at: dt.datetime,
    config: OrbTrialConfig,
    operation: str,
) -> Path:
    session_date = observed_at.astimezone(ZoneInfo("America/New_York")).date()
    return config.lane_forward.output_dir / "trials" / session_date.isoformat() / operation


def _orb_trial_register_command(
    observed_at: dt.datetime,
    config: OrbTrialConfig,
) -> tuple[str, ...]:
    session_date = observed_at.astimezone(ZoneInfo("America/New_York")).date()
    return (
        str(Path(__file__).with_name("run_orb_forward_trial.py")),
        "register",
        "--experiment-ledger",
        str(config.experiment_ledger),
        "--lane-registry",
        str(config.lane_forward.lane_registry),
        "--session-date",
        session_date.isoformat(),
        "--output-dir",
        str(_orb_trial_output_dir(observed_at, config, "register")),
    )


def _orb_trial_start_command(
    observed_at: dt.datetime,
    config: OrbTrialConfig,
) -> tuple[str, ...]:
    session_date = observed_at.astimezone(ZoneInfo("America/New_York")).date()
    return (
        str(Path(__file__).with_name("run_orb_forward_trial.py")),
        "start",
        "--experiment-ledger",
        str(config.experiment_ledger),
        "--session-date",
        session_date.isoformat(),
        "--output-dir",
        str(_orb_trial_output_dir(observed_at, config, "start")),
    )


def _orb_trial_finalize_command(
    output: Path,
    observed_at: dt.datetime,
    config: OrbTrialConfig,
) -> tuple[str, ...]:
    session_date = observed_at.astimezone(ZoneInfo("America/New_York")).date()
    return (
        str(Path(__file__).with_name("run_orb_forward_trial.py")),
        "finalize",
        str(output),
        "--experiment-ledger",
        str(config.experiment_ledger),
        "--lane-registry",
        str(config.lane_forward.lane_registry),
        "--review-ledger",
        str(config.lane_forward.review_ledger),
        "--session-date",
        session_date.isoformat(),
        "--output-dir",
        str(_orb_trial_output_dir(observed_at, config, "finalize")),
    )


def _orb_trial_fail_command(
    observed_at: dt.datetime,
    config: OrbTrialConfig,
    phase: OrbTrialFailurePhase,
    audit: Path,
) -> tuple[str, ...]:
    session_date = observed_at.astimezone(ZoneInfo("America/New_York")).date()
    return (
        str(Path(__file__).with_name("run_orb_forward_trial.py")),
        "fail",
        "--experiment-ledger",
        str(config.experiment_ledger),
        "--session-date",
        session_date.isoformat(),
        "--phase",
        phase.value,
        "--audit",
        str(audit),
        "--output-dir",
        str(_orb_trial_output_dir(observed_at, config, "fail")),
    )


def _run_and_audit(command: tuple[str, ...], audit_path: Path) -> int:
    started_at = dt.datetime.now().astimezone()
    completed = subprocess.run(command, check=False)
    append_cycle_audit(audit_path, started_at, completed.returncode)
    return completed.returncode


def run_session_metrics(
    output: Path,
    observed_at: dt.datetime,
    runner: Callable[[tuple[str, ...], Path], int] = _run_and_audit,
    strategy: StrategyMode = StrategyMode.ORB,
    lane_forward_validation: LaneForwardValidationConfig | None = None,
    orb_trial: OrbTrialConfig | None = None,
) -> int | None:
    if lane_forward_validation is not None and strategy is not StrategyMode.ORB:
        raise ValueError("lane forward validation requires ORB strategy")
    if orb_trial is not None and (
        strategy is not StrategyMode.ORB or orb_trial.lane_forward != lane_forward_validation
    ):
        raise ValueError("ORB trial requires its exact ORB lane forward configuration")
    database = output / "paper_recommendations.sqlite3"
    if regular_session_is_open(observed_at) or not database.is_file():
        return None
    terminal_audit = output / "post_session_orb_trial_terminal_cycles.csv"

    def record_trial_failure(phase: OrbTrialFailurePhase, audit: Path) -> None:
        if orb_trial is not None:
            _ = runner(
                _orb_trial_fail_command(observed_at, orb_trial, phase, audit),
                terminal_audit,
            )

    metrics_audit = output / "post_session_metrics_cycles.csv"
    metrics_exit_code = runner(
        _paper_metrics_command(output),
        metrics_audit,
    )
    if metrics_exit_code:
        record_trial_failure(OrbTrialFailurePhase.PAPER_METRICS, metrics_audit)
        return metrics_exit_code
    research_audit = output / "post_session_research_cycles.csv"
    research_exit_code = runner(
        _daily_research_command(output, observed_at, strategy),
        research_audit,
    )
    if research_exit_code:
        record_trial_failure(OrbTrialFailurePhase.DAILY_RESEARCH_RECORD, research_audit)
        return research_exit_code
    adaptive_audit = output / "post_session_adaptive_evaluation_cycles.csv"
    adaptive_exit_code = runner(
        _adaptive_evaluation_command(output),
        adaptive_audit,
    )
    if adaptive_exit_code:
        record_trial_failure(OrbTrialFailurePhase.ADAPTIVE_EVALUATION, adaptive_audit)
        return adaptive_exit_code
    if lane_forward_validation is None:
        return 0
    lane_audit = output / "post_session_lane_forward_validation_cycles.csv"
    lane_exit_code = runner(
        _lane_forward_validation_command(
            output,
            observed_at,
            lane_forward_validation,
        ),
        lane_audit,
    )
    if lane_exit_code:
        record_trial_failure(OrbTrialFailurePhase.LANE_FORWARD_VALIDATION, lane_audit)
        return lane_exit_code
    if orb_trial is None:
        return 0
    return runner(
        _orb_trial_finalize_command(output, observed_at, orb_trial),
        terminal_audit,
    )


def main(
    output_dir: str | None = None,
    cycles: int = 390,
    interval_seconds: float = 60.0,
    wait_until_open: bool = False,
    max_wait_minutes: int = 720,
    strategy: StrategyMode = StrategyMode.ORB,
    top: int = 10,
    max_pages: int = 1,
    collect_premarket: bool = False,
    premarket_interval_seconds: float = 300.0,
    lane_execution_database: Path | None = None,
    lane_registry: Path | None = None,
    lane_review_ledger: Path | None = None,
    lane_forward_output_dir: Path | None = None,
    experiment_ledger: Path | None = None,
) -> None:
    if not 1 <= cycles <= 390:
        raise typer.BadParameter("cycles는 1~390이어야 합니다")
    if not 1.0 <= interval_seconds <= 3600.0:
        raise typer.BadParameter("interval-seconds는 1~3600이어야 합니다")
    if not 1 <= max_wait_minutes <= 1440:
        raise typer.BadParameter("max-wait-minutes는 1~1440이어야 합니다")
    if not 1 <= top <= 10:
        raise typer.BadParameter("top은 1~10이어야 합니다")
    if not 1 <= max_pages <= 10:
        raise typer.BadParameter("max-pages는 1~10이어야 합니다")
    if not 60.0 <= premarket_interval_seconds <= 3600.0:
        raise typer.BadParameter("premarket-interval-seconds는 60~3600이어야 합니다")
    lane_forward_validation = _lane_forward_validation_config(
        strategy,
        lane_execution_database,
        lane_registry,
        lane_review_ledger,
        lane_forward_output_dir,
    )
    orb_trial = _orb_trial_config(
        strategy,
        experiment_ledger,
        lane_forward_validation,
    )
    checked_at = dt.datetime.now(ZoneInfo("America/New_York"))
    output = (
        Path(output_dir) if output_dir is not None else Path("outputs/live_sessions") / checked_at.strftime("%Y%m%d")
    )
    premarket_exit_codes: tuple[int, ...] = ()
    if not regular_session_is_open(checked_at) and not wait_until_open and not collect_premarket:
        rprint("[yellow]미국 정규장 밖이므로 감시를 시작하지 않습니다.[/yellow]")
        return
    if orb_trial is not None:
        registration_exit_code = _run_and_audit(
            _orb_trial_register_command(checked_at, orb_trial),
            output / "pre_session_orb_trial_registration_cycles.csv",
        )
        if registration_exit_code:
            raise typer.Exit(code=1)
    if not regular_session_is_open(checked_at):
        if collect_premarket:
            rprint("[yellow]미국 장전 랭킹 수집과 정규장 개장을 기다립니다.[/yellow]")
            premarket_result = collect_premarket_until_regular_open(
                lambda: dt.datetime.now(ZoneInfo("America/New_York")),
                time.sleep,
                lambda: _run_and_audit(
                    _premarket_scan_command(output, top),
                    output / "premarket_watch_cycles.csv",
                ),
                PremarketWaitConfig(
                    max_wait=dt.timedelta(minutes=max_wait_minutes),
                    closed_poll_seconds=30.0,
                    collection_interval_seconds=premarket_interval_seconds,
                ),
            )
            opened_at = premarket_result.opened_at
            premarket_exit_codes = premarket_result.exit_codes
        else:
            rprint("[yellow]미국 정규장 개장을 기다립니다.[/yellow]")
            opened_at = wait_for_session_open(
                lambda: dt.datetime.now(ZoneInfo("America/New_York")),
                time.sleep,
                SessionWaitConfig(
                    max_wait=dt.timedelta(minutes=max_wait_minutes),
                    poll_seconds=30.0,
                ),
            )
        if opened_at is None:
            rprint("[red]대기 제한 안에 미국 정규장이 열리지 않았습니다.[/red]")
            raise typer.Exit(code=2)
        checked_at = opened_at

    if orb_trial is not None:
        start_exit_code = _run_and_audit(
            _orb_trial_start_command(checked_at, orb_trial),
            output / "regular_session_orb_trial_start_cycles.csv",
        )
        if start_exit_code:
            raise typer.Exit(code=1)

    def scan_once() -> int:
        return _run_and_audit(
            _scan_command(output, strategy, top, max_pages),
            output / "watch_cycles.csv",
        )

    exit_codes = run_cycles(
        scan_once,
        WatchConfig(cycles, interval_seconds),
        CycleRuntime(
            time.sleep,
            lambda: regular_session_is_open(dt.datetime.now(ZoneInfo("America/New_York"))),
        ),
    )
    session_date = checked_at.astimezone(ZoneInfo("America/New_York")).date()
    eod_ready_at = wait_for_eod_ready(
        lambda: dt.datetime.now(ZoneInfo("America/New_York")),
        time.sleep,
        session_date,
        EodWaitConfig(
            max_wait=dt.timedelta(minutes=3),
            poll_seconds=15.0,
            settlement_delay=dt.timedelta(seconds=65),
        ),
    )
    eod_exit_code = None
    if eod_ready_at is not None:
        eod_exit_code = _run_and_audit(
            eod_catchup_command(Path(__file__).parent, output, strategy, max_pages),
            output / "eod_catchup_cycles.csv",
        )
    ended_at = dt.datetime.now(ZoneInfo("America/New_York"))
    finalized = finalize_session_output(output, ended_at)
    metrics_exit_code = run_session_metrics(
        output,
        ended_at,
        strategy=strategy,
        lane_forward_validation=lane_forward_validation,
        orb_trial=orb_trial,
    )
    failures = sum(code != 0 for code in (*premarket_exit_codes, *exit_codes))
    failures += int(eod_exit_code not in (None, 0))
    failures += int(metrics_exit_code not in (None, 0))
    metrics_status = "skipped" if metrics_exit_code is None else str(metrics_exit_code)
    eod_status = "skipped" if eod_exit_code is None else str(eod_exit_code)
    rprint(
        f"[green]감시 종료[/green] premarket_cycles={len(premarket_exit_codes)}, "
        + f"regular_cycles={len(exit_codes)}, "
        + f"failures={failures}, time_exits={finalized}, "
        + f"eod_exit={eod_status}, metrics_exit={metrics_status}, {output}"
    )
    if failures:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
