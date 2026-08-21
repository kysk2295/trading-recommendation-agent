from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.test_us_day_agent_tick_cli import ROOT, _strategy_runtime
from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _markets
from trading_agent.day_agent_version_models import AgentDeploymentState, build_agent_version
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.us_day_agent_service import CanonicalUsDaySource


def test_strategy_manifest_mismatch_fails_before_task_thesis_or_paper(tmp_path: Path) -> None:
    # Given: a canonical US Day capsule manifest whose exact ID is absent from the Champion lineage.
    source = CanonicalUsDaySource(situation=_project(_inputs()), current_markets=_markets())
    source_path = tmp_path / "source.json"
    day_responses = tmp_path / "day-responses.json"
    thesis_response = tmp_path / "thesis-response.json"
    assert publish_private_immutable_text(source_path, source.model_dump_json())
    assert publish_private_immutable_text(day_responses, "[]")
    assert publish_private_immutable_text(thesis_response, "{}")
    valid, _, strategy_manifest, experiment_ledger = _strategy_runtime(tmp_path)
    mismatched = build_agent_version(
        model_role_bindings=valid.model_role_bindings,
        prompt_sha256=valid.prompt_sha256,
        tool_policy_sha256=valid.tool_policy_sha256,
        memory_retrieval_policy_sha256=valid.memory_retrieval_policy_sha256,
        playbook_ids=("4" * 64,),
        parent_version_id=None,
        creation_evidence_ids=valid.payload.creation_evidence_ids,
        deployment_state=AgentDeploymentState.CHAMPION,
        task_id=valid.task_id,
        created_at=valid.created_at,
        created_session_date=valid.created_session_date,
    )
    version_store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    with version_store.writer() as writer:
        assert writer.register_initial_champion(mismatched)

    # When: the CLI resolves the reviewed strategy before starting any runtime work.
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
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: lineage fails closed and no task, thesis, or Paper store is created.
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["reason"] == "champion_strategy_lineage_invalid"
    assert not (tmp_path / "outputs" / "us_day" / "day_agent.sqlite3").exists()
    assert not tuple((tmp_path / "outputs" / "us_day" / "theses").glob("[!.]*.json"))
    assert not (tmp_path / "outputs" / "us_day" / "paper.sqlite3").exists()
