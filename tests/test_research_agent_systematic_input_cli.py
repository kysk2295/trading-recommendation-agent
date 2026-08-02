from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import assert_never

import pytest

from tests.research_agent_systematic_input_fixtures import write_systematic_input_graph
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.research_agent_systematic_input_models import (
    BlockedSystematicInputActivation,
    ReadySystematicInputActivation,
)
from trading_agent.research_agent_systematic_input_store import load_systematic_input_activation

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_research_agent_systematic_input.py"


class InvalidGraph(StrEnum):
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    EXAMPLE = "example"
    TAMPERED = "tampered"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(SCRIPT), *arguments),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )


def _ready(root: Path, activation: Path) -> subprocess.CompletedProcess[str]:
    return _run("ready", "--artifact-root", str(root), "--activation", str(activation))


def test_help_exposes_only_bounded_activation_commands() -> None:
    # Given / When
    completed = _run("--help")

    # Then
    assert completed.returncode == 0
    assert all(command in completed.stdout for command in ("ready", "blocked", "status"))
    assert not any(option in completed.stdout for option in ("--input-csv", "--foundation", "--live-sessions"))


def test_bad_input_is_rejected_by_cli_parser() -> None:
    # Given / When
    completed = _run("ready")

    # Then
    assert completed.returncode == 2
    assert completed.stdout == ""


def test_ready_cli_writes_private_redacted_activation(tmp_path: Path) -> None:
    # Given
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    activation = tmp_path / "state" / "systematic-input.json"

    # When
    completed = _ready(graph.root, activation)

    # Then
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    stored = load_systematic_input_activation(activation)
    match stored:
        case ReadySystematicInputActivation() as ready:
            assert summary == {
                "bar_count": 384,
                "broker_mutation": 0,
                "foundation_sha256": ready.foundation_sha256,
                "input_sha256": ready.input_sha256,
                "selected_session_count": 1,
                "status": "ready",
            }
        case BlockedSystematicInputActivation():
            pytest.fail("ready command stored a blocked activation")
        case unreachable:
            assert_never(unreachable)
    assert stat.S_IMODE(activation.stat().st_mode) == 0o600
    assert str(tmp_path) not in completed.stdout


def test_ready_exact_replay_is_byte_identical(tmp_path: Path) -> None:
    # Given
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    activation = tmp_path / "state" / "systematic-input.json"
    first = _ready(graph.root, activation)
    first_payload = activation.read_bytes()

    # When
    replay = _ready(graph.root, activation)

    # Then
    assert first.returncode == replay.returncode == 0
    assert activation.read_bytes() == first_payload
    assert replay.stdout == first.stdout


@pytest.mark.parametrize("invalid_graph", list(InvalidGraph))
def test_bad_graph_preserves_pointer(tmp_path: Path, invalid_graph: InvalidGraph) -> None:
    # Given
    valid = write_systematic_input_graph(tmp_path / "valid")
    activation = tmp_path / "state" / "systematic-input.json"
    assert _ready(valid.root, activation).returncode == 0
    before = activation.read_bytes()
    candidate = tmp_path / "candidate"
    match invalid_graph:
        case InvalidGraph.MISSING:
            candidate.mkdir()
        case InvalidGraph.AMBIGUOUS:
            _ = write_systematic_input_graph(candidate / "one")
            _ = write_systematic_input_graph(candidate / "two")
        case InvalidGraph.EXAMPLE:
            candidate = write_systematic_input_graph(tmp_path / "examples" / "candidate").root
        case InvalidGraph.TAMPERED:
            graph = write_systematic_input_graph(candidate)
            graph.foundation_path.write_text("{}\n", encoding="utf-8")
        case unreachable:
            assert_never(unreachable)

    # When
    completed = _ready(candidate, activation)

    # Then
    assert completed.returncode != 0
    assert activation.read_bytes() == before
    assert str(tmp_path) not in completed.stdout + completed.stderr


def test_blocked_replaces_ready_with_bound_report_and_redacted_output(tmp_path: Path) -> None:
    # Given
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    activation = tmp_path / "state" / "systematic-input.json"
    report = tmp_path / "attempt.json"
    report_payload = '{"result":"no_connected_graph"}\n'
    assert publish_private_immutable_text(report, report_payload)
    assert _ready(graph.root, activation).returncode == 0

    # When
    completed = _run(
        "blocked",
        "--reason-code",
        "no_connected_graph",
        "--attempt-report",
        str(report),
        "--activation",
        str(activation),
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary == {
        "attempt_report_sha256": hashlib.sha256(report_payload.encode()).hexdigest(),
        "broker_mutation": 0,
        "reason_code": "no_connected_graph",
        "status": "blocked",
    }
    stored = load_systematic_input_activation(activation)
    assert stored.status == "blocked"
    serialized = activation.read_text(encoding="utf-8")
    assert not any(field in serialized for field in ("input_csv", "dataset_receipt", "foundation_path"))
    assert str(tmp_path) not in completed.stdout


@pytest.mark.parametrize("report_state", ["missing", "mode_invalid"])
def test_blocked_rejects_missing_or_mode_invalid_report(tmp_path: Path, report_state: str) -> None:
    # Given
    activation = tmp_path / "activation.json"
    report = tmp_path / "attempt.json"
    if report_state == "mode_invalid":
        report.write_text("attempt\n", encoding="utf-8")
        report.chmod(0o644)

    # When
    completed = _run(
        "blocked",
        "--reason-code",
        "report_unavailable",
        "--attempt-report",
        str(report),
        "--activation",
        str(activation),
    )

    # Then
    assert completed.returncode != 0
    assert not activation.exists()
    assert str(tmp_path) not in completed.stdout + completed.stderr


def test_status_revalidates_ready_activation_and_reports_redacted_summary(tmp_path: Path) -> None:
    # Given
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    activation = tmp_path / "state" / "systematic-input.json"
    ready = _ready(graph.root, activation)

    # When
    status = _run("status", "--activation", str(activation))

    # Then
    assert status.returncode == 0
    assert status.stdout == ready.stdout
    assert str(tmp_path) not in status.stdout


def test_status_rejects_activation_when_bound_graph_was_tampered(tmp_path: Path) -> None:
    # Given
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    activation = tmp_path / "state" / "systematic-input.json"
    assert _ready(graph.root, activation).returncode == 0
    graph.input_csv_path.write_text("tampered\n", encoding="utf-8")

    # When
    completed = _run("status", "--activation", str(activation))

    # Then
    assert completed.returncode != 0
    assert str(tmp_path) not in completed.stdout + completed.stderr
