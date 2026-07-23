#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_futures_roll_security_master.py --help
# ──────────────────

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.futures_roll_security_master import (
    FuturesRollSecurityMaster,
    FuturesRollSecurityMasterError,
    load_futures_roll_security_master,
    publish_futures_roll_security_master,
    resolve_active_futures_contract,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "futures_roll_security_master_ko.md"


def main(
    manifest: Annotated[Path, typer.Option()],
    as_of: Annotated[str, typer.Option("--as-of")],
    output_dir: Annotated[Path, typer.Option()],
) -> None:
    try:
        parsed_as_of = dt.datetime.fromisoformat(as_of)
        master = load_futures_roll_security_master(manifest)
        _ = resolve_active_futures_contract(master, parsed_as_of)
        _, created = publish_futures_roll_security_master(
            output_dir,
            master,
        )
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(master, created),
        )
    except (
        FuturesRollSecurityMasterError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("futures roll security master is invalid") from None
    typer.echo(f"complete futures roll security master artifact_created={'yes' if created else 'no'}")


def _report(
    master: FuturesRollSecurityMaster,
    created: bool,
) -> str:
    first = master.contracts[0]
    return "\n".join(
        (
            "# Futures Roll Security Master",
            "",
            "> M6 provider-neutral local contract; not licensed market data, a recommendation, or an order.",
            "",
            "- result: ready",
            f"- root symbol: {master.root_symbol}",
            f"- venue: {first.instrument.venue}",
            f"- contract count: {len(master.contracts)}",
            "- active contract: present",
            f"- artifact created: {'yes' if created else 'no'}",
            "- network access: 0",
            "- provider operation: query-only private manifest",
            "- broker, account, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
