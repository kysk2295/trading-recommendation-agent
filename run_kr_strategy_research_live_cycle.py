#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "rich>=14.0", "typer>=0.16", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

import run_kis_kr_market_collect
import run_kis_kr_session_calendar_collect
import run_kr_same_cycle_opportunity
from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_session_runtime_gate import (
    InvalidKrSessionRuntimeError,
    require_open_kr_runtime_session,
)
from trading_agent.kr_strategy_research_source import build_kr_strategy_research_sources
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.signal_contract_models import OpportunitySnapshot

KST = ZoneInfo("Asia/Seoul")
Clock = Callable[[], dt.datetime]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KRX read-only evidence to six-agent research source cycle")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--delivery-database", type=Path, required=True)
    parser.add_argument("--calendar-store", type=Path, required=True)
    parser.add_argument("--cycle-root", type=Path, required=True)
    parser.add_argument("--live-session-root", type=Path, required=True)
    parser.add_argument("--market-context-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    now = clock()
    local = now.astimezone(KST)
    calendar = _ensure_current_calendar(args.calendar_store, args.cycle_root, local.date(), clock)
    try:
        _ = require_open_kr_runtime_session(calendar, now)
    except InvalidKrSessionRuntimeError:
        print(f"status=session_closed session={local.date().isoformat()} mutation=0")
        return 0
    cycle_id = f"kr-research-{local.strftime('%Y%m%d-%H%M%S')}"
    root = args.cycle_root.expanduser().absolute() / cycle_id
    collection = root / "collection"
    runs = root / "runs"
    projection = root / "projection"
    report = root / "report"
    market_report = root / "market"
    for directory in (root, collection, runs, projection, report, market_report):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
    result = run_kr_same_cycle_opportunity.main(
        (
            "--collection-cycle-id",
            cycle_id,
            "--collection-date",
            local.date().isoformat(),
            "--policy",
            str(args.policy),
            "--database",
            str(args.database),
            "--experiment-ledger",
            str(args.experiment_ledger),
            "--delivery-database",
            str(args.delivery_database),
            "--collection-output-dir",
            str(collection),
            "--run-root",
            str(runs),
            "--projection-output-dir",
            str(projection),
            "--output-dir",
            str(report),
        ),
        clock=clock,
    )
    if result != 0:
        return result
    opportunities = _opportunities(projection / "opportunities.v1.jsonl")
    if not opportunities:
        print(f"status=no_opportunity cycle={cycle_id} mutation=0")
        return 0
    opportunity = opportunities[0]
    symbol = opportunity.candidates[0].symbol
    receipt_store = root / f"{symbol}.market.sqlite3"
    collected = run_kis_kr_market_collect.main(
        (
            "--symbol",
            symbol,
            "--calendar-store",
            str(args.calendar_store),
            "--calendar-snapshot-id",
            calendar.snapshot_id,
            "--receipt-store",
            str(receipt_store),
            "--output-dir",
            str(market_report),
        )
    )
    if collected != 0:
        return collected
    evaluated_at = clock()
    enriched, context = build_kr_strategy_research_sources(
        opportunity,
        KisKrMarketReceiptStore(receipt_store).receipts(),
        evaluated_at,
    )
    session = args.live_session_root.expanduser().absolute() / local.strftime("%Y%m%d")
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(session, 0o700)
    outbox = session / "opportunities.v1.jsonl"
    _prepare_outbox(outbox)
    _ = append_opportunity_snapshot(outbox, enriched)
    context_root = args.market_context_root.expanduser().absolute()
    context_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(context_root, 0o700)
    _ = publish_private_immutable_text(
        context_root / f"{context.observed_at.strftime('%Y%m%dT%H%M%S%fZ')}-{context.context_id}.market-context.json",
        context.model_dump_json() + "\n",
    )
    print(f"status=ready cycle={cycle_id} opportunity={enriched.opportunity_id} symbol={symbol} mutation=0")
    return 0


def _opportunities(path: Path) -> tuple[OpportunitySnapshot, ...]:
    if not path.exists():
        return ()
    return tuple(
        OpportunitySnapshot.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line
    )


def _ensure_current_calendar(
    path: Path,
    cycle_root: Path,
    session_date: dt.date,
    clock: Clock,
) -> KrSessionCalendarSnapshot:
    current = _current_calendar(path, session_date)
    if current is None:
        result = run_kis_kr_session_calendar_collect.main(
            (
                "--calendar-store",
                str(path),
                "--output-dir",
                str(cycle_root.expanduser().absolute() / "calendar" / session_date.isoformat()),
            ),
            clock=clock,
        )
        if result != 0:
            raise ValueError("current KR calendar collection failed")
        current = _current_calendar(path, session_date)
    if current is None:
        raise ValueError("current KR calendar missing")
    return current


def _current_calendar(path: Path, session_date: dt.date) -> KrSessionCalendarSnapshot | None:
    matches = tuple(
        snapshot
        for snapshot in KisKrSessionCalendarStore(path).snapshots()
        if any(day.session_date == session_date for day in snapshot.payload.days)
    )
    if not matches:
        return None
    return max(matches, key=lambda item: item.payload.observed_at)


def _prepare_outbox(path: Path) -> None:
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    os.chmod(path, 0o600)


if __name__ == "__main__":
    raise SystemExit(main())
