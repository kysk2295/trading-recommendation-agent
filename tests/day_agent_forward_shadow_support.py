from __future__ import annotations

import datetime as dt
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.day_agent_version_learning_support import SESSION
from tests.day_strategy_capsule_support import bar, builtin_capsule, proposal
from tests.strategy_research_contract_fixtures import hypothesis
from tests.test_day_learning_report_models import NOW, SHA_A
from tests.test_day_research_attempt_binding import _attempt, _binding, _family, _version
from tests.us_forward_shadow_support import no_signal_source, prepared_runtime, shadow_tick, signal_source
from trading_agent.day_agent_challenger_evaluation import (
    DayForwardShadowSessionRequest,
    DayForwardShadowTickRequest,
    UsForwardShadowControllerRunner,
)
from trading_agent.day_agent_version_models import (
    AgentDeploymentState,
    AgentModelRoleBinding,
    AgentVersion,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_policy import ExplorationPolicy, ExplorationPolicyPayload
from trading_agent.day_strategy_capsule import (
    DayStrategyCapsuleRequest,
    GeneratedCapsuleVerification,
    generated_evaluator_bundle_sha256,
    generated_protocol_bundle_sha256,
    publish_day_strategy_capsule,
)
from trading_agent.day_strategy_capsule_models import CapsuleArtifactKind
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_types import AttemptStatus
from trading_agent.us_forward_shadow_models import completed_bar_id


def dual_capsule_runtime(root: Path):
    services, champion_capsule = prepared_runtime(root, source=no_signal_source())
    published = services.generated_artifacts.publish(proposal(signal_source()))
    source_sha256 = published.artifact.payload.source_sha256
    family = _family()
    base_version = _version(family, code_sha256=source_sha256)
    version_payload = base_version.model_dump(mode="python") | {
        "hypothesis_version_id": "",
        "protocol_sha256": generated_protocol_bundle_sha256(),
    }
    version = base_version.model_validate(
        version_payload | {"hypothesis_version_id": base_version.canonical_id_for(version_payload)}
    )
    hypothesis_fixture = hypothesis()
    registered_hypothesis = hypothesis_fixture.model_copy(
        update={
            "hypothesis_id": "hypothesis-catalyst-002",
            "code_sha256": source_sha256,
            "holdout_period_sealed_ref": hypothesis_fixture.holdout_period_sealed_ref.model_copy(
                update={"seal_id": "sealed-holdout-catalyst-2026q3-challenger"}
            ),
        }
    )
    manifest = PreregistrationManifest.from_hypothesis(
        registered_hypothesis,
        preregistered_at=registered_hypothesis.created_at,
    )
    attempt = _attempt(1, AttemptStatus.SUCCEEDED).model_copy(
        update={
            "hypothesis_id": registered_hypothesis.hypothesis_id,
            "code_sha256": source_sha256,
            "artifact_refs": (f"artifact://safe/{source_sha256}",),
        }
    )
    binding = _binding(attempt, version, artifact_ref=f"artifact://safe/{source_sha256}")
    with services.ledger.writer() as writer:
        _ = writer.register_strategy_research(manifest)
        _ = writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(version)
        assert writer.append_strategy_research_attempt(attempt)
        assert writer.register_day_research_attempt_binding(binding)
    limits = builtin_capsule().resource_limits
    sandbox = GeneratedStrategySandbox(
        resolve_generated_strategy_runtime(Path(sys.executable)),
        root / "challenger-preflight",
        limits.to_generated_limits(),
    )
    request = DayStrategyCapsuleRequest(
        hypothesis_version_id=version.hypothesis_version_id,
        attempt_binding_id=binding.binding_id,
        market_id=champion_capsule.market_id,
        artifact_kind=CapsuleArtifactKind.GENERATED_PYTHON,
        artifact_ref=binding.artifact_ref,
        artifact_sha256=source_sha256,
        generated_artifact_id=published.artifact.artifact_id,
        evaluation_cadence=version.evaluation_cadence,
        evidence_schema=("completed_bar_v1",),
        entry_rule=version.entry_rule,
        exit_rule=version.exit_rule,
        stop_rule=version.stop_rule,
        target_rule="host_projects_preregistered_targets",
        cost_model=version.cost_model,
        slippage_model_id="bounded_intraday_slippage_v1",
        resource_limits=limits,
        risk_policy_ref="risk-policy://day-research/v1",
        protocol_version=1,
        protocol_sha256=generated_protocol_bundle_sha256(),
        evaluator_sha256=generated_evaluator_bundle_sha256(),
        published_at=binding.bound_at + dt.timedelta(minutes=1),
        authority_ceiling=champion_capsule.authority_ceiling,
        generated_verification=GeneratedCapsuleVerification(services.generated_artifacts, sandbox, (bar(),)),
    )
    challenger_capsule, created = publish_day_strategy_capsule(services.ledger, request)
    assert created
    original = services.ledger.reader().day_exploration_policies()[0]
    policies = tuple(
        _policy_for_session(
            original,
            (session_date, report_id),
            (
                min(champion_capsule.capsule_id, challenger_capsule.capsule_id),
                max(champion_capsule.capsule_id, challenger_capsule.capsule_id),
            ),
        )
        for session_date, report_id in (
            (dt.date(2026, 8, 21), "9" * 64),
            (dt.date(2026, 8, 24), "8" * 64),
        )
    )
    with services.ledger.writer() as writer:
        assert all(writer.record_day_exploration_policy(policy) for policy in policies)
    return services, champion_capsule, challenger_capsule, policies


def _policy_for_session(
    original: ExplorationPolicy,
    specification: tuple[dt.date, str],
    capsule_ids: tuple[str, str],
) -> ExplorationPolicy:
    session_date, report_id = specification
    payload = ExplorationPolicyPayload.model_validate(
        original.payload.model_dump(mode="python")
        | {
            "final_report_id": report_id,
            "effective_session_date": session_date,
            "effective_at": dt.datetime.combine(session_date, dt.time(13, 30), tzinfo=dt.UTC),
            "active_capsule_ids": capsule_ids,
        }
    )
    return ExplorationPolicy(
        policy_id=hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest(),
        payload=payload,
    )


@dataclass(frozen=True, slots=True)
class EvaluationFixture:
    store: DayAgentVersionStore
    baseline: AgentVersion
    challenger: AgentVersion
    controller: UsForwardShadowControllerRunner
    policy_ids: tuple[str, str]


def registered_evaluation(root: Path) -> EvaluationFixture:
    services, champion_capsule, challenger_capsule, policies = dual_capsule_runtime(root / "controller")
    baseline = build_agent_version(
        model_role_bindings=(AgentModelRoleBinding(role="reasoning", model_id="reasoner-v1"),),
        prompt_sha256="1" * 64,
        tool_policy_sha256="2" * 64,
        memory_retrieval_policy_sha256="3" * 64,
        playbook_ids=(champion_capsule.capsule_id,),
        parent_version_id=None,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.CHAMPION,
        task_id="task-20260820-NVDA",
        created_at=NOW,
        created_session_date=SESSION,
    )
    challenger = build_agent_version(
        model_role_bindings=baseline.model_role_bindings,
        prompt_sha256=baseline.prompt_sha256,
        tool_policy_sha256="5" * 64,
        memory_retrieval_policy_sha256=baseline.memory_retrieval_policy_sha256,
        playbook_ids=(challenger_capsule.capsule_id,),
        parent_version_id=baseline.version_id,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=baseline.task_id,
        created_at=NOW,
        created_session_date=SESSION,
    )
    store = DayAgentVersionStore(root / "versions.sqlite3")
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)
        assert writer.register_challenger(challenger)
    return EvaluationFixture(
        store=store,
        baseline=baseline,
        challenger=challenger,
        controller=UsForwardShadowControllerRunner(services),
        policy_ids=(policies[0].policy_id, policies[1].policy_id),
    )


