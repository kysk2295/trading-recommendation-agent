from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

import pytest
from typer.testing import CliRunner

import run_us_day_agent_tick as cli
import trading_agent.us_day_agent_service as service_module
from tests.day_strategy_capsule_support import bar, proposal
from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _markets, _valid_response
from tests.us_day_agent_tick_close_support import CLOSE_AT, publish_close_manifest, publish_finalized_paper
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
from trading_agent.us_day_agent_cli_bindings import LoopInputBundle
from trading_agent.us_day_agent_service import CanonicalUsDaySource
from trading_agent.us_day_post_close_checkpoint import UsDayPostCloseCheckpointStore
from trading_agent.us_day_thesis_models import UsDayChampion, UsDayPlaybook, situation_id_for
from trading_agent.us_day_thesis_runtime import reason_trade_thesis
from trading_agent.us_day_thesis_store import UsDayThesisStore

ROOT = Path(__file__).parents[1]
type FailureStage = Literal["after_finalize", "after_report", "after_loop"]


class _InjectedPostCloseCrash(RuntimeError):
    pass


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


@pytest.mark.parametrize("failure_stage", ("after_finalize", "after_report", "after_loop"))
def test_production_cli_composition_closes_learns_and_replays_from_private_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: FailureStage,
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
    playbook = UsDayPlaybook(
        playbook_id=parent_capsule.capsule_id,
        title="대장주 돌파",
        entry_type="stop_trigger",
    )
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
            "playbook_id": playbook.playbook_id,
        },
        UsDayChampion(
            version_id=champion.version_id,
            strategy_version=parent_capsule.hypothesis_version_id,
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
    manifest = publish_close_manifest(
        tmp_path,
        ROOT,
        forward,
        parent_capsule,
        lane,
        playbook,
        loop_inputs,
        patch_response,
    )
    day_responses = tmp_path / "day-responses.json"
    thesis_response = tmp_path / "thesis-response.json"
    assert publish_private_immutable_text(day_responses, "[]")
    assert publish_private_immutable_text(thesis_response, "{}")
    control = _FinalizedPaperControl()
    monkeypatch.setattr(cli, "_paper_bindings", lambda *args, **kwargs: _PaperBindings(control))
    payload_reads = 0
    original_read = service_module.StoreBackedUsDayClosePayloadReader.read

    def count_payload_reads(reader, request, agent_version):
        nonlocal payload_reads
        payload_reads += 1
        return original_read(reader, request, agent_version)

    monkeypatch.setattr(service_module.StoreBackedUsDayClosePayloadReader, "read", count_payload_reads)
    failed = False
    match failure_stage:
        case "after_finalize":
            original_paper = UsDayPostCloseCheckpointStore.publish_paper

            def fail_once_after_finalize(store, checkpoint):
                nonlocal failed
                original_paper(store, checkpoint)
                if not failed:
                    failed = True
                    raise _InjectedPostCloseCrash("injected_after_finalize")

            monkeypatch.setattr(UsDayPostCloseCheckpointStore, "publish_paper", fail_once_after_finalize)
        case "after_report":
            original_publish = service_module.publish_market_close_report

            def fail_once_after_report_publish(root, report):
                nonlocal failed
                created = original_publish(root, report)
                if not failed:
                    failed = True
                    raise _InjectedPostCloseCrash("injected_after_report_publish")
                return created

            monkeypatch.setattr(service_module, "publish_market_close_report", fail_once_after_report_publish)
        case "after_loop":
            original_loop = service_module.run_loop_engineer

            def fail_once_after_loop_persistence(report, version, services):
                nonlocal failed
                proposal_record = original_loop(report, version, services)
                if not failed:
                    failed = True
                    raise _InjectedPostCloseCrash("injected_after_loop_persistence")
                return proposal_record

            monkeypatch.setattr(service_module, "run_loop_engineer", fail_once_after_loop_persistence)
        case unreachable:
            assert_never(unreachable)

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
    interrupted_run = CliRunner().invoke(cli._APP, command)
    replay_run = CliRunner().invoke(cli._APP, command)
    exact_replay_run = CliRunner().invoke(cli._APP, command)
    replay = json.loads(replay_run.stdout)

    # Then: exact report/challenger IDs are durable and replay causes no second finalize or proposal.
    assert interrupted_run.exit_code == 1
    assert replay_run.exit_code == 0, replay_run.stdout
    assert exact_replay_run.exit_code == 0, exact_replay_run.stdout
    assert json.loads(exact_replay_run.stdout) == replay
    report_id = replay["market_close_report_id"]
    challenger_id = replay["challenger_version_id"]
    challenger = versions.reader().challenger(challenger_id)
    assert challenger is not None
    proposals = versions.reader().proposals(challenger_id)
    assert len(proposals) == 1
    assert proposals[0].version_id == challenger_id
    capsules = forward.ledger.reader().day_strategy_capsules(MarketId.US_EQUITIES)
    assert len(capsules) == 2
    assert len(tuple(item for item in capsules if item.capsule.capsule_id in challenger.playbook_ids)) == 1
    assert tuple((outputs / "us_day" / "close_reports").glob(f"*{report_id}*.json"))
    assert len(tuple((outputs / "us_day" / "close_reports").glob("*.json"))) == 1
    assert control.calls == ["recover", "finalize", "recover"]
    assert payload_reads == 1
