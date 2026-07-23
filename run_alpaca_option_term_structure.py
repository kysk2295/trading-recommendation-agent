#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_alpaca_option_term_structure.py --help
# ──────────────────

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.alpaca_option_term_structure import (
    build_alpaca_option_term_structure,
    publish_alpaca_option_term_structure,
)
from trading_agent.alpaca_option_term_structure_models import (
    AlpacaOptionTermStructure,
    AlpacaOptionTermStructureError,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "alpaca_option_term_structure_ko.md"


def main(
    surfaces: Annotated[list[Path], typer.Option("--surface")],
    output_dir: Annotated[Path, typer.Option()],
    maximum_observation_skew_seconds: Annotated[
        int,
        typer.Option("--max-observation-skew-seconds", min=0, max=300),
    ] = 300,
) -> None:
    try:
        structure = build_alpaca_option_term_structure(
            tuple(surfaces),
            maximum_observation_skew_seconds,
        )
        _, created = publish_alpaca_option_term_structure(output_dir, structure)
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(structure, created),
        )
    except (
        AlpacaOptionTermStructureError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter(
            "bounded Alpaca option term structure is invalid"
        ) from None
    typer.echo(
        "complete bounded Alpaca option term structure "
        f"artifact_created={'yes' if created else 'no'}"
    )


def _report(structure: AlpacaOptionTermStructure, created: bool) -> str:
    lines = [
        "# Alpaca Option Term Structure",
        "",
        "> M6 shadow-only multi-expiration evidence; "
        "not a recommendation or order.",
        "",
        f"- result: {structure.status.value}",
        f"- underlying symbol: {structure.underlying_symbol}",
        f"- source feed: {structure.feed.value}",
        f"- market date: {structure.market_date.isoformat()}",
        f"- expiration count: {structure.expiration_count}",
        f"- surface count: {structure.surface_count}",
        f"- maximum observation skew seconds: "
        f"{structure.maximum_observation_skew_seconds}",
    ]
    lines.extend(
        (
            f"- slice {item.expiration_date.isoformat()} "
            f"{item.contract_type.value}: contracts={item.contract_count}, "
            f"oi={item.open_interest_observation_count}, "
            f"iv={item.implied_volatility_observation_count}, "
            f"total_oi={item.total_open_interest}, "
            f"median_iv={item.median_implied_volatility}"
        )
        for item in structure.slices
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
