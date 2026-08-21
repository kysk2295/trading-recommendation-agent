from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from tests.day_agent_version_learning_support import champion
from tests.test_day_agent_runtime import _thesis_call
from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _markets
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.us_day_agent_service import CanonicalUsDaySource
from trading_agent.us_day_thesis_models import situation_id_for

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


def test_local_no_trade_tick_uses_existing_champion_store_and_replays_after_restart(tmp_path: Path) -> None:
    # Given: existing canonical stores and explicit local model-response bindings.
    source = CanonicalUsDaySource(situation=_project(_inputs()), current_markets=_markets())
    source_path = tmp_path / "source.json"
    day_responses = tmp_path / "day-responses.json"
    thesis_response = tmp_path / "thesis-response.json"
    assert publish_private_immutable_text(source_path, source.model_dump_json())
    assert publish_private_immutable_text(day_responses, json.dumps([_thesis_call().model_dump(mode="json")]))
    version = champion()
    version_store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    with version_store.writer() as writer:
        assert writer.register_initial_champion(version)
    theme = source.situation.themes[0]
    response = {
        "decision": "no_trade",
        "situation_id": situation_id_for(source.situation),
        "agent_version_id": version.version_id,
        "playbook_id": "leader_breakout",
        "theme_id": theme.theme_id,
        "catalyst_event_id": theme.catalysts[0].event_id,
        "flow_inference_kind": None,
        "theme_name": "semiconductor_infrastructure",
        "symbol": None,
        "entry_price": None,
        "stop_price": None,
        "targets": [],
        "invalidation_rule": "현재 조건에서는 진입하지 않는다.",
        "confidence_bps": 3000,
        "observed_at": EVALUATED_AT.isoformat(),
        "valid_until": (EVALUATED_AT + dt.timedelta(seconds=20)).isoformat(),
        "reason_code": "setup_not_confirmed",
        "theme_rationale": None,
        "catalyst_rationale": None,
        "leader_rationale": None,
        "flow_rationale": None,
    }
    assert publish_private_immutable_text(thesis_response, json.dumps(response))
    command = [
        sys.executable,
        "run_us_day_agent_tick.py",
        "--situation",
        str(source_path),
        "--outputs",
        str(tmp_path / "outputs"),
        "--version-store",
        str(version_store.path),
        "--day-model-responses",
        str(day_responses),
        "--thesis-model-response",
        str(thesis_response),
        "--now",
        EVALUATED_AT.isoformat(),
    ]

    # When: two scheduler processes run the identical tick.
    first = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    replay = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: the local happy path succeeds and the durable receipt is exact.
    assert first.returncode == replay.returncode == 0, (first.stdout, replay.stdout)
    assert json.loads(first.stdout) == json.loads(replay.stdout)
    assert json.loads(first.stdout)["status"] == "accepted"
    assert version_store.reader().champion() == version


def test_local_model_failure_runs_real_task_runtime_without_changing_champion(tmp_path: Path) -> None:
    # Given: an existing Champion and an exhausted explicit model-response boundary.
    source = CanonicalUsDaySource(situation=_project(_inputs()), current_markets=_markets())
    source_path = tmp_path / "source.json"
    day_responses = tmp_path / "day-responses.json"
    thesis_response = tmp_path / "thesis-response.json"
    assert publish_private_immutable_text(source_path, source.model_dump_json())
    assert publish_private_immutable_text(day_responses, "[]")
    assert publish_private_immutable_text(thesis_response, "{}")
    version = champion()
    version_store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    with version_store.writer() as writer:
        assert writer.register_initial_champion(version)

    # When: the scheduler executes the real DayAgentRuntime model call.
    completed = subprocess.run(
        [
            sys.executable,
            "run_us_day_agent_tick.py",
            "--situation",
            str(source_path),
            "--outputs",
            str(tmp_path / "outputs"),
            "--version-store",
            str(version_store.path),
            "--day-model-responses",
            str(day_responses),
            "--thesis-model-response",
            str(thesis_response),
            "--now",
            EVALUATED_AT.isoformat(),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: failure is durable and the exact Champion remains unchanged.
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["reason"] == "day_agent_model_call_failed"
    assert version_store.reader().champion() == version
