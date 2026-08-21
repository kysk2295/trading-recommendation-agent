from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _markets
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.us_day_agent_service import CanonicalUsDaySource

ROOT = Path(__file__).parents[1]


def test_help_has_no_broker_endpoint_option() -> None:
    # Given / When: the CLI help is requested without credentials or providers.
    completed = subprocess.run(
        [sys.executable, "run_us_day_agent_tick.py", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: help succeeds and exposes no broker URL authority.
    assert completed.returncode == 0
    assert "--broker" not in completed.stdout
    assert "--base-url" not in completed.stdout
    assert "--agent-executable" not in completed.stdout
    assert "--day-model-responses" in completed.stdout
    assert "--thesis-model-respo" in completed.stdout


def test_stale_situation_blocks_with_compact_json_before_runtime(tmp_path: Path) -> None:
    # Given: a canonical but stale local situation fixture.
    fixture = ROOT / "tests/fixtures/day-agent/stale-situation.json"
    # When: the scheduler CLI validates it.
    completed = subprocess.run(
        [
            sys.executable,
            "run_us_day_agent_tick.py",
            "--situation",
            str(fixture),
            "--outputs",
            str(tmp_path),
            "--now",
            "2026-08-21T15:00:00Z",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: output is stable, redacted, and nonzero.
    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "phase": "regular",
        "reason": "situation_stale",
        "status": "blocked",
    }
    assert completed.stderr == ""


def test_fresh_canonical_source_requires_only_explicit_model_response_bindings(tmp_path: Path) -> None:
    # Given: a fresh canonical local source with no model response bindings.
    situation = tmp_path / "situation.json"
    assert publish_private_immutable_text(
        situation,
        CanonicalUsDaySource(situation=_project(_inputs()), current_markets=_markets()).model_dump_json(),
    )

    # When: the scheduler reaches its explicit model boundary.
    completed = subprocess.run(
        [
            sys.executable,
            "run_us_day_agent_tick.py",
            "--situation",
            str(situation),
            "--outputs",
            str(tmp_path / "outputs"),
            "--now",
            EVALUATED_AT.isoformat(),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: it blocks compactly without acquiring broker or arbitrary process authority.
    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "phase": "regular",
        "reason": "model_bindings_required",
        "status": "blocked",
    }
    assert completed.stderr == ""