def session_request(services, policy_id: str, session_date: dt.date) -> DayForwardShadowSessionRequest:
    baseline = tuple(
        shadow_tick(
            services,
            minute,
            sequence,
            high=120.0 if sequence == 4 else None,
            policy_id=policy_id,
        )
        for sequence, minute in enumerate((1, 2, 3, 4), start=1)
    )
    delta = session_date - SESSION
    ticks = tuple(_shift_tick(item, delta, session_date) for item in baseline)
    return DayForwardShadowSessionRequest(
        ticks=tuple(DayForwardShadowTickRequest(tick=item, evaluation_at=item.observed_at) for item in ticks)
    )


def _shift_tick(tick, delta: dt.timedelta, session_date: dt.date):
    bars = tuple(item.model_copy(update={"timestamp": item.timestamp + delta}) for item in tick.bars)
    candidate = (
        None
        if tick.candidate is None
        else tick.candidate.model_copy(update={"timestamp": tick.candidate.timestamp + delta})
    )
    quote = tick.quote.model_copy(
        update={
            "observed_at": tick.quote.observed_at + delta,
            "valid_until": tick.quote.valid_until + delta,
        }
    )
    refs = tuple(item.model_copy(update={"observed_at": item.observed_at + delta}) for item in tick.evidence_refs)
    return tick.model_validate(
        tick.model_dump(mode="python")
        | {
            "session_id": f"XNYS-{session_date.isoformat()}",
            "session_date": session_date,
            "completed_bar_id": completed_bar_id(bars[-1]),
            "bars": bars,
            "candidate": candidate,
            "quote": quote,
            "evidence_refs": refs,
            "observed_at": tick.observed_at + delta,
        }
    )


__all__ = ("EvaluationFixture", "dual_capsule_runtime", "registered_evaluation", "session_request")
