#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "rich>=13.9", "typer>=0.15"]
# ///
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run run_forward_session_progress.py --help
# 3. Or make executable and run:
#      chmod +x run_forward_session_progress.py
#      ./run_forward_session_progress.py --help
# ──────────────────

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from trading_agent.forward_session_progress import (
    ForwardSessionProgress,
    ForwardSessionProgressError,
    audit_forward_session_progress,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "forward_session_progress_ko.md"


def main(
    session_dir: Annotated[Path, typer.Argument()],
    minimum_watch_cycles: Annotated[int, typer.Option()] = 1,
    output_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        result = audit_forward_session_progress(
            session_dir,
            minimum_watch_cycles,
        )
    except ForwardSessionProgressError as error:
        raise typer.BadParameter(error.reason) from error
    output = output_dir if output_dir is not None else session_dir / "progress"
    write_private_report(output / REPORT_NAME, _report(result, minimum_watch_cycles))
    status = "progress_clean" if result.clean else "blocked"
    rprint(f"[green]forward progress audit[/green] result={status}")
    if not result.clean:
        raise typer.Exit(code=1)


def _report(
    result: ForwardSessionProgress,
    minimum_watch_cycles: int,
) -> str:
    quality = result.quality
    watch_cycles = 0 if quality is None else quality.watch_cycles
    ranking_cycles = 0 if quality is None else quality.ranking_cycles
    retry_cycles = 0 if quality is None else quality.read_retry_cycles
    input_cycles = 0 if quality is None else quality.candidate_input_cycles
    status = "progress_clean" if result.clean else "blocked"
    return "\n".join(
        (
            "# Forward session strict progress audit",
            "",
            "> 장중 partial invariant 감사이며 최종 session eligibility가 아닙니다.",
            "",
            f"- result: {status}",
            f"- minimum watch cycles: {minimum_watch_cycles}",
            f"- watch cycles: {watch_cycles}",
            f"- ranking cycles: {ranking_cycles}",
            f"- retry cycles: {retry_cycles}",
            f"- candidate input cycles: {input_cycles}",
            *(f"- blocker: {blocker}" for blocker in result.blockers),
            *(f"- incident: {incident}" for incident in result.incidents),
            "- final eligibility: pending_post_session",
            "- quality gate relaxed: false",
            "- external provider/account/order mutation: 0",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
