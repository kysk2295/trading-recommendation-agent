from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.day_agent_forward_shadow_support import (
    dual_capsule_runtime,
    session_request,
)
from tests.day_agent_loop_e2e_support import LoopEvaluationFixture, loop_evaluation
from tests.day_agent_version_learning_support import SESSION, champion
from tests.test_day_learning_report_models import NOW, SHA_A
from trading_agent.day_agent_challenger_evaluation import (
    DayAgentChallengerEvaluationRequest,
    UsForwardShadowControllerRunner,
    deploy_recommended_challenger,
    evaluate_day_agent_challenger,
)
from trading_agent.day_agent_version_models import (
    AgentDeploymentState,
    AgentEvaluationMetrics,
    AgentPromotionDecision,
    AgentPromotionRecommendation,
    AgentScoreComparison,
    DayAgentVersionStoreError,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore


def test_real_forward_shadow_controller_runs_both_capsules_on_identical_snapshots(
    tmp_path: Path,
) -> None:
    # Given: two research-only generated capsules selected by one stored US policy.
    services, champion_capsule, challenger_capsule, policies = dual_capsule_runtime(tmp_path)
    request = session_request(services, policies[0].policy_id, dt.date(2026, 8, 21))

    # When: the production adapter drives the completed Forward Shadow controller.
    evidence = UsForwardShadowControllerRunner(services).run_session(
        request,
        (champion_capsule.capsule_id, challenger_capsule.capsule_id),
    )

    # Then: every tick contains both capsules and persisted validated artifacts are returned.
    assert all(len(item.results) == 2 for item in evidence.tick_results)
    assert {item.capsule_id for item in evidence.signals} == {challenger_capsule.capsule_id}
    assert {item.trial_id for item in evidence.outcomes} == {item.trial_id for item in evidence.signals}


def _evaluated_promotion(
    fixture: LoopEvaluationFixture,
) -> AgentPromotionRecommendation:
    sessions = (
        session_request(
            fixture.controller.services,
            fixture.policies[0].policy_id,
            fixture.policies[0].payload.effective_session_date,
        ),
        session_request(
            fixture.controller.services,
            fixture.policies[1].policy_id,
            fixture.policies[1].payload.effective_session_date,
        ),
    )
    request = DayAgentChallengerEvaluationRequest(
        champion=fixture.baseline,
        challenger=fixture.challenger,
        champion_capsule_id=fixture.champion_capsule.capsule_id,
        challenger_capsule_id=fixture.challenger_capsule.capsule_id,
        sessions=sessions,
        minimum_sessions=2,
        evaluated_at=dt.datetime(2026, 8, 24, 20, 0, tzinfo=dt.UTC),
    )
    return evaluate_day_agent_challenger(
        request,
        fixture.store,
        fixture.controller,
    )


def test_host_deployment_promotes_only_validated_multi_session_recommendation(
    tmp_path: Path,
) -> None:
    # Given: a promotion recommendation derived from two paired future controller sessions.
    services, champion_capsule, challenger_capsule, policies = dual_capsule_runtime(tmp_path / "evaluation")
    base = champion()
    baseline = build_agent_version(
        model_role_bindings=base.model_role_bindings,
        prompt_sha256=base.prompt_sha256,
        tool_policy_sha256=base.tool_policy_sha256,
        memory_retrieval_policy_sha256=base.memory_retrieval_policy_sha256,
        playbook_ids=(champion_capsule.capsule_id,),
        parent_version_id=None,
        creation_evidence_ids=base.payload.creation_evidence_ids,
        deployment_state=AgentDeploymentState.CHAMPION,
        task_id=base.task_id,
        created_at=base.created_at,
        created_session_date=base.created_session_date,
    )
    challenger = build_agent_version(
        model_role_bindings=baseline.model_role_bindings,
        prompt_sha256="5" * 64,
        tool_policy_sha256=baseline.tool_policy_sha256,
        memory_retrieval_policy_sha256=baseline.memory_retrieval_policy_sha256,
        playbook_ids=(challenger_capsule.capsule_id,),
        parent_version_id=baseline.version_id,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=baseline.task_id,
        created_at=NOW,
        created_session_date=SESSION,
    )
    store = DayAgentVersionStore(tmp_path / "promotion-versions.sqlite3")
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)
        assert writer.register_challenger(challenger)
    sessions = tuple(
        session_request(services, policy.policy_id, policy.payload.effective_session_date)
        for policy in policies
    )
    recommendation = evaluate_day_agent_challenger(
        DayAgentChallengerEvaluationRequest(
            champion=baseline,
            challenger=challenger,
            champion_capsule_id=champion_capsule.capsule_id,
            challenger_capsule_id=challenger_capsule.capsule_id,
            sessions=sessions,
            minimum_sessions=2,
            evaluated_at=dt.datetime(2026, 8, 24, 20, 0, tzinfo=dt.UTC),
        ),
        store,
        UsForwardShadowControllerRunner(services),
    )

    # When: the deterministic host deployment function applies the stored recommendation.
    transition = deploy_recommended_challenger(recommendation, store)

    # Then: promotion is atomic and the prior Champion is demoted in the query projection.
    assert recommendation.decision is AgentPromotionDecision.PROMOTE
    assert recommendation.comparison.challenger.provenance_ids
    assert recommendation.comparison.challenger.theme_timing > recommendation.comparison.champion.theme_timing
    metrics = recommendation.comparison.challenger
    assert metrics.theme_timing == 0.75
    assert metrics.leader_rank == 0.4
    assert metrics.recommendation_calibration == pytest.approx(0.9045907590759076)
    assert metrics.mfe == pytest.approx(120.0 / 101.0 - 1.0)
    assert metrics.mae == pytest.approx(100.5 / 101.0 - 1.0)
    assert metrics.cost_adjusted_modeled_result == pytest.approx(0.014251485148514851)
    assert metrics.no_trade_quality == 1.0
    assert metrics.evidence_fidelity == 1.0
    assert transition.promoted_version_id == challenger.version_id
    states = {item.version_id: item.deployment_state for item in store.reader().versions()}
    assert states == {
        baseline.version_id: "shadow",
        challenger.version_id: "champion",
    }
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


