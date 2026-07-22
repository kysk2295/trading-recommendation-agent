#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_theme_day_post_session_audit import (
    kr_theme_day_post_session_phase_status,
    run_audited_kr_theme_day_post_session_phase,
)
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_shadow_exit_store import KrThemeDayShadowExitStore
from trading_agent.kr_theme_day_terminal_delivery import (
    InvalidKrThemeDayTerminalDeliveryError,
    KrThemeDayTerminalDeliverySources,
    project_kr_theme_day_terminal_delivery,
)
from trading_agent.kr_theme_day_trial_terminal_store import KrThemeDayTrialTerminalStore
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_day_post_session_ko.md"
TERMINAL_AUDIT_NAME = "kr_theme_day_terminal_cycles.csv"
DELIVERY_AUDIT_NAME = "kr_theme_day_terminal_delivery_cycles.csv"
REVIEWER_AUDIT_NAME = "kr_theme_day_reviewer_cycles.csv"
LIFECYCLE_AUDIT_NAME = "kr_theme_day_lifecycle_cycles.csv"

CommandRunner = Callable[[tuple[str, ...]], int]
Clock = Callable[[], dt.datetime]


@dataclass(frozen=True, slots=True)
class KrThemeDayPostSessionPaths:
    experiment_ledger: Path
    entry_store: Path
    exit_store: Path
    terminal_store: Path
    delivery_store: Path
    review_store: Path
    calendar_store: Path
    output_dir: Path


@dataclass(frozen=True, slots=True)
class KrThemeDayPostSessionRequest:
    paths: KrThemeDayPostSessionPaths
    trial_id: str
    strategy_version: str
    session_date: dt.date


@dataclass(frozen=True, slots=True)
class KrThemeDayPostSessionResult:
    terminal_exit_code: int
    delivery_exit_code: int | None
    reviewer_exit_code: int | None
    lifecycle_exit_code: int | None

    @property
    def completed(self) -> bool:
        return (
            self.terminal_exit_code == 0
            and self.delivery_exit_code == 0
            and self.reviewer_exit_code == 0
            and self.lifecycle_exit_code == 0
        )


DeliveryRunner = Callable[[KrThemeDayPostSessionRequest], int]


def terminal_command(request: KrThemeDayPostSessionRequest) -> tuple[str, ...]:
    return (
        str(Path(__file__).with_name("run_kr_theme_day_trial_terminal.py")),
        *_evidence_arguments(request.paths),
        "--trial-id",
        request.trial_id,
        "--output-dir",
        str(request.paths.output_dir / "terminal" / request.session_date.isoformat()),
    )


def reviewer_command(request: KrThemeDayPostSessionRequest) -> tuple[str, ...]:
    return (
        str(Path(__file__).with_name("run_kr_theme_day_reviewer.py")),
        *_evidence_arguments(request.paths),
        "--review-store",
        str(request.paths.review_store),
        "--strategy-version",
        request.strategy_version,
        "--as-of-session",
        request.session_date.isoformat(),
        "--output-dir",
        str(request.paths.output_dir / "reviewer" / request.session_date.isoformat()),
    )


def lifecycle_command(request: KrThemeDayPostSessionRequest) -> tuple[str, ...]:
    return (
        str(Path(__file__).with_name("run_kr_theme_day_lifecycle.py")),
        *_evidence_arguments(request.paths),
        "--review-store",
        str(request.paths.review_store),
        "--calendar-store",
        str(request.paths.calendar_store),
        "--strategy-version",
        request.strategy_version,
        "--as-of-session",
        request.session_date.isoformat(),
        "--output-dir",
        str(request.paths.output_dir / "lifecycle" / request.session_date.isoformat()),
    )


def _deliver_terminal(request: KrThemeDayPostSessionRequest) -> int:
    paths = request.paths
    sources = KrThemeDayTerminalDeliverySources(
        entry_store=KrThemeDayShadowEntryStore(paths.entry_store),
        exit_store=KrThemeDayShadowExitStore(paths.exit_store),
        terminal_store=KrThemeDayTrialTerminalStore(paths.terminal_store),
        delivery_store=HermesDeliveryStore(paths.delivery_store),
    )
    try:
        _ = project_kr_theme_day_terminal_delivery(sources, request.trial_id)
    except InvalidKrThemeDayTerminalDeliveryError:
        return 1
    return 0


