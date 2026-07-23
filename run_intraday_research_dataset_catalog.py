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

from trading_agent.intraday_research_dataset_catalog import (
    materialize_intraday_research_dataset_catalog,
)
from trading_agent.intraday_research_dataset_catalog_models import (
    IntradayResearchDatasetCatalogError,
    IntradayResearchDatasetCatalogRequest,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "intraday_research_dataset_catalog_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit and accumulate strict point-in-time sessions for intraday research"
    )
    parser.add_argument("--session-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--producer-commit-sha", required=True)
    parser.add_argument("--minimum-sessions", type=int, default=1)
    parser.add_argument("--max-sessions", type=int, default=60)
    parser.add_argument("--max-bars", type=int, default=100_000)
    parser.add_argument("--required-session-date", type=_session_date, action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = materialize_intraday_research_dataset_catalog(
            IntradayResearchDatasetCatalogRequest(
                session_dirs=tuple(args.session_dir),
                output_root=args.output_dir,
                minimum_sessions=args.minimum_sessions,
                max_sessions=args.max_sessions,
                max_bars=args.max_bars,
                producer_commit_sha=args.producer_commit_sha,
                required_session_dates=tuple(args.required_session_date),
            )
        )
    except (IntradayResearchDatasetCatalogError, OSError, TypeError, ValueError):
        write_private_report(
            args.output_dir / REPORT_NAME,
            "# Intraday research dataset catalog\n\n"
            "- result: blocked\n"
            "- external mutation: 0\n",
        )
        return 1
    dataset = result.dataset
    write_private_report(
        args.output_dir / REPORT_NAME,
        "# Intraday research dataset catalog\n\n"
        "- result: ready\n"
        + f"- candidate sessions: {result.candidate_sessions}\n"
        + f"- selected sessions: {dataset.session_count}\n"
        + f"- blocked sessions: {result.blocked_sessions}\n"
        + f"- input sha256: {dataset.input_sha256}\n"
        + f"- catalog receipt sha256: {result.catalog_receipt_sha256}\n"
        + f"- bars: {dataset.bar_count}\n"
        + "- producer commit bound: true\n"
        + f"- created: {str(result.created).lower()}\n"
        + "- quality gate: strict\n"
        + "- external mutation: 0\n",
    )
    return 0


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("required session date must be YYYY-MM-DD") from None


if __name__ == "__main__":
    raise SystemExit(main())
