from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

import run_research_evidence_read_model as cli
from tests.test_research_evidence_read_model import AS_OF, _event, _extraction
from trading_agent.research_evidence_artifact import load_research_evidence_artifact
from trading_agent.research_evidence_models import ClaimStance


def test_help_exposes_local_evidence_projection_only() -> None:
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])

    assert raised.value.code == 0


def test_valid_bundle_writes_immutable_private_artifact_and_replays(tmp_path: Path) -> None:
    input_path = _input(tmp_path)
    artifact_root = tmp_path / "artifacts"
    output = tmp_path / "report"
    arguments = _arguments(input_path, artifact_root, output)

    first = cli.main(arguments)
    first_report = (output / cli.REPORT_NAME).read_text()
    second = cli.main(arguments)
    report_path = output / cli.REPORT_NAME
    second_report = report_path.read_text()
    artifacts = tuple(artifact_root.glob("research_evidence_*.json"))

    assert first == second == 0
    assert "result: ready" in first_report
    assert "artifact append: new" in first_report
    assert "artifact append: replay" in second_report
    assert "claim count: 1" in second_report
    assert len(artifacts) == 1
    assert load_research_evidence_artifact(artifacts[0]).claims[0].independent_source_count == 2
    assert stat.S_IMODE(artifact_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert "raw_receipt_ref" not in artifacts[0].read_text()


def test_nonprivate_or_malformed_input_is_blocked(tmp_path: Path) -> None:
    input_path = _input(tmp_path)
    input_path.chmod(0o644)
    output = tmp_path / "report"

    code = cli.main(_arguments(input_path, tmp_path / "artifacts", output))

    assert code == 1
    assert "result: blocked" in (output / cli.REPORT_NAME).read_text()
    assert not (tmp_path / "artifacts").exists()


def _input(tmp_path: Path) -> Path:
    events = (
        _event("event-news-1", "fixture", "news", minutes_ago=10, digest="a" * 64),
        _event("event-filing-1", "fixture", "filing", minutes_ago=5, digest="b" * 64),
    )
    extractions = tuple(_extraction(event, stance=ClaimStance.REPORTS) for event in events)
    payload = {
        "as_of": AS_OF.isoformat(),
        "baseline_window_seconds": 21_600,
        "burst_threshold_bps": 20_000,
        "current_window_seconds": 3_600,
        "events": [item.model_dump(mode="json") for item in events],
        "extractions": [item.model_dump(mode="json") for item in extractions],
        "schema_version": 1,
    }
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    return path


def _arguments(input_path: Path, artifact_root: Path, output: Path) -> tuple[str, ...]:
    return (
        "--input",
        str(input_path),
        "--artifact-root",
        str(artifact_root),
        "--output-dir",
        str(output),
    )
