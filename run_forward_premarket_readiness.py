#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_forward_premarket_readiness.py --help
# ──────────────────

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.forward_premarket_readiness import (
    PremarketReadiness,
    PremarketReadinessError,
    audit_forward_premarket_readiness,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "forward_premarket_readiness_ko.md"


def _clock() -> dt.datetime:
    return dt.datetime.now().astimezone()


def main(
    session_dir: Annotated[Path, typer.Argument()],
    session_date: Annotated[str, typer.Option()],
    minimum_cycles: Annotated[int, typer.Option()] = 12,
    maximum_latest_age_seconds: Annotated[int, typer.Option()] = 600,
    minimum_latest_selected: Annotated[int, typer.Option()] = 1,
    output_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        result = audit_forward_premarket_readiness(
            session_dir,
            dt.date.fromisoformat(session_date),
            minimum_cycles,
            maximum_latest_age_seconds,
            minimum_latest_selected,
            _clock(),
        )
        destination = (
            output_dir
            if output_dir is not None
            else session_dir / "premarket_readiness"
        )
        write_private_stable_report(
            destination / REPORT_NAME,
            _report(result),
        )
    except (
        InvalidPrivateStableReportError,
        OSError,
        PremarketReadinessError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter(
            "strict forward premarket readiness input is invalid"
        ) from None
    typer.echo(
        "forward premarket readiness "
        + ("ready" if result.ready else "blocked")
    )
    if not result.ready:
        raise typer.Exit(code=1)


def _report(result: PremarketReadiness) -> str:
    latest = (
        "none"
        if result.latest_observed_at is None
        else result.latest_observed_at.isoformat()
    )
    age = (
        "none"
        if result.latest_age_seconds is None
        else str(result.latest_age_seconds)
    )
    return "\n".join(
        (
            "# Forward premarket strict readiness",
            "",
            "> Current-session read-only quality gate; not a recommendation.",
            "",
            f"- result: {'ready' if result.ready else 'blocked'}",
            f"- session date: {result.session_date.isoformat()}",
            f"- input SHA-256: {result.input_sha256}",
            f"- premarket cycles: {result.premarket_cycles}",
            f"- ranking requests: {result.ranking_requests}",
            f"- ranking snapshot rows: {result.ranking_snapshot_rows}",
            f"- latest observed at: {latest}",
            f"- latest age seconds: {age}",
            (
                "- latest selected candidates: "
                f"{result.latest_selected_candidates}"
            ),
            *(f"- blocker: {blocker}" for blocker in result.blockers),
            "- quality gate relaxed: false",
            "- external provider/account/order mutation: 0",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
