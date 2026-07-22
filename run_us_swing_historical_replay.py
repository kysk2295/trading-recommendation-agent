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
from collections.abc import Sequence
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.private_report import write_private_report
from trading_agent.research_hypothesis_registration import (
    register_research_hypothesis_manifest,
)
from trading_agent.swing_shadow_review_store import SwingShadowReviewStore
from trading_agent.swing_shadow_store import SwingShadowStore
from trading_agent.us_swing_historical_replay import (
    HistoricalSwingFixtureScanner,
    SwingHistoricalReplayFixture,
    SwingHistoricalReplayRequest,
    SwingHistoricalReplayResult,
    run_swing_historical_replay,
)
from trading_agent.us_swing_operating_models import SwingOperatingConfig

ROOT = Path(__file__).resolve().parent
REPORT_NAME = "us_swing_historical_replay_ko.md"


class UsSwingHistoricalReplayCliError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing historical replay를 안전하게 실행할 수 없습니다"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US swing fixture를 시점 순서대로 replay하고 shadow Reviewer 증거까지 생성"
    )
    parser.add_argument(
        "--fixture",
        action="append",
        required=True,
        metavar="YYYY-MM-DD=PATH",
    )
    parser.add_argument(
        "--research-manifest",
        type=Path,
        default=ROOT / "examples" / "research" / "us-swing-new-high-rvol-v1.json",
    )
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--shadow-ledger", type=Path, required=True)
    parser.add_argument("--delivery-store", type=Path, required=True)
    parser.add_argument("--review-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_code_version: str | None = None,
) -> int:
    args = parse_args(argv)
    try:
        fixtures = _fixtures(args.fixture)
        code_version = (
            _current_code_version()
            if runtime_code_version is None
            else runtime_code_version
        )
        experiment = ExperimentLedgerStore(args.experiment_ledger)
        _ = register_research_hypothesis_manifest(args.research_manifest, experiment)
        shadow = SwingShadowStore(args.shadow_ledger)
        delivery = HermesDeliveryStore(args.delivery_store)
        reviews = SwingShadowReviewStore(args.review_ledger)
        result = run_swing_historical_replay(
            SwingHistoricalReplayRequest(fixtures, code_version),
            SwingOperatingConfig(
                experiment_ledger=experiment,
                shadow_ledger=shadow,
                delivery_store=delivery,
                review_store=reviews,
                scanner=HistoricalSwingFixtureScanner(fixtures, shadow, delivery),
            ),
        )
    except (
        OSError,
        RuntimeError,
        sqlite3.Error,
        subprocess.SubprocessError,
        UsSwingHistoricalReplayCliError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, None)
        return 1
    _write_report(args.output_dir, result)
    return 0


def _fixtures(values: Sequence[str]) -> tuple[SwingHistoricalReplayFixture, ...]:
    fixtures: list[SwingHistoricalReplayFixture] = []
    for value in values:
        date_text, separator, path_text = value.partition("=")
        try:
            session_date = dt.date.fromisoformat(date_text)
        except ValueError:
            raise UsSwingHistoricalReplayCliError from None
        if (
            separator != "="
            or session_date.isoformat() != date_text
            or not path_text
        ):
            raise UsSwingHistoricalReplayCliError
        fixtures.append(
            SwingHistoricalReplayFixture(
                session_date,
                Path(path_text).expanduser().resolve(strict=False),
            )
        )
    return tuple(fixtures)


def _current_code_version() -> str:
    revision = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise UsSwingHistoricalReplayCliError
    return revision


def _write_report(
    output_dir: Path,
    result: SwingHistoricalReplayResult | None,
) -> None:
    details = (
        ("result: blocked", "external broker mutations: 0")
        if result is None
        else (
            "result: completed",
            f"sessions replayed: {result.sessions_replayed}",
            f"causal snapshots: {result.causal_snapshots}",
            f"recommendation cards: {result.recommendation_cards}",
            f"no-recommendation cards: {result.no_recommendation_cards}",
            f"shadow entries: {result.shadow_entries}",
            f"shadow terminals: {result.shadow_terminals}",
            f"reviewer evidence: {result.reviewer_evidence}",
            "external broker mutations: 0",
        )
    )
    content = "\n".join(
        (
            "# US Swing historical replay",
            "",
            "> 날짜별 완료 일봉을 해당 장후 시점에만 열어 shadow 증거를 재생합니다.",
            "",
            *(f"- {detail}" for detail in details),
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
