from __future__ import annotations

import argparse
import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.us_day_session_tick import UsDaySessionTickRequest, run_us_day_session_tick


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project current US evidence and run one restart-safe human Day Agent tick."
    )
    parser.add_argument("--scanner", type=Path, required=True)
    parser.add_argument("--articles", type=Path, required=True)
    parser.add_argument("--news-evidence", type=Path, required=True)
    parser.add_argument("--market-context", type=Path, required=True)
    parser.add_argument("--quote", type=Path, action="append", required=True)
    parser.add_argument("--completed-tick", type=Path, action="append", required=True)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--version-store", type=Path)
    parser.add_argument("--production-manifest", type=Path)
    parser.add_argument("--strategy-manifest", type=Path)
    parser.add_argument("--experiment-ledger", type=Path)
    parser.add_argument("--day-model-responses", type=Path)
    parser.add_argument("--thesis-model-response", type=Path)
    parser.add_argument("--live-model-provider")
    parser.add_argument("--now", required=True)
    parser.add_argument("--entry-cutoff-minutes", type=int, default=15)
    parser.add_argument("--eod-minutes", type=int, default=5)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        request = UsDaySessionTickRequest(
            scanner=args.scanner,
            articles=args.articles,
            news_evidence=args.news_evidence,
            market_context=args.market_context,
            quotes=tuple(args.quote),
            completed_ticks=tuple(args.completed_tick),
            outputs=args.outputs,
            evaluated_at=_now(args.now),
            version_store=args.version_store,
            production_manifest=args.production_manifest,
            strategy_manifest=args.strategy_manifest,
            experiment_ledger=args.experiment_ledger,
            day_model_responses=args.day_model_responses,
            thesis_model_response=args.thesis_model_response,
            live_model_provider=args.live_model_provider,
            entry_cutoff_minutes=args.entry_cutoff_minutes,
            eod_minutes=args.eod_minutes,
        )
        code, result = run_us_day_session_tick(request)
    except (TypeError, ValidationError, ValueError):
        print(
            json.dumps(
                {"mutation": "0", "reason": "session_tick_input_invalid", "status": "blocked"},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2
    print(result.model_dump_json(exclude_none=True))
    return code


def _now(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(dt.UTC)


if __name__ == "__main__":
    raise SystemExit(main())
