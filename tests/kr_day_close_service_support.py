from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from tests.day_strategy_capsule_support import PUBLISHED_AT, bar, builtin_request, proposal
from tests.kr_day_shadow_support import run_authorized_kr_shadow_tick
from tests.strategy_research_contract_fixtures import hypothesis
from tests.test_day_research_attempt_binding import _attempt, _binding, _family, _manifest, _version
from tests.test_kis_kr_session_calendar import _payload as calendar_payload
from tests.test_kis_kr_session_calendar import _row as calendar_row
from tests.test_kr_day_capsule_shadow import _advance, _entry_evaluation
from tests.us_forward_shadow_support import signal_source
from trading_agent.day_agent_challenger_publisher import DayAgentFutureShadowSession
from trading_agent.day_agent_change_patches import AgentChangeKind, ExitPolicyPatch, ExitRule
from trading_agent.day_agent_loop_engineer import ProposedAgentChange
from trading_agent.day_agent_version_models import (
    AgentDeploymentState,
    AgentModelRoleBinding,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_forward_trial_identity import ForwardExecutionLane
from trading_agent.day_forward_trial_models import DayForwardTrial
from trading_agent.day_strategy_capsule import (
    GeneratedCapsuleVerification,
    generated_evaluator_bundle_sha256,
    generated_protocol_bundle_sha256,
    publish_day_strategy_capsule,
)
from trading_agent.day_strategy_capsule_models import CapsuleArtifactKind, CapsuleAuthorityCeiling
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json, research_source_key
from trading_agent.experiment_ledger_models import (
    HypothesisRegistration,
    ResearchHypothesisCard,
    ResearchSource,
    ResearchSourceKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.experiment_scope_models import ExperimentScope, ExperimentScopeKind
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import (
    GeneratedStrategyRuntimeIdentity,
    resolve_generated_strategy_runtime,
)
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import KisKrSessionCalendarReceipt, KrSessionCalendarSnapshot
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation, KrDayCapsuleEvaluationPayload
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_close_service_config import KrDayCloseServiceConfig
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.kr_day_loop_inputs import (
    KrDayLoopInputBundle,
    build_kr_day_loop_research_binding,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_identity_models import LaneId
from trading_agent.models import BarInput
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import CandidateStrategyDraft, LlmCallReceipt, ProposedHypothesis
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_types import AttemptStatus
from trading_agent.us_day_agent_cli_bindings import LoopInputBundle

ROOT = Path(__file__).resolve().parents[1]
KST = dt.timezone(dt.timedelta(hours=9))
SESSION_DATE = dt.date(2026, 8, 24)


@dataclass(frozen=True, slots=True)
class CloseFixture:
    config: KrDayCloseServiceConfig
    config_path: Path
    pre_close: dt.datetime
    post_close: dt.datetime


def close_fixture(
    root: Path,
    *,
    open_day: bool = True,
    terminal: bool = True,
    calendar_base_date: dt.date = SESSION_DATE,
    shadow_snapshot_id: str | None = None,
    configured_loop: bool = False,
    loop_calendar_snapshot_id: str | None = None,
    us_loop_inputs: bool = False,
) -> CloseFixture:
    state = root / "state"
    state.mkdir(mode=0o700, parents=True)
    os.chmod(state, 0o700)
    config = KrDayCloseServiceConfig(
        project_root=ROOT,
        expected_commit=_head(),
        executable_path=Path(shutil.which("uv") or "/bin/false").resolve(),
        state_root=state,
        calendar_store=state / "calendar/calendar.sqlite3",
        experiment_ledger=state / "ledger/experiment.sqlite3",
        report_root=state / "reports",
        policy_root=state / "policies",
        hermes_delivery_database=state / "hermes/delivery.sqlite3",
        health_root=state / "health",
        completion_root=state / "completion",
        launch_agents_directory=root / "LaunchAgents",
    )
    snapshot = _seed_calendar(
        config.calendar_store,
        open_day=open_day,
        base_date=calendar_base_date,
    )
    if open_day:
        _seed_session(
            config,
            snapshot.snapshot_id,
            terminal=terminal,
            shadow_snapshot_id=shadow_snapshot_id,
            configured_loop=configured_loop,
            loop_calendar_snapshot_id=loop_calendar_snapshot_id,
            us_loop_inputs=us_loop_inputs,
        )
    return CloseFixture(
        config=config,
        config_path=root / f"kr-day-close-{config.expected_commit}.json",
        pre_close=dt.datetime(2026, 8, 24, 15, 29, tzinfo=KST),
        post_close=dt.datetime(2026, 8, 24, 15, 40, tzinfo=KST),
    )


def _seed_calendar(
    path: Path,
    *,
    open_day: bool,
    base_date: dt.date,
) -> KrSessionCalendarSnapshot:
    flag = "Y" if open_day else "N"
    leading = (
        ()
        if base_date == SESSION_DATE
        else (calendar_row(base_date.strftime("%Y%m%d"), "Y", "Y", "Y", "Y"),)
    )
    receipt = KisKrSessionCalendarReceipt(
        base_date=base_date,
        received_at=dt.datetime.combine(base_date, dt.time(8), tzinfo=KST),
        status_code=200,
        content_type="application/json",
        raw_payload=calendar_payload(
            rows=(
                *leading,
                calendar_row("20260824", flag, flag, flag, flag),
                calendar_row("20260825", "N", "N", "N", "N"),
                calendar_row("20260826", "Y", "Y", "Y", "Y"),
            )
        ),
    )
    snapshot = project_kis_kr_session_calendar(receipt)
    assert KisKrSessionCalendarStore(path).append(receipt, snapshot)
    return snapshot


def _seed_session(
    config: KrDayCloseServiceConfig,
    snapshot_id: str,
    *,
    terminal: bool,
    shadow_snapshot_id: str | None,
    configured_loop: bool,
    loop_calendar_snapshot_id: str | None,
    us_loop_inputs: bool,
) -> None:
    ledger = ExperimentLedgerStore(config.experiment_ledger)
    family = _family()
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    generated = GeneratedStrategyArtifactStore(config.state_root / "generated-strategies", runtime)
    loop_proposal = (
        proposal(signal_source())
        if us_loop_inputs
        else kr_proposal(kr_signal_source())
    )
    loop_bars = (bar(),) if us_loop_inputs else (kr_bar(),)
    published = generated.publish(loop_proposal) if configured_loop else None
    source_sha = "a" * 64 if published is None else published.artifact.payload.source_sha256
    base_version = _version(family, market_id=MarketId.KR_EQUITIES, code_sha256=source_sha)
    version_payload = base_version.model_dump(mode="python") | {
        "hypothesis_version_id": "",
        "protocol_sha256": generated_protocol_bundle_sha256() if configured_loop else base_version.protocol_sha256,
    }
    version = base_version.model_validate(
        version_payload | {"hypothesis_version_id": base_version.canonical_id_for(version_payload)}
    )
    registered_fixture = hypothesis()
    registered = registered_fixture.model_copy(
        update={
            "hypothesis_id": (
                "KR-DAY-005930-PARENT-001" if configured_loop else registered_fixture.hypothesis_id
            ),
            "code_sha256": source_sha,
        }
    )
    manifest = PreregistrationManifest.from_hypothesis(registered, preregistered_at=registered.created_at)
    attempt = _attempt(0, AttemptStatus.SUCCEEDED).model_copy(
        update={
            "hypothesis_id": registered.hypothesis_id,
            "code_sha256": source_sha,
            "artifact_refs": (f"artifact://safe/{source_sha}",),
        }
    )
    binding = _binding(attempt, version, artifact_ref=f"artifact://safe/{source_sha}")
    with ledger.writer() as writer:
        assert writer.register_strategy_research(manifest if configured_loop else _manifest())
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(version)
        assert writer.append_strategy_research_attempt(attempt)
        assert writer.register_day_research_attempt_binding(binding)
    request = replace(
        builtin_request(market_id=MarketId.KR_EQUITIES),
        hypothesis_version_id=version.hypothesis_version_id,
        attempt_binding_id=binding.binding_id,
        artifact_ref=binding.artifact_ref,
        artifact_sha256=source_sha,
        evaluation_cadence=version.evaluation_cadence,
        entry_rule=version.entry_rule,
        exit_rule=version.exit_rule,
        stop_rule=version.stop_rule,
        cost_model=version.cost_model,
        protocol_sha256=version.protocol_sha256,
        published_at=binding.bound_at + dt.timedelta(minutes=1),
    )
    if configured_loop:
        limits = request.resource_limits
        request = replace(
            request,
            artifact_kind=CapsuleArtifactKind.GENERATED_PYTHON,
            generated_artifact_id=published.artifact.artifact_id if published is not None else None,
            evaluator_sha256=generated_evaluator_bundle_sha256(),
            authority_ceiling=CapsuleAuthorityCeiling.RESEARCH_ONLY,
            generated_verification=GeneratedCapsuleVerification(
                generated,
                GeneratedStrategySandbox(runtime, config.state_root / "parent-preflight", limits.to_generated_limits()),
                loop_bars,
            ),
        )
    capsule, _ = publish_day_strategy_capsule(ledger, request)
    if configured_loop:
        _seed_loop_authority(
            config,
            capsule.capsule_id,
            snapshot_id,
            runtime,
            foreign_future_snapshot_id=loop_calendar_snapshot_id,
            us_loop_inputs=us_loop_inputs,
        )
    eligible = dt.datetime(2026, 8, 24, 10, 1, tzinfo=KST).astimezone(dt.UTC)
    trial_payload = {
        "schema_version": 1,
        "trial_id": "",
        "capsule_id": capsule.capsule_id,
        "hypothesis_version_id": capsule.hypothesis_version_id,
        "market_id": MarketId.KR_EQUITIES,
        "execution_lane": ForwardExecutionLane.FORWARD_PROBE,
        "session_id": "XKRX-2026-08-24",
        "session_date": SESSION_DATE,
        "calendar_snapshot_id": f"calendar://official/XKRX/{snapshot_id}",
        "cost_model_sha256": _sha(canonical_experiment_ledger_json(capsule.cost_model)),
        "source_refs_sha256": _sha(json.dumps(version.source_refs, separators=(",", ":"))),
        "evidence_schema_sha256": _sha(json.dumps(capsule.evidence_schema, separators=(",", ":"))),
        "preregistered_at": eligible - dt.timedelta(seconds=30),
        "registration_completed_bar_at": eligible - dt.timedelta(minutes=1),
        "first_eligible_completed_bar_at": eligible,
        "trading_authority": False,
        "profitability_claim": False,
    }
    trial = DayForwardTrial.model_validate(
        trial_payload | {"trial_id": DayForwardTrial.canonical_id_for(trial_payload)}
    )
    with ledger.writer() as writer:
        assert writer.register_day_forward_trial(trial)
    event_snapshot_id = snapshot_id if shadow_snapshot_id is None else shadow_snapshot_id
    entry = _authorized_evaluation(
        capsule.capsule_id,
        capsule.hypothesis_version_id,
        event_snapshot_id,
    )
    shadow = KrDayCapsuleShadowStore(config.shadow_store)
    _ = run_authorized_kr_shadow_tick(shadow, (entry,))
    if terminal:
        _ = run_authorized_kr_shadow_tick(
            shadow,
            (
                _bind_evaluation(
                    _advance(entry, low=Decimal("9900"), high=Decimal("10400")),
                    capsule.capsule_id,
                    capsule.hypothesis_version_id,
                    event_snapshot_id,
                ),
            ),
        )
    generated = KrDayDecisionStore(
        config.shadow_store.with_name(f"{config.shadow_store.stem}-decisions.sqlite3")
    )
    destination = KrDayDecisionStore(config.decision_store)
    for event in generated.events():
        _ = destination.append(event)


def _seed_loop_authority(
    config: KrDayCloseServiceConfig,
    capsule_id: str,
    snapshot_id: str,
    runtime: GeneratedStrategyRuntimeIdentity,
    *,
    foreign_future_snapshot_id: str | None,
    us_loop_inputs: bool,
) -> None:
    champion = build_agent_version(
        model_role_bindings=(AgentModelRoleBinding(role="reasoning", model_id="reasoner-v1"),),
        prompt_sha256="1" * 64,
        tool_policy_sha256="2" * 64,
        memory_retrieval_policy_sha256="3" * 64,
        playbook_ids=(capsule_id,),
        parent_version_id=None,
        creation_evidence_ids=("a" * 64,),
        deployment_state=AgentDeploymentState.CHAMPION,
        task_id="task-20260824-KR-close",
        created_at=dt.datetime(2026, 8, 24, 6, 0, tzinfo=dt.UTC),
        created_session_date=SESSION_DATE,
    )
    with DayAgentVersionStore(config.state_root / "day-agent-versions.sqlite3").writer() as writer:
        assert writer.register_initial_champion(champion)
    future = tuple(
        DayAgentFutureShadowSession(
            session_date=session_date,
            calendar_snapshot_id=(
                f"calendar://official/XKRX/{foreign_future_snapshot_id}"
                if index == 1 and foreign_future_snapshot_id is not None
                else f"calendar://official/XKRX/{snapshot_id}"
            ),
            effective_at=dt.datetime.combine(session_date, dt.time(0), tzinfo=dt.UTC),
        )
        for index, session_date in enumerate((dt.date(2026, 8, 26), dt.date(2026, 8, 27)))
    )
    if us_loop_inputs:
        inputs = LoopInputBundle(
            runtime=runtime,
            proposal_template=proposal(signal_source()),
            replay_bars=(bar(),),
            future_sessions=future,
        )
    else:
        template = kr_proposal(kr_signal_source())
        replay_bars = (kr_bar(),)
        inputs = KrDayLoopInputBundle(
            binding=build_kr_day_loop_research_binding(template, replay_bars, "005930"),
            runtime=runtime,
            proposal_template=template,
            replay_bars=replay_bars,
            future_sessions=future,
        )
    assert publish_private_immutable_text(
        config.state_root / "kr-day-loop-inputs.json",
        inputs.model_dump_json(),
    )
    assert publish_private_immutable_text(
        config.state_root / "kr-day-loop-patch.json",
        ProposedAgentChange(
            patch=ExitPolicyPatch(
                kind=AgentChangeKind.EXIT_POLICY,
                rule=ExitRule.TRAILING_STRUCTURE,
                trailing_window_bars=5,
            )
        ).model_dump_json(),
    )


def _authorized_evaluation(capsule_id: str, hypothesis_id: str, snapshot_id: str) -> KrDayCapsuleEvaluation:
    return _bind_evaluation(_entry_evaluation(), capsule_id, hypothesis_id, snapshot_id)


def kr_proposal(source: str) -> ProposedHypothesis:
    registered_at = PUBLISHED_AT - dt.timedelta(days=1)
    research_source = ResearchSource(
        source_id="kr-krx-005930-market-data",
        source_kind=ResearchSourceKind.OFFICIAL_MARKET_RULE,
        title="KRX 005930 completed-bar market data contract",
        source_url="https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd",
        published_on=dt.date(2026, 8, 1),
        claim="KRX identifies 005930 as a Korean listed-equity instrument for bounded research.",
        limitations="The official instrument source does not establish profitability.",
        retrieved_at=registered_at,
        ledger_recorded_at=registered_at,
    )
    scope = ExperimentScope(
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id="KR-DAY-005930-LOOP-001",
        primary_lane=LaneId.INTRADAY_MOMENTUM,
        lanes=(LaneId.INTRADAY_MOMENTUM,),
        registered_at=registered_at,
    )
    registration = HypothesisRegistration(
        hypothesis_id=scope.hypothesis_id,
        experiment_scope=scope,
        experiment_scope_key=experiment_scope_key(scope),
        primary_lane=scope.primary_lane,
        hypothesis="KR equity 005930 may exhibit bounded completed-bar continuation after host validation.",
        falsification_rule="Reject when exact KR replay and future shadow evidence fail registered thresholds.",
        source_registered_at=registered_at,
        ledger_recorded_at=registered_at,
    )
    card = ResearchHypothesisCard(
        hypothesis=registration,
        research_source_keys=(str(research_source_key(research_source)),),
        economic_mechanism="KRX order-flow persistence is tested only as a research hypothesis.",
        counterfactual_baseline="Matched 005930 completed bars without the registered continuation setup.",
    )
    return ProposedHypothesis(
        card=card,
        cited_sources=(research_source,),
        llm_receipt=LlmCallReceipt(
            model_id="fixture-kr-researcher-v1",
            prompt_sha256="c" * 64,
            response_sha256="d" * 64,
            seed=11,
            temperature=0.0,
            called_at=registered_at,
        ),
        strategy_draft=CandidateStrategyDraft(source, ()),
    )


def kr_signal_source() -> str:
    return (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            if bar['symbol'] != '005930':\n"
        "                return None\n"
        "            return None\n"
        "    return Strategy()\n"
    )


def kr_bar() -> BarInput:
    return BarInput(
        "005930",
        PUBLISHED_AT - dt.timedelta(minutes=5),
        70_000.0,
        70_500.0,
        69_500.0,
        70_100.0,
        1_000_000,
        69_800.0,
        15_000_000,
        8.0,
    )


def _bind_evaluation(
    source: KrDayCapsuleEvaluation,
    capsule_id: str,
    hypothesis_id: str,
    snapshot_id: str,
) -> KrDayCapsuleEvaluation:
    setup = source.setup_input.model_copy(update={"producer_strategy_version": capsule_id})
    values = source.model_dump(mode="python", exclude={"evaluation_id"}) | {
        "capsule_id": capsule_id,
        "hypothesis_version_id": hypothesis_id,
        "calendar_snapshot_id": snapshot_id,
        "setup_input": setup,
    }
    payload = KrDayCapsuleEvaluationPayload.model_validate(values)
    return KrDayCapsuleEvaluation.model_validate(
        values | {"evaluation_id": KrDayCapsuleEvaluation.canonical_id_for(payload)}
    )


def _head() -> str:
    return subprocess.run(
        ("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = ("KST", "ROOT", "SESSION_DATE", "CloseFixture", "close_fixture")
