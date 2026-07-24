from __future__ import annotations

import argparse
import datetime as dt
import stat
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

import run_kr_same_cycle_opportunity
import run_kr_same_cycle_opportunity_watch
from tests.test_kr_same_cycle_opportunity_cli import (
    FIXTURES,
    _register,
    _write_policy,
)
from trading_agent.signal_contract_models import OpportunitySnapshot

ROOT = Path(__file__).parents[1]
KST = dt.timezone(dt.timedelta(hours=9))
COLLECTION_DATE = "2026-07-27"


def test_fixture_watch_runs_collection_projection_end_to_end(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register(tmp_path / "experiment.sqlite3", tmp_path)
    policy = _write_policy(tmp_path)

    result = run_kr_same_cycle_opportunity_watch.main(
        [
            "--cycle-id-prefix",
            "kr-watch-fixture",
            "--collection-date",
            "2026-07-16",
            "--deadline",
            "2026-07-16T15:20:00+09:00",
            "--max-attempts",
            "1",
            "--policy",
            str(policy),
            "--database",
            str(tmp_path / "source.sqlite3"),
            "--experiment-ledger",
            str(tmp_path / "experiment.sqlite3"),
            "--delivery-database",
            str(tmp_path / "delivery.sqlite3"),
            "--collection-output-root",
            str(tmp_path / "collections"),
            "--run-root",
            str(tmp_path / "runs"),
            "--projection-output-dir",
            str(tmp_path / "projection"),
            "--operator-output-root",
            str(tmp_path / "operator"),
            "--watch-output-dir",
            str(tmp_path / "watch"),
            "--fixture-root",
            str(FIXTURES),
        ],
        clock=lambda: dt.datetime(2026, 7, 16, 10, 2, 30, tzinfo=KST),
    )

    opportunities = tuple(
        OpportunitySnapshot.model_validate_json(line)
        for line in (tmp_path / "projection" / "opportunities.v1.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert result == 0
    assert len(opportunities) == 1
    assert capsys.readouterr().out == "kr-watch-fixture-001\n"
    assert "kr-watch-fixture-001" in (
        tmp_path / "watch" / "kr_same_cycle_opportunity_watch_ko.md"
    ).read_text(encoding="utf-8")
    for name in ("cycle.stdout.log", "cycle.stderr.log"):
        assert stat.S_IMODE((tmp_path / "operator" / "kr-watch-fixture-001" / name).stat().st_mode) == 0o600


def test_watch_retries_blocked_and_empty_cycles_until_one_unique_opportunity(
    tmp_path: Path,
) -> None:
    # Given: source readiness recovers, then a later complete cycle produces a candidate.
    clock = _MutableClock(dt.datetime(2026, 7, 27, 9, 5, tzinfo=KST))
    outcomes = iter(((1, "blocked", 0), (0, "no_opportunity", 0), (0, "ready", 1)))
    calls: list[tuple[str, Path, Path]] = []

    def cycle_runner(argv: Sequence[str], _clock: Callable[[], dt.datetime]) -> int:
        args = _cycle_args(argv)
        exit_code, result, count = next(outcomes)
        calls.append(
            (
                args.collection_cycle_id,
                args.collection_output_dir,
                args.output_dir,
            )
        )
        run_kr_same_cycle_opportunity._write_report(
            args.output_dir,
            result=result,
            opportunity_count=count,
        )
        return exit_code

    # When: the bounded watch runs.
    result = run_kr_same_cycle_opportunity_watch.main(
        _argv(tmp_path, max_attempts=5),
        clock=clock,
        sleeper=clock.advance,
        cycle_runner=cycle_runner,
    )

    # Then: every attempt is a new immutable source cycle and the watch stops at READY.
    assert result == 0
    assert [call[0] for call in calls] == [
        "kr-m3-live-20260727-001",
        "kr-m3-live-20260727-002",
        "kr-m3-live-20260727-003",
    ]
    assert len({call[1] for call in calls}) == 3
    assert len({call[2] for call in calls}) == 3
    assert clock.sleeps == [60.0, 60.0]
    report = tmp_path / "watch" / "kr_same_cycle_opportunity_watch_ko.md"
    report_text = report.read_text(encoding="utf-8")
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert "- result: ready" in report_text
    assert "- attempt count: 3" in report_text
    assert "- selected cycle id: kr-m3-live-20260727-003" in report_text
    assert "blocked" in report_text
    assert "no_opportunity" in report_text


def test_watch_exhausts_bounded_attempts_without_weakening_empty_cycle(
    tmp_path: Path,
) -> None:
    # Given: every exact four-source cycle completes without a qualifying candidate.
    clock = _MutableClock(dt.datetime(2026, 7, 27, 9, 5, tzinfo=KST))
    calls: list[str] = []

    def cycle_runner(argv: Sequence[str], _clock: Callable[[], dt.datetime]) -> int:
        args = _cycle_args(argv)
        calls.append(args.collection_cycle_id)
        run_kr_same_cycle_opportunity._write_report(
            args.output_dir,
            result="no_opportunity",
            opportunity_count=0,
        )
        return 0

    # When
    result = run_kr_same_cycle_opportunity_watch.main(
        _argv(tmp_path, max_attempts=2),
        clock=clock,
        sleeper=clock.advance,
        cycle_runner=cycle_runner,
    )

    # Then: the watch records a truthful bounded exhaustion and returns non-zero.
    assert result == 1
    assert calls == ["kr-m3-live-20260727-001", "kr-m3-live-20260727-002"]
    assert clock.sleeps == [60.0]
    report_text = (tmp_path / "watch" / "kr_same_cycle_opportunity_watch_ko.md").read_text(encoding="utf-8")
    assert "- result: exhausted" in report_text
    assert "- attempt count: 2" in report_text
    assert "- selected cycle id: none" in report_text


def test_watch_stops_at_deadline_without_starting_an_out_of_window_cycle(
    tmp_path: Path,
) -> None:
    # Given: the first failed attempt consumes the remaining operating window.
    clock = _MutableClock(dt.datetime(2026, 7, 27, 15, 19, 30, tzinfo=KST))
    calls: list[str] = []

    def cycle_runner(argv: Sequence[str], _clock: Callable[[], dt.datetime]) -> int:
        args = _cycle_args(argv)
        calls.append(args.collection_cycle_id)
        run_kr_same_cycle_opportunity._write_report(
            args.output_dir,
            result="blocked",
            opportunity_count=0,
        )
        clock.advance(40)
        return 1

    # When
    result = run_kr_same_cycle_opportunity_watch.main(
        _argv(tmp_path, max_attempts=5),
        clock=clock,
        sleeper=clock.advance,
        cycle_runner=cycle_runner,
    )

    # Then
    assert result == 1
    assert calls == ["kr-m3-live-20260727-001"]
    assert clock.sleeps == [40]
    report_text = (tmp_path / "watch" / "kr_same_cycle_opportunity_watch_ko.md").read_text(encoding="utf-8")
    assert "- result: deadline_reached" in report_text


def test_help_exposes_bounded_read_only_watch_surface() -> None:
    completed = subprocess.run(
        [str(ROOT / "run_kr_same_cycle_opportunity_watch.py"), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0
    for option in (
        "--cycle-id-prefix",
        "--collection-date",
        "--deadline",
        "--poll-interval-seconds",
        "--max-attempts",
        "--collection-output-root",
        "--watch-output-dir",
    ):
        assert option in output
    for forbidden in ("--account", "--order", "--broker", "--arm", "--url"):
        assert forbidden not in output


class _MutableClock:
    def __init__(self, value: dt.datetime) -> None:
        self.value = value
        self.sleeps: list[float] = []

    def __call__(self) -> dt.datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += dt.timedelta(seconds=seconds)


def _argv(tmp_path: Path, *, max_attempts: int) -> list[str]:
    return [
        "--cycle-id-prefix",
        "kr-m3-live-20260727",
        "--collection-date",
        COLLECTION_DATE,
        "--deadline",
        "2026-07-27T15:20:00+09:00",
        "--poll-interval-seconds",
        "60",
        "--max-attempts",
        str(max_attempts),
        "--policy",
        str(tmp_path / "policy.json"),
        "--database",
        str(tmp_path / "source.sqlite3"),
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--delivery-database",
        str(tmp_path / "delivery.sqlite3"),
        "--collection-output-root",
        str(tmp_path / "collections"),
        "--run-root",
        str(tmp_path / "runs"),
        "--projection-output-dir",
        str(tmp_path / "projection"),
        "--operator-output-root",
        str(tmp_path / "operator"),
        "--watch-output-dir",
        str(tmp_path / "watch"),
    ]


def _cycle_args(argv: Sequence[str]) -> argparse.Namespace:
    return run_kr_same_cycle_opportunity.parse_args(argv)
