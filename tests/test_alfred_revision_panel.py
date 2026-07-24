from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.alfred_revision_panel import (
    AlfredRevisionPanelError,
    build_alfred_revision_panel,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_alfred_models import FredAlfredRequest, FredSourceMode
from trading_agent.fred_alfred_snapshot_models import (
    FredAlfredSnapshot,
    FredObservation,
)
from trading_agent.private_immutable_file import publish_private_immutable_text

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_alfred_revision_panel.py"


def test_panel_preserves_release_and_revision_causality() -> None:
    first = _snapshot("2024-03-01", {"2024-01-01": "3.1"})
    second = _snapshot(
        "2024-04-01",
        {"2024-01-01": "3.2", "2024-02-01": None},
    )
    third = _snapshot(
        "2024-05-01",
        {"2024-01-01": "3.2", "2024-02-01": "3.4"},
    )

    panel = build_alfred_revision_panel((third, first, second))

    assert panel.vintage_dates == (
        dt.date(2024, 3, 1),
        dt.date(2024, 4, 1),
        dt.date(2024, 5, 1),
    )
    assert [cell.state for cell in panel.rows[1].cells] == [
        "not_observed",
        "missing",
        "available",
    ]
    assert [cell.revision_from_previous_available for cell in panel.rows[0].cells] == [
        None,
        Decimal("0.1"),
        Decimal("0.0"),
    ]
    assert panel.comparable_revision_count == 2
    assert panel.changed_revision_count == 1
    assert len(panel.panel_id) == 64


def test_panel_rejects_mixed_series_and_future_observation() -> None:
    first = _snapshot("2024-03-01", {"2024-01-01": "3.1"})
    mixed = _snapshot(
        "2024-04-01",
        {"2024-01-01": "3.2"},
        series_id="UNRATE",
    )
    with pytest.raises(AlfredRevisionPanelError):
        _ = build_alfred_revision_panel((first, mixed))

    future = _snapshot("2024-03-01", {"2024-04-01": "3.1"})
    with pytest.raises(AlfredRevisionPanelError):
        _ = build_alfred_revision_panel((first, future))


def test_cli_publishes_content_addressed_panel_and_exact_replay(
    tmp_path: Path,
) -> None:
    first = _snapshot("2024-03-01", {"2024-01-01": "3.1"})
    second = _snapshot(
        "2024-04-01",
        {"2024-01-01": "3.2", "2024-02-01": "3.4"},
    )
    inputs = tmp_path / "inputs"
    paths = tuple(_publish_snapshot(inputs, item) for item in (first, second))
    output = tmp_path / "output"

    first_run = _run_cli(tuple(reversed(paths)), output)
    replay = _run_cli(paths, output)

    assert first_run.returncode == 0, first_run.stderr
    assert replay.returncode == 0, replay.stderr
    assert "artifact_created=yes" in first_run.stdout
    assert "artifact_created=no" in replay.stdout
    artifacts = tuple(output.glob("alfred_revision_panel_*.json"))
    assert len(artifacts) == 1
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["vintage_dates"] == ["2024-03-01", "2024-04-01"]
    panel_id = artifacts[0].stem.removeprefix("alfred_revision_panel_")
    assert len(panel_id) == 64
    assert set(panel_id) <= set("0123456789abcdef")
    report = (output / "alfred_revision_panel_ko.md").read_text(encoding="utf-8")
    assert "- artifact created: no" in report
    assert "- provider network access: 0" in report
    for path in (*artifacts, output / "alfred_revision_panel_ko.md"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    bad_output = tmp_path / "bad-output"
    bad = _run_cli((paths[0],), bad_output)
    assert bad.returncode == 2
    assert not bad_output.exists()


def _snapshot(
    vintage: str,
    values: dict[str, str | None],
    *,
    series_id: str = "CPIAUCSL",
) -> FredAlfredSnapshot:
    vintage_date = dt.date.fromisoformat(vintage)
    request = FredAlfredRequest(
        collection_id=f"alfred-{series_id.lower()}-{vintage.replace('-', '')}",
        source_mode=FredSourceMode.ALFRED,
        series_id=series_id,
        observation_start=dt.date(2024, 1, 1),
        observation_end=dt.date(2024, 12, 1),
        vintage_date=vintage_date,
        limit=100,
    )
    return FredAlfredSnapshot(
        request_id=request.request_id,
        raw_receipt_id=("a" if vintage_date.month % 2 else "b") * 64,
        observed_at=dt.datetime(2026, 7, vintage_date.month, tzinfo=dt.UTC),
        source_mode=FredSourceMode.ALFRED,
        series_id=series_id,
        observation_start=request.observation_start,
        observation_end=request.observation_end,
        vintage_date=vintage_date,
        units="lin",
        observations=tuple(
            FredObservation(
                realtime_start=vintage_date,
                realtime_end=vintage_date,
                observation_date=dt.date.fromisoformat(date),
                value=None if value is None else Decimal(value),
            )
            for date, value in values.items()
        ),
    )


def _publish_snapshot(root: Path, snapshot: FredAlfredSnapshot) -> Path:
    path = root / f"fred_alfred_snapshot_{snapshot.snapshot_id}.json"
    assert publish_private_immutable_text(
        path,
        canonical_experiment_ledger_json(snapshot) + "\n",
    )
    return path


def _run_cli(
    snapshots: tuple[Path, ...],
    output: Path,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT)]
    for snapshot in snapshots:
        command.extend(("--snapshot", str(snapshot)))
    command.extend(("--output-dir", str(output)))
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
