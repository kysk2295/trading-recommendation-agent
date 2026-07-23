#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.futures_positioning_context import (
    build_futures_positioning_context,
    load_cftc_tff_context_artifact,
    load_futures_positioning_binding,
    load_futures_roll_master_artifact,
    publish_futures_positioning_context,
)
from trading_agent.futures_positioning_context_models import (
    FuturesPositioningContext,
    FuturesPositioningContextError,
    FuturesPositioningJoinRequest,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "futures_positioning_context_ko.md"


def main(
    cftc_context: Annotated[Path, typer.Option("--cftc-context")],
    futures_master: Annotated[Path, typer.Option("--futures-master")],
    binding: Annotated[Path, typer.Option()],
    as_of: Annotated[str, typer.Option("--as-of")],
    maximum_report_age_days: Annotated[
        int,
        typer.Option("--maximum-report-age-days", min=1, max=31),
    ] = 14,
    output_dir: Annotated[Path, typer.Option()] = Path("outputs"),
) -> None:
    try:
        request = FuturesPositioningJoinRequest(
            cftc=load_cftc_tff_context_artifact(cftc_context),
            futures_master=load_futures_roll_master_artifact(
                futures_master,
            ),
            binding=load_futures_positioning_binding(binding),
            as_of=dt.datetime.fromisoformat(as_of),
            maximum_report_age_days=maximum_report_age_days,
        )
        context = build_futures_positioning_context(request)
        _, created = publish_futures_positioning_context(
            output_dir,
            context,
        )
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(context, created),
        )
    except (
        FuturesPositioningContextError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter(
            "futures positioning context is invalid",
        ) from None
    typer.echo(
        f"complete futures positioning context artifact_created={'yes' if created else 'no'}",
    )


def _report(
    context: FuturesPositioningContext,
    created: bool,
) -> str:
    return "\n".join(
        (
            "# Futures Positioning Context",
            "",
            "> M6 as-of shadow context; not licensed market data, a recommendation, or an order.",
            "",
            "- result: ready",
            f"- root symbol: {context.root_symbol}",
            f"- venue: {context.active_instrument.venue}",
            f"- as of: {context.as_of.isoformat()}",
            f"- latest report date: {context.latest_report_date.isoformat()}",
            f"- previous report date: {context.previous_report_date.isoformat()}",
            f"- maximum report age days: {context.maximum_report_age_days}",
            f"- category count: {len(context.categories)}",
            "- active contract: present",
            f"- artifact created: {'yes' if created else 'no'}",
            "- network access: 0",
            "- provider operation: query-only private artifacts",
            "- broker, account, order, recommendation, or allocation mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
