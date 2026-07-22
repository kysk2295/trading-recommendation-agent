#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.alpaca_http import DEFAULT_ALPACA_SECRET_PATH
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.private_report import write_private_report
from trading_agent.research_hypothesis_registration import register_research_hypothesis_manifest
from trading_agent.swing_shadow_review_store import SwingShadowReviewStore
from trading_agent.swing_shadow_source import load_swing_daily_source
from trading_agent.swing_shadow_store import SwingShadowReader
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_swing_operating_coordinator import (
    SwingOperatingConfig,
    SwingOperatingRequest,
    SwingOperatingResult,
    run_us_swing_operating_tick,
)
from trading_agent.us_swing_operating_models import (
    SwingScanCompleted,
    SwingScanFailed,
    SwingScanFailureReason,
    SwingScanOutcome,
)

ROOT = Path(__file__).resolve().parent
REPORT_NAME = "us_swing_operating_session_ko.md"


class UsSwingOperatingCliError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing operating session을 안전하게 실행할 수 없습니다"


@dataclass(frozen=True, slots=True)
class SwingScannerCommand:
    shadow_ledger: Path
    delivery_store: Path
    output_dir: Path
    universe_file: Path | None
    auto_universe: bool
    fixture_root: Path | None
    secret_path: Path


@dataclass(frozen=True, slots=True)
class SubprocessSwingDailyScanner:
    command: SwingScannerCommand
    clock: Callable[[], dt.datetime]

    def run(self, session_date: dt.date) -> SwingScanOutcome:
        arguments = [
            sys.executable,
            str(ROOT / "run_us_swing_shadow.py"),
            "--session-date",
            session_date.isoformat(),
            "--database",
            str(self.command.shadow_ledger),
            "--delivery-database",
            str(self.command.delivery_store),
            "--output-dir",
            str(self.command.output_dir),
        ]
        if self.command.fixture_root is not None:
            arguments.extend(("--fixture-root", str(self.command.fixture_root)))
        elif self.command.auto_universe:
            arguments.append("--auto-universe")
        elif self.command.universe_file is not None:
            arguments.extend(
                (
                    "--universe-file",
                    str(self.command.universe_file),
                    "--secret-path",
                    str(self.command.secret_path),
                )
            )
        else:
            raise UsSwingOperatingCliError
        try:
            _ = subprocess.run(arguments, cwd=ROOT, check=True, capture_output=True, text=True)
            if self.command.fixture_root is not None:
                source = load_swing_daily_source(self.command.fixture_root, session_date=session_date)
                return SwingScanCompleted(source.observed_at + dt.timedelta(microseconds=1))
        except (OSError, subprocess.SubprocessError, ValueError):
            return SwingScanFailed(self.clock(), SwingScanFailureReason.SOURCE_UNAVAILABLE)
        return SwingScanCompleted(self.clock())


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US swing completed-day scan과 shadow trial 수명주기를 한 tick으로 운영"
    )
    parser.add_argument("--session-date", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--universe-file", type=Path)
    source.add_argument("--fixture-root", type=Path)
    source.add_argument("--auto-universe", action="store_true")
    parser.add_argument(
        "--research-manifest",
        type=Path,
        default=ROOT / "examples" / "research" / "us-swing-new-high-rvol-v1.json",
    )
    parser.add_argument(
        "--experiment-ledger",
        type=Path,
        default=Path("outputs/experiment_control/experiment_ledger.sqlite3"),
    )
    parser.add_argument(
        "--shadow-ledger",
        type=Path,
        default=Path("outputs/us_swing_shadow/swing-shadow.sqlite3"),
    )
    parser.add_argument(
        "--delivery-store",
        type=Path,
        default=Path("outputs/hermes/delivery.sqlite3"),
    )
    parser.add_argument(
        "--review-ledger",
        type=Path,
        default=Path("outputs/us_swing_shadow/reviews.sqlite3"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/us_swing_shadow/operating/latest"),
    )
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    now: dt.datetime | None = None,
    runtime_code_version: str | None = None,
) -> int:
    args = parse_args(argv)
    timestamp = dt.datetime.now(NEW_YORK) if now is None else now
    try:
        _ = _session_date(args.session_date, timestamp)
        code_version = _current_code_version() if runtime_code_version is None else runtime_code_version
        experiment = ExperimentLedgerStore(args.experiment_ledger)
        _ = register_research_hypothesis_manifest(args.research_manifest, experiment)
        scanner = SubprocessSwingDailyScanner(
            SwingScannerCommand(
                shadow_ledger=args.shadow_ledger,
                delivery_store=args.delivery_store,
                output_dir=args.output_dir / "scanner",
                universe_file=args.universe_file,
                auto_universe=args.auto_universe,
                fixture_root=args.fixture_root,
                secret_path=args.secret_path,
            ),
            clock=lambda: timestamp,
        )
        result = run_us_swing_operating_tick(
            SwingOperatingRequest(timestamp, code_version),
            SwingOperatingConfig(
                experiment_ledger=experiment,
                shadow_ledger=SwingShadowReader(args.shadow_ledger),
                delivery_store=HermesDeliveryStore(args.delivery_store),
                review_store=SwingShadowReviewStore(args.review_ledger),
                scanner=scanner,
            ),
        )
    except (
        OSError,
        RuntimeError,
        sqlite3.Error,
        subprocess.SubprocessError,
        UsSwingOperatingCliError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, None)
        return 1
    _write_report(args.output_dir, result)
    return 0


def _session_date(value: str, now: dt.datetime) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise UsSwingOperatingCliError from None
    if parsed.isoformat() != value or parsed != now.astimezone(NEW_YORK).date():
        raise UsSwingOperatingCliError
    return parsed


def _current_code_version() -> str:
    revision = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ("git", "status", "--porcelain"), cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    if dirty:
        raise UsSwingOperatingCliError
    return revision


def _write_report(output_dir: Path, result: SwingOperatingResult | None) -> None:
    details = (
        ("result: blocked", "external broker mutations: 0")
        if result is None
        else (
            "result: completed",
            f"phase: {result.phase.value}",
            f"scanner_executed: {str(result.scanner_executed).lower()}",
            f"registered: {result.registered}",
            f"started: {result.started}",
            f"finalized: {result.finalized}",
            f"delivered: {result.delivered}",
            f"incidents: {result.incidents}",
            f"reviewed: {result.reviewed}",
            f"blocked_signals: {len(result.blocked_signal_ids)}",
            "external broker mutations: 0",
        )
    )
    content = "\n".join(
        (
            "# US Swing operating session",
            "",
            "> 완료 일봉 기반 shadow 연구이며 broker 주문 권한이 없습니다.",
            "",
            *(f"- {detail}" for detail in details),
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
