#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.alpaca_bars import AlpacaBarsClient
from trading_agent.alpaca_http import (
    DEFAULT_ALPACA_SECRET_PATH,
    create_alpaca_client,
    load_alpaca_credentials,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.private_report import write_private_report
from trading_agent.systematic_regime_models import SystematicRecommendationCard
from trading_agent.systematic_regime_operating import (
    SystematicOperatingPhase,
    SystematicOperatingResult,
    run_systematic_regime_tick,
    systematic_operating_phase,
)
from trading_agent.systematic_regime_source import (
    collect_current_systematic_daily_source,
    load_systematic_daily_source,
    validate_current_systematic_collection,
)
from trading_agent.systematic_regime_store import SystematicRegimeStore
from trading_agent.us_equity_calendar import NEW_YORK

ROOT = Path(__file__).resolve().parent
CARD_NAME = "us_systematic_regime_card_ko.md"
REPORT_NAME = "us_systematic_regime_report_ko.md"


class UsSystematicRegimeCliError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime CLI is invalid"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="완료 일봉 기반 US systematic risk-on/risk-off shadow vertical"
    )
    parser.add_argument("--session-date", type=_date_argument, required=True)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("outputs/us_systematic_regime/systematic.sqlite3"),
    )
    parser.add_argument(
        "--experiment-ledger",
        type=Path,
        default=Path("outputs/experiment_control/experiment_ledger.sqlite3"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/us_systematic_regime/latest"),
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
    output = args.output_dir.expanduser().resolve(strict=False)
    try:
        _require_current_session(args.session_date, timestamp)
        phase = systematic_operating_phase(timestamp)
        source = _source(args, timestamp, phase)
        code_version = _current_code_version() if runtime_code_version is None else runtime_code_version
        store = SystematicRegimeStore(args.database.expanduser().resolve(strict=False))
        result = run_systematic_regime_tick(
            now=timestamp,
            code_version=code_version,
            experiment_ledger=ExperimentLedgerStore(
                args.experiment_ledger.expanduser().resolve(strict=False)
            ),
            store=store,
            source=source,
        )
        cards = store.cards()
        if cards:
            write_private_report(output / CARD_NAME, render_systematic_card(cards[-1]))
        _write_report(output, result)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValidationError, ValueError):
        _write_report(output, None)
        return 1
    return 0


def _source(args: argparse.Namespace, now: dt.datetime, phase: SystematicOperatingPhase):
    if phase is not SystematicOperatingPhase.POST_CLOSE:
        return None
    if args.fixture_root is not None:
        return load_systematic_daily_source(args.fixture_root, session_date=args.session_date)
    validate_current_systematic_collection(
        session_date=args.session_date,
        observed_at=now,
        now=now,
    )
    credentials = load_alpaca_credentials(args.secret_path)
    with create_alpaca_client() as client:
        return collect_current_systematic_daily_source(
            bars_client=AlpacaBarsClient(client, credentials, request_interval_seconds=1.0),
            session_date=args.session_date,
            observed_at=now,
            now=now,
        )


def render_systematic_card(card: SystematicRecommendationCard) -> str:
    candidates = ", ".join(card.candidate_symbols) if card.candidate_symbols else "없음"
    signal_lines = tuple(
        f"- {signal.symbol}: 조건부 진입 {signal.entry_price}, 손절 {signal.stop_price}, "
        f"목표 {signal.targets[0].price}"
        for signal in card.signals
    )
    return "\n".join(
        (
            "# US Systematic Regime 추천 카드",
            "",
            "> 완료 일봉 기반 다음 세션 shadow 연구이며 현재 진입 가능 또는 수익을 보장하지 않습니다.",
            "",
            f"- 카드 ID: {card.card_id}",
            f"- 관측 시각: {card.observed_at.isoformat()}",
            f"- 대상 세션: {card.target_session.isoformat()}",
            f"- 전략 버전: {card.strategy_version}",
            f"- 시장 regime: {card.context.regime.value}",
            f"- equity breadth: {card.context.equity_breadth_count}/3",
            f"- 결과: {card.decision_kind.value}",
            f"- 후보: {candidates}",
            "- market_regime 권한: signal-only",
            "- 주문 권한: 없음",
            "- 계좌·포지션 접근: 없음",
            "- Allocation Manager 연결: 없음",
            *signal_lines,
            "",
        )
    )


def _write_report(output: Path, result: SystematicOperatingResult | None) -> None:
    lines = (
        ("result: blocked", "account access: 0", "order mutations: 0", "http post: 0")
        if result is None
        else (
            "result: completed",
            f"phase: {result.phase.value}",
            f"cards_created: {result.cards_created}",
            f"trials_registered: {result.trials_registered}",
            f"trials_started: {result.trials_started}",
            f"trials_finalized: {result.trials_finalized}",
            "account access: 0",
            "order mutations: 0",
            "http post: 0",
        )
    )
    write_private_report(
        output / REPORT_NAME,
        "\n".join(("# US Systematic Regime 운영 결과", "", *(f"- {line}" for line in lines), "")),
    )


def _date_argument(value: str) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("session date must be YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("session date must be YYYY-MM-DD")
    return parsed


def _require_current_session(session_date: dt.date, now: dt.datetime) -> None:
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or session_date != now.astimezone(NEW_YORK).date()
    ):
        raise UsSystematicRegimeCliError


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
        raise UsSystematicRegimeCliError
    return revision


if __name__ == "__main__":
    raise SystemExit(main())
