#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

from trading_agent.intraday_research_input_binding import (
    bind_intraday_research_input,
)
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchInputBindingError,
    IntradayResearchInputBindingRequest,
    IntradayResearchStrategyBinding,
)
from trading_agent.private_report import write_private_report
from trading_agent.strategy_factory import StrategyMode

REPORT_NAME = "intraday_research_input_binding_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bind an actual causal intraday dataset to READY foundations and a v2 research manifest"
    )
    parser.add_argument("--dataset-csv", type=Path, required=True)
    parser.add_argument("--dataset-receipt", type=Path, required=True)
    parser.add_argument("--entitlement-contract", type=Path, required=True)
    parser.add_argument("--source-queue-artifact", type=Path, required=True)
    parser.add_argument(
        "--strategy-binding",
        type=_strategy_binding,
        action="append",
        required=True,
        metavar="STRATEGY,VERSION,CARD_SHA256",
    )
    parser.add_argument("--code-version", required=True)
    parser.add_argument("--registered-at", type=_aware_datetime, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-training-sessions", type=int, default=0)
    parser.add_argument("--max-bars", type=int, default=100_000)
    parser.add_argument("--max-sessions", type=int, default=60)
    parser.add_argument("--per-side-fee-bps", type=int, default=5)
    parser.add_argument("--per-side-slippage-bps", type=int, default=15)
    parser.add_argument("--bootstrap-samples", type=int, default=1_000)
    parser.add_argument("--rss-limit-gib", type=float, default=9.5)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = bind_intraday_research_input(
            IntradayResearchInputBindingRequest(
                dataset_csv=args.dataset_csv,
                dataset_receipt=args.dataset_receipt,
                entitlement_contract=args.entitlement_contract,
                source_queue_artifact=args.source_queue_artifact,
                output_root=args.output_dir,
                strategy_bindings=tuple(args.strategy_binding),
                code_version=args.code_version,
                registered_at=args.registered_at,
                observed_at=dt.datetime.now(dt.UTC),
                minimum_training_sessions=args.minimum_training_sessions,
                max_bars=args.max_bars,
                max_sessions=args.max_sessions,
                per_side_fee_bps=args.per_side_fee_bps,
                per_side_slippage_bps=args.per_side_slippage_bps,
                bootstrap_samples=args.bootstrap_samples,
                rss_limit_gib=args.rss_limit_gib,
            )
        )
    except (IntradayResearchInputBindingError, OSError, TypeError, ValueError):
        write_private_report(
            args.output_dir / REPORT_NAME,
            "# Intraday research input binding\n\n- result: blocked\n- external mutation: 0\n",
        )
        return 1
    foundation_lines = "".join(f"- foundation sha256: {digest}\n" for digest in result.foundation_sha256s)
    write_private_report(
        args.output_dir / REPORT_NAME,
        "# Intraday research input binding\n\n"
        "- result: ready\n"
        + f"- input sha256: {result.input_sha256}\n"
        + f"- manifest sha256: {result.manifest_sha256}\n"
        + f"- foundations: {len(result.foundation_paths)}\n"
        + foundation_lines
        + f"- created: {str(result.created).lower()}\n"
        + "- external mutation: 0\n",
    )
    return 0


def _strategy_binding(value: str) -> IntradayResearchStrategyBinding:
    try:
        strategy, version, card_key = value.split(",")
        return IntradayResearchStrategyBinding(
            strategy=StrategyMode(strategy),
            strategy_version=version,
            queue_card_key=card_key,
        )
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("strategy binding must be STRATEGY,VERSION,CARD_SHA256") from None


def _aware_datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except ValueError:
        raise argparse.ArgumentTypeError("registered-at must be an ISO-8601 timestamp with offset") from None


if __name__ == "__main__":
    raise SystemExit(main())
