#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.private_report import write_private_report
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore
from trading_agent.us_runtime_supervisor_live_summary import (
    RuntimeSupervisorLiveSummary,
    RuntimeSupervisorLiveSummaryError,
    summarize_runtime_supervisor_live_audit,
)

REPORT_NAME = "us_runtime_supervisor_live_audit_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query validated runtime supervisor live child audit aggregates.",
    )
    parser.add_argument("--supervisor-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source = args.supervisor_store.expanduser().absolute()
        if source.is_symlink() or not source.is_file():
            raise RuntimeSupervisorLiveSummaryError
        summary = summarize_runtime_supervisor_live_audit(RuntimeMinuteSupervisorStore(source))
    except (OSError, RuntimeSupervisorLiveSummaryError, TypeError, ValueError):
        _report(args.output_dir, ("result: blocked", "account/order mutation: 0"))
        return 1
    _report(args.output_dir, _details(summary))
    return 0


def _details(summary: RuntimeSupervisorLiveSummary) -> tuple[str, ...]:
    return (
        "result: ready",
        f"parent count: {summary.parent_count}",
        f"legacy parent count: {summary.legacy_parent_count}",
        f"child count: {summary.child_count}",
        f"disabled count: {summary.disabled_count}",
        f"not attempted count: {summary.not_attempted_count}",
        f"completed count: {summary.completed_count}",
        f"blocked count: {summary.blocked_count}",
        f"selected/new/replay: {summary.selected_count}/{summary.created_count}/{summary.replay_count}",
        "account/order mutation: 0",
    )


def _report(output_dir: Path, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# Runtime supervisor live audit",
            "",
            *(f"- {item}" for item in details),
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