def run_post_session(
    request: KrThemeDayPostSessionRequest,
    *,
    runner: CommandRunner = lambda command: subprocess.run(command, check=False).returncode,
    delivery_runner: DeliveryRunner = _deliver_terminal,
    clock: Clock = lambda: dt.datetime.now().astimezone(),
) -> KrThemeDayPostSessionResult:
    paths = request.paths
    terminal = _run_phase(
        terminal_command(request),
        paths.output_dir / TERMINAL_AUDIT_NAME,
        runner,
        clock,
    )
    if terminal != 0:
        return KrThemeDayPostSessionResult(terminal, None, None, None)
    delivery = run_audited_kr_theme_day_post_session_phase(
        lambda: delivery_runner(request),
        paths.output_dir / DELIVERY_AUDIT_NAME,
        clock,
    )
    if delivery != 0:
        return KrThemeDayPostSessionResult(terminal, delivery, None, None)
    reviewer = _run_phase(
        reviewer_command(request),
        paths.output_dir / REVIEWER_AUDIT_NAME,
        runner,
        clock,
    )
    if reviewer != 0:
        return KrThemeDayPostSessionResult(terminal, delivery, reviewer, None)
    lifecycle = _run_phase(
        lifecycle_command(request),
        paths.output_dir / LIFECYCLE_AUDIT_NAME,
        runner,
        clock,
    )
    return KrThemeDayPostSessionResult(terminal, delivery, reviewer, lifecycle)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR theme day terminal, Reviewer와 lifecycle을 장후 직렬 실행")
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--entry-store", type=Path, required=True)
    parser.add_argument("--exit-store", type=Path, required=True)
    parser.add_argument("--terminal-store", type=Path, required=True)
    parser.add_argument("--delivery-store", type=Path, required=True)
    parser.add_argument("--review-store", type=Path, required=True)
    parser.add_argument("--calendar-store", type=Path, required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument("--session-date", type=_session_date, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: CommandRunner = lambda command: subprocess.run(command, check=False).returncode,
    delivery_runner: DeliveryRunner = _deliver_terminal,
    clock: Clock = lambda: dt.datetime.now().astimezone(),
) -> int:
    args = parse_args(argv)
    request = KrThemeDayPostSessionRequest(
        paths=KrThemeDayPostSessionPaths(
            experiment_ledger=args.experiment_ledger,
            entry_store=args.entry_store,
            exit_store=args.exit_store,
            terminal_store=args.terminal_store,
            delivery_store=args.delivery_store,
            review_store=args.review_store,
            calendar_store=args.calendar_store,
            output_dir=args.output_dir,
        ),
        trial_id=args.trial_id,
        strategy_version=args.strategy_version,
        session_date=args.session_date,
    )
    result = run_post_session(
        request,
        runner=runner,
        delivery_runner=delivery_runner,
        clock=clock,
    )
    if not _write_report(request, result):
        return 2
    return 0 if result.completed else 1


def _evidence_arguments(paths: KrThemeDayPostSessionPaths) -> tuple[str, ...]:
    return (
        "--experiment-ledger",
        str(paths.experiment_ledger),
        "--entry-store",
        str(paths.entry_store),
        "--exit-store",
        str(paths.exit_store),
        "--terminal-store",
        str(paths.terminal_store),
    )


def _run_phase(
    command: tuple[str, ...],
    audit_path: Path,
    runner: CommandRunner,
    clock: Clock,
) -> int:
    return run_audited_kr_theme_day_post_session_phase(
        lambda: runner(command),
        audit_path,
        clock,
    )


def _write_report(
    request: KrThemeDayPostSessionRequest,
    result: KrThemeDayPostSessionResult,
) -> bool:
    lines = (
        "# KR Theme Day Post-session Control Cycle",
        "",
        "> completed는 전략 성과가 아니라 세 local control 단계의 실행 완료를 뜻합니다.",
        "",
        f"- result: {'completed_control_cycle' if result.completed else 'blocked'}",
        f"- session_date: {request.session_date.isoformat()}",
        f"- terminal phase: {kr_theme_day_post_session_phase_status(result.terminal_exit_code)}",
        f"- delivery phase: {kr_theme_day_post_session_phase_status(result.delivery_exit_code)}",
        f"- Reviewer phase: {kr_theme_day_post_session_phase_status(result.reviewer_exit_code)}",
        f"- lifecycle phase: {kr_theme_day_post_session_phase_status(result.lifecycle_exit_code)}",
        "- automatic champion: false",
        "- order authority change: false",
        "- allocation change: false",
        "- external account/order mutation: 0",
        "",
    )
    try:
        write_private_report(request.paths.output_dir / REPORT_NAME, "\n".join(lines))
    except OSError:
        return False
    return True


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("session date는 YYYY-MM-DD 형식이어야 합니다") from error


if __name__ == "__main__":
    raise SystemExit(main())
