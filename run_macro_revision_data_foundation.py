#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Annotated, Final

import typer
from pydantic import ValidationError

from trading_agent.alfred_revision_panel_models import AlfredRevisionPanel
from trading_agent.alfred_revision_release_gate import (
    AlfredRevisionReleaseAssessment,
)
from trading_agent.data_capability_models import DataSourceId
from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_vintage_dates_models import FredVintageDatesSnapshot
from trading_agent.macro_revision_data_foundation import (
    MacroRevisionDataFoundation,
    MacroRevisionDataFoundationError,
    build_macro_revision_data_foundation,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "macro_revision_data_foundation_ko.md"
FRED_OBSERVATIONS: Final = DataSourceId(
    provider="fred",
    feed="series_observations",
)
ALFRED_OBSERVATIONS: Final = DataSourceId(
    provider="alfred",
    feed="vintage_observations",
)
FRED_VINTAGE_DATES: Final = DataSourceId(
    provider="fred",
    feed="series_vintage_dates",
)


def main(
    panel: Annotated[Path, typer.Option()],
    vintage_calendar: Annotated[Path, typer.Option()],
    release_assessment: Annotated[Path, typer.Option()],
    fred_alfred_registry: Annotated[Path, typer.Option()],
    vintage_calendar_registry: Annotated[Path, typer.Option()],
    evaluated_at: Annotated[str, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
) -> None:
    try:
        evaluated = _datetime(evaluated_at)
        checked_panel, panel_payload = _read_panel(panel)
        calendar, calendar_payload = _read_calendar(vintage_calendar)
        assessment, assessment_payload = _read_assessment(release_assessment)
        observations = DataCapabilityRegistryStore(
            fred_alfred_registry
        ).snapshot(
            as_of=evaluated,
            source_ids=(ALFRED_OBSERVATIONS, FRED_OBSERVATIONS),
        )
        calendar_source = DataCapabilityRegistryStore(
            vintage_calendar_registry
        ).snapshot(
            as_of=evaluated,
            source_ids=(FRED_VINTAGE_DATES,),
        )
        if (
            observations.missing_capability_source_ids
            or observations.missing_entitlement_source_ids
            or calendar_source.missing_capability_source_ids
            or calendar_source.missing_entitlement_source_ids
        ):
            raise MacroRevisionDataFoundationError
        foundation = build_macro_revision_data_foundation(
            panel=checked_panel,
            calendar=calendar,
            assessment=assessment,
            panel_file_sha256=_sha(panel_payload),
            calendar_file_sha256=_sha(calendar_payload),
            assessment_file_sha256=_sha(assessment_payload),
            capabilities=(
                *observations.capabilities,
                *calendar_source.capabilities,
            ),
            entitlements=(
                *observations.entitlements,
                *calendar_source.entitlements,
            ),
            evaluated_at=evaluated,
        )
        created = publish_private_immutable_text(
            output_dir
            / (
                "macro_revision_data_foundation_"
                f"{foundation.foundation_id}.json"
            ),
            canonical_experiment_ledger_json(foundation) + "\n",
        )
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(foundation, created),
        )
    except (
        DataCapabilityRegistryError,
        InvalidPrivateImmutableFileError,
        InvalidPrivateStableReportError,
        MacroRevisionDataFoundationError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise typer.BadParameter(
            "macro revision data foundation input is invalid"
        ) from None
    typer.echo(
        "complete macro revision data foundation "
        f"artifact_created={'yes' if created else 'no'}"
    )


def _read_panel(path: Path) -> tuple[AlfredRevisionPanel, str]:
    payload = read_private_text(path)
    model = AlfredRevisionPanel.model_validate_json(payload)
    if (
        path.name != f"alfred_revision_panel_{model.panel_id}.json"
        or payload != canonical_experiment_ledger_json(model) + "\n"
    ):
        raise MacroRevisionDataFoundationError
    return model, payload


def _read_calendar(path: Path) -> tuple[FredVintageDatesSnapshot, str]:
    payload = read_private_text(path)
    model = FredVintageDatesSnapshot.model_validate_json(payload)
    if (
        path.name
        != f"fred_vintage_dates_snapshot_{model.snapshot_id}.json"
        or payload != canonical_experiment_ledger_json(model) + "\n"
    ):
        raise MacroRevisionDataFoundationError
    return model, payload


def _read_assessment(
    path: Path,
) -> tuple[AlfredRevisionReleaseAssessment, str]:
    payload = read_private_text(path)
    model = AlfredRevisionReleaseAssessment.model_validate_json(payload)
    if (
        path.name
        != (
            "alfred_revision_release_assessment_"
            f"{model.assessment_id}.json"
        )
        or payload != canonical_experiment_ledger_json(model) + "\n"
    ):
        raise MacroRevisionDataFoundationError
    return model, payload


def _datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MacroRevisionDataFoundationError
    return parsed


def _sha(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def _report(
    foundation: MacroRevisionDataFoundation,
    created: bool,
) -> str:
    decision = foundation.data_manifest.evaluate_data_readiness()
    return "\n".join(
        (
            "# Macro Revision Data Foundation",
            "",
            "> Exact FRED and ALFRED evidence admitted for causal macro research.",
            "",
            f"- result: {decision.status.value}",
            f"- ready requirements: {len(decision.evaluations)}/3",
            f"- artifact created: {'yes' if created else 'no'}",
            "- exact panel file identity bound: yes",
            "- exact release calendar file identity bound: yes",
            "- exact release assessment file identity bound: yes",
            "- provider network access: 0",
            "- credential access: 0",
            "- broker, account, order, lifecycle, or allocation mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
