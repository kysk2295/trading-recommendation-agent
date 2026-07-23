#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb>=1.3", "pyarrow>=20", "pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_alpaca_option_skew.py --help
# ──────────────────

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.alpaca_option_skew import (
    build_alpaca_option_skew,
    publish_alpaca_option_skew,
)
from trading_agent.alpaca_option_skew_models import (
    AlpacaOptionSkew,
    AlpacaOptionSkewError,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "alpaca_option_skew_ko.md"


def main(
    call_surface: Annotated[Path, typer.Option("--call-surface")],
    put_surface: Annotated[Path, typer.Option("--put-surface")],
    spot_runtime_store: Annotated[
        Path,
        typer.Option("--spot-runtime-store"),
    ],
    spot_dataset: Annotated[Path, typer.Option("--spot-dataset")],
    output_dir: Annotated[Path, typer.Option()],
    maximum_observation_skew_seconds: Annotated[
        int,
        typer.Option("--max-observation-skew-seconds", min=0, max=300),
    ] = 300,
) -> None:
    try:
        skew = build_alpaca_option_skew(
            call_surface,
            put_surface,
            spot_runtime_store,
            spot_dataset,
            maximum_observation_skew_seconds,
        )
        _, created = publish_alpaca_option_skew(output_dir, skew)
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(skew, created),
        )
    except (
        AlpacaOptionSkewError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("source-backed Alpaca option skew is invalid") from None
    typer.echo(f"complete source-backed Alpaca option skew artifact_created={'yes' if created else 'no'}")


def _report(skew: AlpacaOptionSkew, created: bool) -> str:
    lines = [
        "# Alpaca Option Skew",
        "",
        "> M6 shadow-only source-backed skew evidence; not a recommendation or order.",
        "",
        f"- result: {skew.status.value}",
        f"- underlying symbol: {skew.underlying_symbol}",
        f"- source feed: {skew.feed.value}",
        f"- expiration date: {skew.expiration_date.isoformat()}",
        f"- spot completed at: {skew.spot_bar_completed_at.isoformat()}",
        f"- observation skew seconds: {skew.observation_skew_seconds}",
    ]
    lines.extend(
        f"- strike bucket {item.bucket_id}: "
        f"matches={item.matched_strike_count}, "
        f"put_minus_call_iv={item.median_put_minus_call_iv}"
        for item in skew.strike_buckets
    )
    lines.extend(
        f"- delta bucket {item.bucket_id}: "
        f"call={item.call_observation_count}, "
        f"put={item.put_observation_count}, "
        f"put_minus_call_iv={item.put_minus_call_median_iv}"
        for item in skew.delta_buckets
    )
    lines.extend(
        (
            f"- artifact created: {'yes' if created else 'no'}",
            "- network access: 0",
            "- provider operation: query-only local evidence aggregation",
            "- broker, account, or order mutation: none",
            "",
        )
    )
    return "\n".join(lines)


if __name__ == "__main__":
    typer.run(main)
