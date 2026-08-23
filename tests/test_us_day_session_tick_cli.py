from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.test_day_agent_runtime import _thesis_call
from tests.test_run_us_day_source_projection import _fixture_arguments
from tests.test_us_day_agent_tick_cli import _strategy_runtime
from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _markets
from tests.us_day_agent_tick_close_support import CLOSE_AT
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.us_day_source_models import CanonicalUsDaySource
from trading_agent.us_day_thesis_models import situation_id_for

ROOT = Path(__file__).parents[1]


def test_help_exposes_one_session_composition_surface() -> None:
    # Given / When: an operator asks the production composition CLI for help.
    completed = subprocess.run(
        [sys.executable, "run_us_day_session_tick.py", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the scheduler-facing evidence and authority bindings are explicit.
    assert completed.returncode == 0
    assert "--scanner" in completed.stdout
    assert "--completed-tick" in completed.stdout
    assert "--production-manifest" in completed.stdout
    assert "--broker" not in completed.stdout
    assert "--base-url" not in completed.stdout


def test_current_session_projects_and_replays_one_no_trade_tick(tmp_path: Path) -> None:
    # Given: current, immutable provider evidence and explicit reviewed local bindings.
    command, outputs, version_store = _happy_command(tmp_path)

    # When: the same scheduler tick is restarted.
    first = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    replay = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: source and tick identities are stable and only one receipt is retained.
    assert first.returncode == replay.returncode == 0, (first.stdout, first.stderr)
    first_payload = json.loads(first.stdout)
    assert first_payload == json.loads(replay.stdout)
    assert first_payload["session_id"] == "XNYS-2026-08-20"
    assert first_payload["tick_status"] == "accepted"
    assert first_payload["mutation"] == "0"
    assert len(tuple((outputs / "us_day" / "session_sources").glob("us_day_source_*.json"))) == 1
    assert len(tuple((outputs / "us_day" / "session_tick_receipts").glob("*.json"))) == 1
    assert DayAgentVersionStore(version_store).reader().champion() is not None
    assert not (outputs / "execution.sqlite3").exists()
    assert _paper_mutation_count(outputs / "us_day" / "paper.sqlite3") == 0


def test_missing_quote_blocks_before_tick_or_paper_state(tmp_path: Path) -> None:
    # Given: a scheduler invocation with one required quote artifact omitted.
    command, outputs, _ = _happy_command(tmp_path)
    quote_index = command.index("--quote")
    del command[quote_index : quote_index + 2]

    # When: the composition CLI validates the source set.
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: it blocks at projection and creates no Day Agent or Paper database.
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "blocked"
    assert payload["reason"] == "source_projection_blocked"
    assert payload["mutation"] == "0"
    assert not (outputs / "us_day" / "day_agent.sqlite3").exists()
    assert not (outputs / "us_day" / "paper.sqlite3").exists()


def test_missing_champion_blocks_without_production_manifest_or_paper_mutation(
    tmp_path: Path,
) -> None:
    # Given: valid evidence and strategy/model fixtures but no Champion store.
    command, outputs, version_store = _happy_command(tmp_path)
    version_store.unlink()

    # When: the composed tick reaches the existing authority boundary.
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: it fails closed without ProductionManifest or Paper activity.
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["tick_status"] == "blocked"
    assert payload["reason"] == "existing_champion_store_required"
    assert payload["mutation"] == "0"
    assert not (outputs / "us_day" / "paper.sqlite3").exists()


def test_closed_session_rejects_current_named_evidence_before_tick(tmp_path: Path) -> None:
    # Given: otherwise-valid evidence evaluated after the XNYS regular session.
    command, outputs, _ = _happy_command(tmp_path)
    now_index = command.index("--now") + 1
    command[now_index] = "2026-08-20T21:30:00Z"

    # When: the production composition is invoked.
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: session projection blocks and no tick store is opened.
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["reason"] == "source_projection_blocked"
    assert payload["mutation"] == "0"
    assert not (outputs / "us_day" / "day_agent.sqlite3").exists()


def test_stale_current_session_evidence_blocks_before_tick(tmp_path: Path) -> None:
    # Given: regular-session evidence whose five-second quote window has expired.
    command, outputs, _ = _happy_command(tmp_path)
    now_index = command.index("--now") + 1
    command[now_index] = (EVALUATED_AT + dt.timedelta(seconds=10)).isoformat()

    # When: the production composition validates the current evidence set.
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: stale projection is rejected before Day Agent state exists.
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["reason"] == "source_projection_blocked"
    assert not (outputs / "us_day" / "day_agent.sqlite3").exists()


def test_invalid_capsule_manifest_blocks_before_paper_mutation(tmp_path: Path) -> None:
    # Given: current evidence and Champion state bound to an invalid capsule manifest.
    command, outputs, _ = _happy_command(tmp_path)
    invalid = tmp_path / "invalid-strategy.json"
    assert publish_private_immutable_text(invalid, "{}")
    manifest_index = command.index("--strategy-manifest") + 1
    command[manifest_index] = str(invalid)

    # When: the existing reviewed-strategy authority boundary is reached.
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: the capsule fails closed with no recommendation, event, or alert mutation.
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["reason"] == "champion_strategy_lineage_invalid"
    assert payload["mutation"] == "0"
    assert _paper_mutation_count(outputs / "us_day" / "paper.sqlite3") == 0


def test_post_close_reuses_latest_current_session_source_before_close_authority(
    tmp_path: Path,
) -> None:
    # Given: one immutable current-session source produced at the close boundary.
    command, outputs, _ = _happy_command(tmp_path)
    situation = _project(_inputs()).model_copy(update={"evaluated_at": CLOSE_AT})
    source = CanonicalUsDaySource(situation=situation, current_markets=_markets())
    source_root = outputs / "us_day" / "session_sources"
    source_name = f"us_day_source_{situation_id_for(situation)}.json"
    assert publish_private_immutable_text(source_root / source_name, source.model_dump_json())
    now_index = command.index("--now") + 1
    command[now_index] = CLOSE_AT.isoformat()

    # When: closed-market evidence projection fails and the same composition continues.
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: the latest source reaches post-close and absent ProductionManifest remains fail-closed.
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["stage"] == "tick"
    assert payload["tick_phase"] == "post_close"
    assert payload["reason"] == "paper_bindings_missing"
    assert payload["source"] == source_name
    assert payload["mutation"] == "0"


def _happy_command(tmp_path: Path) -> tuple[list[str], Path, Path]:
    projection = _fixture_arguments(tmp_path / "evidence")
    del projection[-4:]
    outputs = tmp_path / "outputs"
    version, playbook, strategy_manifest, experiment_ledger = _strategy_runtime(tmp_path / "strategy")
    version_store = tmp_path / "versions.sqlite3"
    with DayAgentVersionStore(version_store).writer() as writer:
        assert writer.register_initial_champion(version)
    day_responses = tmp_path / "day-responses.json"
    thesis_response = tmp_path / "thesis-response.json"
    assert publish_private_immutable_text(
        day_responses,
        json.dumps([_thesis_call().model_dump(mode="json")]),
    )
    source = _project(_inputs())
    theme = source.themes[0]
    assert publish_private_immutable_text(
        thesis_response,
        json.dumps(
            {
                "decision": "no_trade",
                "situation_id": situation_id_for(source),
                "agent_version_id": version.version_id,
                "playbook_id": playbook.playbook_id,
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
        ),
    )
    return (
        [
            sys.executable,
            "run_us_day_session_tick.py",
            *projection,
            "--outputs",
            str(outputs),
            "--version-store",
            str(version_store),
            "--strategy-manifest",
            str(strategy_manifest),
            "--experiment-ledger",
            str(experiment_ledger),
            "--day-model-responses",
            str(day_responses),
            "--thesis-model-response",
            str(thesis_response),
            "--now",
            EVALUATED_AT.isoformat(),
        ],
        outputs,
        version_store,
    )


def _paper_mutation_count(path: Path) -> int:
    if not path.exists():
        return 0
    with sqlite3.connect(path) as connection:
        return sum(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("recommendations", "events", "bar_checkpoints", "alert_outbox")
        )
