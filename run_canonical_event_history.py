#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.4", "pyarrow==25.0.0", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

from trading_agent.canonical_event_history import (
    CanonicalEventHistoryError,
    replay_canonical_event_history,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "canonical_event_history_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="검증된 canonical Parquet dataset의 correction/tombstone history를 local-only 재생"
    )
    parser.add_argument("--dataset", action="append", type=Path, required=True)
    parser.add_argument("--as-of", type=_aware_datetime, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        replay = replay_canonical_event_history(tuple(args.dataset), as_of=args.as_of)
    except CanonicalEventHistoryError:
        _write_report(args.output_dir, result="blocked", details=("history validation: failed",))
        return 1
    _write_report(
        args.output_dir,
        result="ready",
        details=(
            "history validation: passed",
            f"verified dataset: {len(replay.dataset_ids)}",
            f"observed event: {replay.observed_event_count}",
            f"active event: {len(replay.active_events)}",
            f"superseded event: {len(replay.superseded_event_ids)}",
            f"tombstoned root: {len(replay.tombstoned_root_event_ids)}",
        ),
    )
    return 0


def _aware_datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("as-of는 timezone이 포함된 ISO-8601이어야 합니다") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("as-of는 timezone이 포함된 ISO-8601이어야 합니다")
    return parsed


def _write_report(output_dir: Path, *, result: str, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# Canonical event history replay",
            "",
            "> local Parquet/DuckDB replay only. Provider, credential, account, and order access are disabled.",
            "",
            f"- 결과: {result}",
            *(f"- {detail}" for detail in details),
            "- network access: 0",
            "- broker mutation: 0",
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
