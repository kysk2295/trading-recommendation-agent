from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.day_agent_forward_shadow_support import (
    TypedControllerFake,
    dual_capsule_runtime,
    session_request,
)
from tests.day_agent_version_learning_support import SESSION, champion
from tests.test_day_learning_report_models import NOW, SHA_A
from tests.us_forward_shadow_support import prepared_runtime, signal_source
from trading_agent.day_agent_challenger_evaluation import (
    DayAgentChallengerEvaluationRequest,
    UsForwardShadowControllerRunner,
    deploy_recommended_challenger,
    evaluate_day_agent_challenger,
)
from trading_agent.day_agent_version_models import (
    AgentDeploymentState,
    AgentModelRoleBinding,
    AgentPromotionDecision,
    AgentPromotionRecommendation,
    AgentVersion,
    DayAgentVersionStoreError,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore


def test_real_forward_shadow_controller_runs_both_capsules_on_identical_snapshots(
    tmp_path: Path,
) -> None:
    # Given: two research-only generated capsules selected by one stored US policy.
    services, champion_capsule, challenger_capsule, policy = dual_capsule_runtime(tmp_path)
    request = session_request(services, policy.policy_id, dt.date(2026, 8, 21))

    # When: the production adapter drives the completed Forward Shadow controller.
    evidence = UsForwardShadowControllerRunner(services).run_session(
        request,
        (champion_capsule.capsule_id, challenger_capsule.capsule_id),
    )

    # Then: every tick contains both capsules and persisted validated artifacts are returned.
    assert all(len(item.results) == 2 for item in evidence.tick_results)
    assert {item.capsule_id for item in evidence.signals} == {
        champion_capsule.capsule_id,
        challenger_capsule.capsule_id,
    }
    assert {item.trial_id for item in evidence.outcomes} == {item.trial_id for item in evidence.signals}


def _evaluated_promotion(
    root: Path,
    store: DayAgentVersionStore,
    baseline: AgentVersion,
    challenger: AgentVersion,
) -> AgentPromotionRecommendation:
    services, _ = prepared_runtime(root, source=signal_source())
    policy = services.ledger.reader().day_exploration_policies()[0]
    sessions = (
        session_request(services, policy.policy_id, dt.date(2026, 8, 21)),
        session_request(services, policy.policy_id, dt.date(2026, 8, 24)),
    )
    request = DayAgentChallengerEvaluationRequest(
        champion=baseline,
        challenger=challenger,
        champion_capsule_id=baseline.playbook_ids[0],
        challenger_capsule_id=challenger.playbook_ids[0],
        sessions=sessions,
        minimum_sessions=2,
        evaluated_at=dt.datetime(2026, 8, 24, 20, 0, tzinfo=dt.UTC),
    )
    return evaluate_day_agent_challenger(
        request,
        store,
        TypedControllerFake(baseline.playbook_ids[0], challenger.playbook_ids[0]),
    )


def test_host_deployment_promotes_only_validated_multi_session_recommendation(
    tmp_path: Path,
) -> None:
    # Given: a promotion recommendation derived from two paired future controller sessions.
    store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    baseline = build_agent_version(
        model_role_bindings=(AgentModelRoleBinding(role="reasoning", model_id="reasoner-v1"),),
        prompt_sha256="1" * 64,
        tool_policy_sha256="2" * 64,
        memory_retrieval_policy_sha256="3" * 64,
        playbook_ids=("4" * 64,),
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
        playbook_ids=("5" * 64,),
        parent_version_id=baseline.version_id,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=baseline.task_id,
        created_at=NOW,
        created_session_date=SESSION,
    )
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)
        assert writer.register_challenger(challenger)
    recommendation = _evaluated_promotion(
        tmp_path / "evaluation",
        store,
        baseline,
        challenger,
    )

    # When: the deterministic host deployment function applies the stored recommendation.
    transition = deploy_recommended_challenger(recommendation, store)

    # Then: promotion is atomic and the prior Champion is demoted in the query projection.
    assert recommendation.decision is AgentPromotionDecision.PROMOTE
    assert transition.promoted_version_id == challenger.version_id
    states = {item.version_id: item.deployment_state for item in store.reader().versions()}
    assert states == {baseline.version_id: "shadow", challenger.version_id: "champion"}
    next_challenger = build_agent_version(
        model_role_bindings=challenger.model_role_bindings,
        prompt_sha256="6" * 64,
        tool_policy_sha256=challenger.tool_policy_sha256,
        memory_retrieval_policy_sha256=challenger.memory_retrieval_policy_sha256,
        playbook_ids=challenger.playbook_ids,
        parent_version_id=challenger.version_id,
        creation_evidence_ids=(transition.transition_id,),
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=challenger.task_id,
        created_at=NOW + dt.timedelta(days=1),
        created_session_date=dt.date(2026, 8, 25),
    )
    with store.writer() as writer:
        assert writer.register_challenger(next_challenger)


def test_host_deployment_rejects_same_session_or_unstored_recommendation(
    tmp_path: Path,
) -> None:
    # Given: a syntactically valid promotion recommendation without validated stored evaluation.
    store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    baseline = champion()
    challenger = build_agent_version(
        model_role_bindings=baseline.model_role_bindings,
        prompt_sha256="5" * 64,
        tool_policy_sha256=baseline.tool_policy_sha256,
        memory_retrieval_policy_sha256=baseline.memory_retrieval_policy_sha256,
        playbook_ids=baseline.playbook_ids,
        parent_version_id=baseline.version_id,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=baseline.task_id,
        created_at=NOW,
        created_session_date=SESSION,
    )
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)
        assert writer.register_challenger(challenger)
    recommendation = AgentPromotionRecommendation(
        recommendation_id="9" * 64,
        champion_version_id=baseline.version_id,
        challenger_version_id=challenger.version_id,
        decision=AgentPromotionDecision.PROMOTE,
        evaluated_session_dates=(SESSION,),
        paired_snapshot_ids=("snapshot-same-session",),
        controller_evidence_ids=("8" * 64,),
        champion_score=0.1,
        challenger_score=0.9,
        reason_codes=("challenger_margin_met",),
        evaluated_at=NOW + dt.timedelta(hours=1),
    )

    # When / Then: deployment fails before any effective state transition.
    with pytest.raises(DayAgentVersionStoreError, match="deployment_recommendation_invalid"):
        _ = deploy_recommended_challenger(recommendation, store)
    assert {item.deployment_state for item in store.reader().versions()} == {
        "champion",
        "shadow",
    }
