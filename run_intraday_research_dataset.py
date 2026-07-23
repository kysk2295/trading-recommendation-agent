#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.intraday_research_dataset import (
    IntradayResearchDatasetError,
    IntradayResearchDatasetRequest,
    materialize_intraday_research_dataset,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "intraday_research_dataset_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize quality-eligible point-in-time sessions for bounded intraday research"
    )
    parser.add_argument("--session-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--producer-commit-sha", required=True)
    parser.add_argument("--max-sessions", type=int, default=60)
    parser.add_argument("--max-bars", type=int, default=100_000)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = materialize_intraday_research_dataset(
            IntradayResearchDatasetRequest(
                session_dirs=tuple(args.session_dir),
                output_root=args.output_dir,
                max_sessions=args.max_sessions,
                max_bars=args.max_bars,
                producer_commit_sha=args.producer_commit_sha,
            )
        )
    except (IntradayResearchDatasetError, OSError, TypeError, ValueError):
        write_private_report(
            args.output_dir / REPORT_NAME,
            "# Intraday point-in-time research dataset\n\n"
            "- result: blocked\n"
            "- external mutation: 0\n",
        )
        return 1
    write_private_report(
        args.output_dir / REPORT_NAME,
        "# Intraday point-in-time research dataset\n\n"
        "- result: ready\n"
        + f"- input sha256: {result.input_sha256}\n"
        + f"- sessions: {result.session_count}\n"
        + f"- eligible symbol sessions: {result.eligible_symbol_sessions}\n"
        + f"- censored symbol sessions: {result.censored_symbol_sessions}\n"
        + f"- bars: {result.bar_count}\n"
        + f"- csv: {result.csv_path.name}\n"
        + f"- receipt: {result.receipt_path.name}\n"
        + "- producer commit bound: true\n"
        + f"- created: {str(result.created).lower()}\n"
        + "- external mutation: 0\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
