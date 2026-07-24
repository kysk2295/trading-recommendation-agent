#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.alfred_revision_panel_models import AlfredRevisionPanel
from trading_agent.alfred_revision_release_gate import (
    AlfredRevisionReleaseAssessment,
    AlfredRevisionReleaseGateError,
    build_alfred_revision_release_assessment,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_vintage_dates_models import FredVintageDatesSnapshot
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "alfred_revision_release_assessment_ko.md"


def main(
    panel: Annotated[Path, typer.Option()],
    vintage_calendar: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
) -> None:
    try:
        checked_panel = _read_panel(panel)
        checked_calendar = _read_calendar(vintage_calendar)
        assessment = build_alfred_revision_release_assessment(
            checked_panel,
            checked_calendar,
        )
        created = publish_private_immutable_text(
            output_dir
            / (
                "alfred_revision_release_assessment_"
                f"{assessment.assessment_id}.json"
            ),
            canonical_experiment_ledger_json(assessment) + "\n",
        )
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(assessment, created),
        )
    except (
        AlfredRevisionReleaseGateError,
        InvalidPrivateImmutableFileError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter(
            "ALFRED revision release evidence is invalid"
        ) from None
    typer.echo(
        "complete ALFRED revision release assessment "
        f"artifact_created={'yes' if created else 'no'}"
    )


def _read_panel(path: Path) -> AlfredRevisionPanel:
    payload = read_private_text(path)
    panel = AlfredRevisionPanel.model_validate_json(payload)
    if (
        path.name != f"alfred_revision_panel_{panel.panel_id}.json"
        or payload != canonical_experiment_ledger_json(panel) + "\n"
    ):
        raise AlfredRevisionReleaseGateError
    return panel


def _read_calendar(path: Path) -> FredVintageDatesSnapshot:
    payload = read_private_text(path)
    calendar = FredVintageDatesSnapshot.model_validate_json(payload)
    if (
        path.name
        != f"fred_vintage_dates_snapshot_{calendar.snapshot_id}.json"
        or payload != canonical_experiment_ledger_json(calendar) + "\n"
    ):
        raise AlfredRevisionReleaseGateError
    return calendar


def _report(
    assessment: AlfredRevisionReleaseAssessment,
    created: bool,
) -> str:
    return "\n".join(
        (
            "# ALFRED Revision Release Assessment",
            "",
            "> Exact panel vintages admitted by an official FRED change calendar.",
            "",
            "- result: ready",
            f"- admitted vintage count: {len(assessment.vintage_dates)}",
            f"- artifact created: {'yes' if created else 'no'}",
            "- panel identity bound: yes",
            "- release calendar identity bound: yes",
            "- future or arbitrary vintage admitted: no",
            "- provider network access: 0",
            "- credential access: 0",
            "- broker, account, order, lifecycle, or allocation mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
