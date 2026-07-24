#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.alfred_revision_panel import (
    AlfredRevisionPanelError,
    build_alfred_revision_panel,
)
from trading_agent.alfred_revision_panel_models import AlfredRevisionPanel
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_alfred_snapshot_models import FredAlfredSnapshot
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "alfred_revision_panel_ko.md"


def main(
    snapshot: Annotated[list[Path], typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
) -> None:
    try:
        panel = build_alfred_revision_panel(tuple(_read(item) for item in snapshot))
        created = publish_private_immutable_text(
            output_dir / f"alfred_revision_panel_{panel.panel_id}.json",
            canonical_experiment_ledger_json(panel) + "\n",
        )
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(panel, created),
        )
    except (
        AlfredRevisionPanelError,
        InvalidPrivateImmutableFileError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("ALFRED revision panel input is invalid") from None
    typer.echo(
        "complete ALFRED revision panel "
        f"artifact_created={'yes' if created else 'no'}"
    )


def _read(path: Path) -> FredAlfredSnapshot:
    payload = read_private_text(path)
    snapshot = FredAlfredSnapshot.model_validate_json(payload)
    if (
        path.name != f"fred_alfred_snapshot_{snapshot.snapshot_id}.json"
        or payload != canonical_experiment_ledger_json(snapshot) + "\n"
    ):
        raise AlfredRevisionPanelError
    return snapshot


def _report(panel: AlfredRevisionPanel, created: bool) -> str:
    return "\n".join(
        (
            "# ALFRED Revision Panel",
            "",
            "> Query-only point-in-time research artifact from exact ALFRED vintages.",
            "",
            "- result: ready",
            f"- vintage count: {len(panel.vintage_dates)}",
            f"- observation row count: {len(panel.rows)}",
            f"- comparable revision count: {panel.comparable_revision_count}",
            f"- changed revision count: {panel.changed_revision_count}",
            f"- artifact created: {'yes' if created else 'no'}",
            "- source snapshots content-addressed: yes",
            "- latest FRED values used as historical backfill: no",
            "- provider network access: 0",
            "- credential access: 0",
            "- broker, account, order, lifecycle, or allocation mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