def test_predeclared_observation_threshold_blocks_two_good_sessions(tmp_path: Path) -> None:
    # Given: a challenger with only eight of its twenty preregistered observations.
    services, champion_capsule, challenger_capsule, policies = dual_capsule_runtime(tmp_path)
    base = champion()
    baseline = build_agent_version(
        model_role_bindings=base.model_role_bindings,
        prompt_sha256=base.prompt_sha256,
        tool_policy_sha256=base.tool_policy_sha256,
        memory_retrieval_policy_sha256=base.memory_retrieval_policy_sha256,
        playbook_ids=(champion_capsule.capsule_id,),
        parent_version_id=None,
        creation_evidence_ids=base.payload.creation_evidence_ids,
        deployment_state=AgentDeploymentState.CHAMPION,
        task_id=base.task_id,
        created_at=base.created_at,
        created_session_date=base.created_session_date,
    )
    challenger = build_agent_version(
        model_role_bindings=baseline.model_role_bindings,
        prompt_sha256="5" * 64,
        tool_policy_sha256=baseline.tool_policy_sha256,
        memory_retrieval_policy_sha256=baseline.memory_retrieval_policy_sha256,
        playbook_ids=(challenger_capsule.capsule_id,),
        parent_version_id=baseline.version_id,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=baseline.task_id,
        created_at=NOW,
        created_session_date=SESSION,
    )
    store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)
        assert writer.register_challenger(challenger)
    sessions = tuple(
        session_request(services, policy.policy_id, policy.payload.effective_session_date)
        for policy in policies[:2]
    )

    # When: the deterministic evaluator sees a favorable but undersized sample.
    recommendation = evaluate_day_agent_challenger(
        DayAgentChallengerEvaluationRequest(
            champion=baseline,
            challenger=challenger,
            champion_capsule_id=champion_capsule.capsule_id,
            challenger_capsule_id=challenger_capsule.capsule_id,
            sessions=sessions,
            minimum_sessions=2,
            evaluated_at=dt.datetime(2026, 8, 24, 20, 0, tzinfo=dt.UTC),
        ),
        store,
        UsForwardShadowControllerRunner(services),
    )

    # Then: it durably rejects instead of promoting from a small sample.
    assert recommendation.decision is AgentPromotionDecision.REJECT
    assert recommendation.reason_codes == ("minimum_observations_not_met",)
    assert store.reader().recommendations(challenger.version_id) == (recommendation,)


class _DerivedController(UsForwardShadowControllerRunner):
    pass


def test_public_evaluation_rejects_substituted_controller_output(tmp_path: Path) -> None:
    # Given: valid persisted versions and sessions but a substituted controller subtype.
    fixture = loop_evaluation(tmp_path)
    request = DayAgentChallengerEvaluationRequest(
        champion=fixture.baseline,
        challenger=fixture.challenger,
        champion_capsule_id=fixture.champion_capsule.capsule_id,
        challenger_capsule_id=fixture.challenger_capsule.capsule_id,
        sessions=(
            session_request(
                fixture.controller.services,
                fixture.policies[0].policy_id,
                fixture.policies[0].payload.effective_session_date,
            ),
            session_request(
                fixture.controller.services,
                fixture.policies[1].policy_id,
                fixture.policies[1].payload.effective_session_date,
            ),
        ),
        minimum_sessions=2,
        evaluated_at=dt.datetime(2026, 8, 24, 20, 0, tzinfo=dt.UTC),
    )

    # When / Then: public evaluation rejects it before any controller session runs.
    with pytest.raises(DayAgentVersionStoreError, match="future_shadow_version_invalid"):
        _ = evaluate_day_agent_challenger(
            request,
            fixture.store,
            _DerivedController(fixture.controller.services),
        )


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
        controller_evidence_ids=("7" * 64, "8" * 64),
        comparison=AgentScoreComparison(
            champion=AgentEvaluationMetrics(
                theme_timing=0.1,
                leader_rank=0.1,
                recommendation_calibration=0.1,
                mfe=0.01,
                mae=-0.01,
                cost_adjusted_modeled_result=0.0,
                no_trade_quality=0.1,
                evidence_fidelity=1.0,
                provenance_ids=("7" * 64,),
            ),
            challenger=AgentEvaluationMetrics(
                theme_timing=0.9,
                leader_rank=0.9,
                recommendation_calibration=0.9,
                mfe=0.1,
                mae=0.0,
                cost_adjusted_modeled_result=0.1,
                no_trade_quality=0.9,
                evidence_fidelity=1.0,
                provenance_ids=("8" * 64,),
            ),
        ),
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
