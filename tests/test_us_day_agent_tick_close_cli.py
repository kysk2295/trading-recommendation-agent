from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from typer.testing import CliRunner

import run_us_day_agent_tick as cli
from tests.day_strategy_capsule_support import bar, proposal
from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _markets, _valid_response
from tests.us_day_agent_tick_close_support import CLOSE_AT, publish_finalized_paper
from tests.us_forward_shadow_support import prepared_runtime, signal_source
from trading_agent.day_agent_challenger_publisher import DayAgentFutureShadowSession
from trading_agent.day_agent_change_patches import AgentChangeKind, MarketRegimePatch, MarketRegimeRule
from trading_agent.day_agent_loop_engineer import ProposedAgentChange
from trading_agent.day_agent_version_models import (
    AgentDeploymentState,
    AgentModelRoleBinding,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.us_day_agent_cli_bindings import LoopInputBundle, ProductionManifest
from trading_agent.us_day_agent_service import CanonicalUsDaySource
from trading_agent.us_day_thesis_models import UsDayChampion, UsDayPlaybook, situation_id_for
from trading_agent.us_day_thesis_runtime import reason_trade_thesis
from trading_agent.us_day_thesis_store import UsDayThesisStore

ROOT = Path(__file__).parents[1]


class _FinalizedPaperControl:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def recover_and_reconcile(self, evaluated_at: dt.datetime) -> None:
        del evaluated_at
        self.calls.append("recover")

    def finalize(self, evaluated_at: dt.datetime) -> str:
        del evaluated_at
        self.calls.append("finalize")
        return "finalized"


@dataclass(frozen=True, slots=True)
class _PaperBindings:
    session_control: _FinalizedPaperControl


def test_production_cli_composition_closes_learns_and_replays_from_private_stores(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Given: canonical finalized stores, a Champion capsule, and strict private loop/model-output bundles.
    outputs = tmp_path / "outputs"
    (outputs / "us_day").mkdir(parents=True, mode=0o700)
    forward, parent_capsule = prepared_runtime(tmp_path / "forward", source=signal_source())
    champion = build_agent_version(
        model_role_bindings=(AgentModelRoleBinding(role="reasoning", model_id="reasoner-v1"),),
        prompt_sha256="1" * 64,
        tool_policy_sha256="2" * 64,
        memory_retrieval_policy_sha256="3" * 64,
        playbook_ids=(parent_capsule.capsule_id,),
        parent_version_id=None,
        creation_evidence_ids=("a" * 64,),
        deployment_state=AgentDeploymentState.CHAMPION,
        task_id="task-20260820-NVDA",
        created_at=EVALUATED_AT - dt.timedelta(minutes=6),
        created_session_date=EVALUATED_AT.date(),
    )
    versions = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    with versions.writer() as writer:
        assert writer.register_initial_champion(champion)
    thesis_situation = _project(_inputs())
    source_situation = thesis_situation.model_copy(update={"evaluated_at": CLOSE_AT})
    playbook = UsDayPlaybook(playbook_id="leader_breakout", title="대장주 돌파", entry_type="stop_trigger")
    lane = StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id=playbook.playbook_id,
    )
    thesis = reason_trade_thesis(
        _valid_response()
        | {
            "agent_version_id": champion.version_id,
            "situation_id": situation_id_for(thesis_situation),
        },
        UsDayChampion(
            version_id=champion.version_id,
            strategy_version=playbook.playbook_id,
            strategy_lane=lane,
            deployed=True,
            playbooks=(playbook,),
        ),
        thesis_situation,
        _markets(),
    ).thesis
    thesis_store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert thesis_store.publish_thesis(thesis)
    assert publish_private_immutable_text(
        outputs / "us_day" / "situations" / f"{thesis.situation_id}.json",
        thesis_situation.model_dump_json(),
    )
    publish_finalized_paper(outputs, champion.version_id)
    source_path = tmp_path / "source.json"
    assert publish_private_immutable_text(
        source_path,
        CanonicalUsDaySource(situation=source_situation, current_markets=_markets()).model_dump_json(),
    )
    loop_inputs = tmp_path / "loop-inputs.json"
    future = tuple(
        DayAgentFutureShadowSession(
            session_date=session_date,
            calendar_snapshot_id="calendar://official/XNYS/2026-v1",
            effective_at=dt.datetime.combine(session_date, dt.time(13, 30), tzinfo=dt.UTC),
        )
        for session_date in (dt.date(2026, 8, 21), dt.date(2026, 8, 24))
    )
    assert publish_private_immutable_text(
        loop_inputs,
        LoopInputBundle(
            runtime=forward.generated_artifacts.runtime,
            proposal_template=proposal(signal_source()),
            replay_bars=(bar(),),
            future_sessions=future,
        ).model_dump_json(),
    )
    patch_response = tmp_path / "patch-response.json"
    assert publish_private_immutable_text(
        patch_response,
        ProposedAgentChange(
            patch=MarketRegimePatch(
                kind=AgentChangeKind.MARKET_REGIME_POLICY,
                rule=MarketRegimeRule.TREND_ALIGNMENT,
                confirmation_bars=2,
            )
        ).model_dump_json(),
    )
    manifest = _manifest(tmp_path, forward, loop_inputs, patch_response)
    day_responses = tmp_path / "day-responses.json"
    thesis_response = tmp_path / "thesis-response.json"
    assert publish_private_immutable_text(day_responses, "[]")
    assert publish_private_immutable_text(thesis_response, "{}")
    control = _FinalizedPaperControl()
    monkeypatch.setattr(cli, "_paper_bindings", lambda *args, **kwargs: _PaperBindings(control))

    command = [
        "--situation",
        str(source_path),
        "--outputs",
        str(outputs),
        "--version-store",
        str(versions.path),
        "--production-manifest",
        str(manifest),
        "--day-model-responses",
        str(day_responses),
        "--thesis-model-response",
        str(thesis_response),
        "--now",
        CLOSE_AT.isoformat(),
    ]

    # When: post-close runs once and an independently reconstructed CLI invocation replays the same tick.
    first_run = CliRunner().invoke(cli._APP, command)
    replay_run = CliRunner().invoke(cli._APP, command)
    first = json.loads(first_run.stdout)
    replay = json.loads(replay_run.stdout)

    # Then: exact report/challenger IDs are durable and replay causes no second finalize or proposal.
    assert first_run.exit_code == replay_run.exit_code == 0, (first_run.stdout, replay_run.stdout)
    assert first == replay
    report_id = first["market_close_report_id"]
    challenger_id = first["challenger_version_id"]
    assert versions.reader().challenger(challenger_id) is not None
    assert versions.reader().proposals(challenger_id)[0].version_id == challenger_id
    assert tuple((outputs / "us_day" / "close_reports").glob(f"*{report_id}*.json"))
    assert control.calls == ["recover", "finalize"]


def _manifest(tmp_path: Path, forward, loop_inputs: Path, patch_response: Path) -> Path:
    path = tmp_path / "production.json"
    assert publish_private_immutable_text(
        path,
        ProductionManifest(
            repository=ROOT,
            review_ledger=tmp_path / "review.sqlite3",
            experiment_ledger=forward.ledger.path,
            lane_registry=tmp_path / "paper-lane.sqlite3",
            arm_database=tmp_path / "arms.sqlite3",
            arm_signing_key=tmp_path / "arm.key",
            execution_database=tmp_path / "paper-execution.sqlite3",
            delivery_database=tmp_path / "delivery.sqlite3",
            session_root=tmp_path / "sessions",
            safety_arm_request_id="a" * 64,
            generated_artifact_root=forward.generated_artifacts.root,
            forward_shadow_artifact_root=forward.shadow_artifacts.root,
            loop_task_root=tmp_path / "loop-tasks",
            loop_inputs=loop_inputs,
            patch_model_response=patch_response,
        ).model_dump_json(),
    )
    return path
