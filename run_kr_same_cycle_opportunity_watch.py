#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "rich>=14.0", "typer>=0.16", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import re
import stat
import time
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import run_kr_same_cycle_opportunity
from trading_agent.private_report import open_private_append, write_private_report

REPORT_NAME = "kr_same_cycle_opportunity_watch_ko.md"
KST = ZoneInfo("Asia/Seoul")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CYCLE_RESULTS = frozenset(("blocked", "no_opportunity", "ready"))
Clock = Callable[[], dt.datetime]
Sleeper = Callable[[float], None]
CycleRunner = Callable[[Sequence[str], Clock], int]


@dataclass(frozen=True)
class _Attempt:
    cycle_id: str
    result: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KR strict same-cycle Opportunity를 bounded 간격으로 재수집",
    )
    parser.add_argument("--cycle-id-prefix", required=True)
    parser.add_argument("--collection-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--deadline", type=_aware_datetime, required=True)
    parser.add_argument("--poll-interval-seconds", type=_positive_int, default=300)
    parser.add_argument("--max-attempts", type=_bounded_attempts, default=75)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--delivery-database", type=Path, required=True)
    parser.add_argument("--collection-output-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--projection-output-dir", type=Path, required=True)
    parser.add_argument("--operator-output-root", type=Path, required=True)
    parser.add_argument("--watch-output-dir", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
    sleeper: Sleeper = time.sleep,
    cycle_runner: CycleRunner | None = None,
) -> int:
    args = parse_args(argv)
    deadline = args.deadline.astimezone(KST)
    if (
        deadline.date() != args.collection_date
        or _SAFE_ID.fullmatch(f"{args.cycle_id_prefix}-{args.max_attempts:03d}") is None
    ):
        raise ValueError("invalid KR Opportunity watch boundary")
    _validate_targets(args)
    run_cycle = _run_cycle if cycle_runner is None else cycle_runner
    attempts: list[_Attempt] = []
    selected_cycle_id: str | None = None
    terminal_result = "exhausted"
    for attempt_number in range(1, args.max_attempts + 1):
        observed_at = _aware_clock(clock).astimezone(KST)
        if args.fixture_root is None and observed_at.date() != args.collection_date:
            terminal_result = "wrong_session_date"
            break
        if args.fixture_root is None and observed_at > deadline:
            terminal_result = "deadline_reached"
            break
        cycle_id = f"{args.cycle_id_prefix}-{attempt_number:03d}"
        operator_output = args.operator_output_root / cycle_id
        cycle_argv = _cycle_argv(
            args,
            cycle_id=cycle_id,
            collection_output=args.collection_output_root / cycle_id,
            operator_output=operator_output,
        )
        exit_code = run_cycle(cycle_argv, clock)
        result, opportunity_count = _read_cycle_report(operator_output)
        if exit_code not in (0, 1) or (exit_code == 0) != (result != "blocked"):
            raise ValueError("inconsistent KR Opportunity cycle result")
        attempts.append(_Attempt(cycle_id=cycle_id, result=result))
        if result == "ready":
            if opportunity_count != 1:
                raise ValueError("KR Opportunity watch requires exactly one candidate")
            selected_cycle_id = cycle_id
            terminal_result = "ready"
            break
        if opportunity_count != 0:
            raise ValueError("blocked or empty cycle reported an Opportunity")
        if attempt_number == args.max_attempts:
            break
        remaining = (deadline - _aware_clock(clock).astimezone(KST)).total_seconds()
        if remaining <= 0:
            terminal_result = "deadline_reached"
            break
        sleeper(min(float(args.poll_interval_seconds), remaining))
    _write_report(
        args.watch_output_dir,
        result=terminal_result,
        attempts=tuple(attempts),
        selected_cycle_id=selected_cycle_id,
    )
    if selected_cycle_id is None:
        return 1
    print(selected_cycle_id)
    return 0


