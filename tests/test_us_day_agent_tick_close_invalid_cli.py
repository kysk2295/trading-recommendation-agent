from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

import run_us_day_agent_tick as cli
from tests.day_agent_version_learning_support import champion
from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _markets
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.store import PaperStore
from trading_agent.us_day_agent_cli_bindings import ProductionManifest
from trading_agent.us_day_agent_service import CanonicalUsDaySource
from trading_agent.us_day_thesis_store import UsDayThesisStore

ROOT = Path(__file__).parents[1]


@dataclass(frozen=True, slots=True)
class _UnreachablePaperBindings:
    marker: str = "unreachable"


def test_invalid_production_close_config_fails_before_paper_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: otherwise-valid local CLI inputs and a private production manifest with an invalid loop bundle.
    source = CanonicalUsDaySource(situation=_project(_inputs()), current_markets=_markets())
    source_path = tmp_path / "source.json"
    assert publish_private_immutable_text(source_path, source.model_dump_json())
    version_store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    with version_store.writer() as writer:
        assert writer.register_initial_champion(champion())
    day_responses = tmp_path / "day-responses.json"
    thesis_response = tmp_path / "thesis-response.json"
    assert publish_private_immutable_text(day_responses, "[]")
    assert publish_private_immutable_text(thesis_response, "{}")
    manifest = tmp_path / "production.json"
    assert publish_private_immutable_text(
        manifest,
        ProductionManifest(
            repository=ROOT,
            review_ledger=tmp_path / "review.sqlite3",
            experiment_ledger=tmp_path / "experiment.sqlite3",
            strategy_manifest=tmp_path / "strategy.json",
            lane_registry=tmp_path / "lane.sqlite3",
            arm_database=tmp_path / "arms.sqlite3",
            arm_signing_key=tmp_path / "arm.key",
            execution_database=tmp_path / "execution.sqlite3",
            delivery_database=tmp_path / "delivery.sqlite3",
            session_root=tmp_path / "sessions",
            safety_arm_request_id="a" * 64,
            generated_artifact_root=tmp_path / "generated",
            forward_shadow_artifact_root=tmp_path / "forward-shadow",
            loop_task_root=tmp_path / "loop-tasks",
            loop_inputs=tmp_path / "missing-loop-inputs.json",
            patch_model_response=tmp_path / "missing-patch-response.json",
        ).model_dump_json(),
    )
    paper_binding_called = False

    def fail_if_paper_binding_called(
        production: ProductionManifest,
        canonical_source: CanonicalUsDaySource,
        outputs: Path,
        thesis_store: UsDayThesisStore,
        paper_store: PaperStore,
        evaluated_at: dt.datetime,
    ) -> _UnreachablePaperBindings:
        del production, canonical_source, outputs, thesis_store, paper_store, evaluated_at
        nonlocal paper_binding_called
        paper_binding_called = True
        return _UnreachablePaperBindings()

    monkeypatch.setattr(cli, "_paper_bindings", fail_if_paper_binding_called)
    command = [
        "--situation",
        str(source_path),
        "--outputs",
        str(tmp_path / "outputs"),
        "--version-store",
        str(version_store.path),
        "--production-manifest",
        str(manifest),
        "--day-model-responses",
        str(day_responses),
        "--thesis-model-response",
        str(thesis_response),
        "--now",
        EVALUATED_AT.isoformat(),
    ]

    # When: the production CLI is invoked.
    completed = CliRunner().invoke(cli._APP, command)

    # Then: close input parsing fails closed before Paper construction can mutate or finalize anything.
    assert completed.exit_code == 2
    assert json.loads(completed.stdout)["reason"] == "production_close_bindings_invalid"
    assert not paper_binding_called
