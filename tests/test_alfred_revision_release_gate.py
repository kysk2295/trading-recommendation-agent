from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.alfred_revision_panel import build_alfred_revision_panel
from trading_agent.alfred_revision_panel_models import AlfredRevisionPanel
from trading_agent.alfred_revision_release_gate import (
    AlfredRevisionReleaseGateError,
    build_alfred_revision_release_assessment,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_alfred_models import FredAlfredRequest, FredSourceMode
from trading_agent.fred_alfred_snapshot_models import (
    FredAlfredSnapshot,
    FredObservation,
)
from trading_agent.fred_vintage_dates_models import FredVintageDatesSnapshot
from trading_agent.private_immutable_file import publish_private_immutable_text

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_alfred_revision_release_gate.py"


def test_gate_requires_every_panel_vintage_in_exact_calendar() -> None:
    panel = _panel()
    calendar = _calendar((dt.date(2024, 3, 1), dt.date(2024, 4, 1)))

    assessment = build_alfred_revision_release_assessment(panel, calendar)

    assert assessment.status == "ready"
    assert assessment.panel_id == panel.panel_id
    assert assessment.calendar_snapshot_id == calendar.snapshot_id
    assert assessment.vintage_dates == panel.vintage_dates
    assert len(assessment.assessment_id) == 64

    with pytest.raises(AlfredRevisionReleaseGateError):
        _ = build_alfred_revision_release_assessment(
            panel,
            _calendar((dt.date(2024, 3, 1),)),
        )


def test_cli_publishes_ready_assessment_and_replays(tmp_path: Path) -> None:
    panel = _panel()
    calendar = _calendar(panel.vintage_dates)
    inputs = tmp_path / "inputs"
    panel_path = inputs / f"alfred_revision_panel_{panel.panel_id}.json"
    calendar_path = (
        inputs / f"fred_vintage_dates_snapshot_{calendar.snapshot_id}.json"
    )
    assert publish_private_immutable_text(
        panel_path,
        canonical_experiment_ledger_json(panel) + "\n",
    )
    assert publish_private_immutable_text(
        calendar_path,
        canonical_experiment_ledger_json(calendar) + "\n",
    )
    output = tmp_path / "output"

    first = _run_cli(panel_path, calendar_path, output)
    replay = _run_cli(panel_path, calendar_path, output)

    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    assert "artifact_created=yes" in first.stdout
    assert "artifact_created=no" in replay.stdout
    artifacts = tuple(
        output.glob("alfred_revision_release_assessment_*.json")
    )
    assert len(artifacts) == 1
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    report = (
        output / "alfred_revision_release_assessment_ko.md"
    ).read_text(encoding="utf-8")
    assert "- result: ready" in report
    assert "- provider network access: 0" in report
    for path in (*artifacts, output / "alfred_revision_release_assessment_ko.md"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def _panel() -> AlfredRevisionPanel:
    return build_alfred_revision_panel(
        (
            _snapshot(dt.date(2024, 3, 1), Decimal("3.1")),
            _snapshot(dt.date(2024, 4, 1), Decimal("3.2")),
        )
    )


def _snapshot(vintage: dt.date, value: Decimal) -> FredAlfredSnapshot:
    request = FredAlfredRequest(
        collection_id=f"alfred-dff-{vintage:%Y%m%d}",
        source_mode=FredSourceMode.ALFRED,
        series_id="DFF",
        observation_start=dt.date(2024, 1, 1),
        observation_end=dt.date(2024, 12, 1),
        vintage_date=vintage,
        limit=100,
    )
    return FredAlfredSnapshot(
        request_id=request.request_id,
        raw_receipt_id=("a" if vintage.month == 3 else "b") * 64,
        observed_at=dt.datetime(2026, 7, vintage.month, tzinfo=dt.UTC),
        source_mode=FredSourceMode.ALFRED,
        series_id="DFF",
        observation_start=request.observation_start,
        observation_end=request.observation_end,
        vintage_date=vintage,
        units="lin",
        observations=(
            FredObservation(
                realtime_start=vintage,
                realtime_end=vintage,
                observation_date=dt.date(2024, 1, 1),
                value=value,
            ),
        ),
    )


def _calendar(
    vintage_dates: tuple[dt.date, ...],
) -> FredVintageDatesSnapshot:
    return FredVintageDatesSnapshot(
        request_id="c" * 64,
        raw_receipt_id="d" * 64,
        observed_at=dt.datetime(2026, 7, 24, tzinfo=dt.UTC),
        series_id="DFF",
        realtime_start=dt.date(2024, 1, 1),
        realtime_end=dt.date(2024, 12, 31),
        vintage_dates=vintage_dates,
    )


def _run_cli(
    panel: Path,
    calendar: Path,
    output: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--panel",
            str(panel),
            "--vintage-calendar",
            str(calendar),
            "--output-dir",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