def _run_cycle(argv: Sequence[str], clock: Clock) -> int:
    args = run_kr_same_cycle_opportunity.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.chmod(0o700)
    with (
        open_private_append(args.output_dir / "cycle.stdout.log") as stdout,
        open_private_append(args.output_dir / "cycle.stderr.log") as stderr,
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        return run_kr_same_cycle_opportunity.main(argv, clock=clock)


def _cycle_argv(
    args: argparse.Namespace,
    *,
    cycle_id: str,
    collection_output: Path,
    operator_output: Path,
) -> tuple[str, ...]:
    values = [
        "--collection-cycle-id",
        cycle_id,
        "--collection-date",
        args.collection_date.isoformat(),
        "--policy",
        str(args.policy),
        "--database",
        str(args.database),
        "--experiment-ledger",
        str(args.experiment_ledger),
        "--delivery-database",
        str(args.delivery_database),
        "--collection-output-dir",
        str(collection_output),
        "--run-root",
        str(args.run_root),
        "--projection-output-dir",
        str(args.projection_output_dir),
        "--output-dir",
        str(operator_output),
    ]
    if args.fixture_root is not None:
        values.extend(("--fixture-root", str(args.fixture_root)))
    return tuple(values)


def _read_cycle_report(output_dir: Path) -> tuple[str, int]:
    report = output_dir / run_kr_same_cycle_opportunity.REPORT_NAME
    if (
        not report.is_file()
        or report.is_symlink()
        or stat.S_IMODE(report.stat().st_mode) != 0o600
    ):
        raise ValueError("invalid KR Opportunity cycle report")
    fields = {
        key.strip(): value.strip()
        for line in report.read_text(encoding="utf-8").splitlines()
        if line.startswith("- ") and ": " in line
        for key, value in (line[2:].split(": ", maxsplit=1),)
    }
    result = fields.get("result", "")
    if result not in _CYCLE_RESULTS:
        raise ValueError("invalid KR Opportunity cycle status")
    try:
        opportunity_count = int(fields["opportunity count"])
    except (KeyError, ValueError) as error:
        raise ValueError("invalid KR Opportunity count") from error
    return result, opportunity_count


def _write_report(
    output_dir: Path,
    *,
    result: str,
    attempts: tuple[_Attempt, ...],
    selected_cycle_id: str | None,
) -> None:
    attempt_lines = tuple(
        f"- attempt {index}: {attempt.cycle_id} | {attempt.result}"
        for index, attempt in enumerate(attempts, start=1)
    )
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR same-cycle Opportunity watch",
                "",
                "> 각 시도는 새 exact four-source terminal cycle이며 실패 기록을 보존합니다.",
                "",
                f"- result: {result}",
                f"- attempt count: {len(attempts)}",
                f"- selected cycle id: {selected_cycle_id or 'none'}",
                f"- attempts exhausted: {str(result == 'exhausted').lower()}",
                "- source quality gate weakened: false",
                "- failed cycle deletion: 0",
                "- order authority: false",
                "- domestic account endpoint: false",
                "- external account/order mutation: 0",
                "",
                *attempt_lines,
                "",
            )
        ),
    )


def _validate_targets(args: argparse.Namespace) -> None:
    database_targets = {
        item.expanduser().resolve(strict=False)
        for database in (
            args.database,
            args.experiment_ledger,
            args.delivery_database,
        )
        for item in (
            database,
            Path(f"{database}.writer.lock"),
            Path(f"{database}-journal"),
            Path(f"{database}-shm"),
            Path(f"{database}-wal"),
        )
    }
    roots = (
        args.collection_output_root,
        args.run_root,
        args.projection_output_dir,
        args.operator_output_root,
        args.watch_output_dir,
    )
    resolved_roots = tuple(root.expanduser().resolve(strict=False) for root in roots)
    if (
        len(set(resolved_roots)) != len(resolved_roots)
        or any(root in database_targets for root in resolved_roots)
        or any(root.is_symlink() for root in roots)
    ):
        raise ValueError("invalid KR Opportunity watch targets")


def _aware_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("deadline must include a timezone offset")
    return parsed


def _aware_clock(clock: Clock) -> dt.datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an aware datetime")
    return value


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0 or parsed > 3600:
        raise argparse.ArgumentTypeError("poll interval must be between 1 and 3600")
    return parsed


def _bounded_attempts(value: str) -> int:
    parsed = int(value)
    if parsed <= 0 or parsed > 999:
        raise argparse.ArgumentTypeError("max attempts must be between 1 and 999")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
