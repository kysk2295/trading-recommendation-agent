from __future__ import annotations

from pathlib import Path

import pytest

from tests.day_agent_forward_shadow_support import session_request
from tests.day_agent_loop_e2e_support import loop_evaluation
from tests.day_agent_version_learning_support import SESSION
from trading_agent.day_agent_challenger_evaluation import (
    DayAgentChallengerEvaluationRequest,
    evaluate_day_agent_challenger,
)
from trading_agent.day_agent_version_models import AgentPromotionDecision
from trading_agent.us_forward_shadow_services import InvalidUsForwardShadowRuntimeError


def test_finalized_close_change_runs_as_the_exact_future_shadow_challenger(tmp_path: Path) -> None:
    # Given: a finalized close report that produced one real persisted Challenger lineage.
    fixture = loop_evaluation(tmp_path)
    sessions = tuple(
        session_request(
            fixture.controller.services,
            policy.policy_id,
            policy.payload.effective_session_date,
        )
        for policy in fixture.policies
    )

    # When: two later sessions run both exact stored capsules through the production controller.
    recommendation = evaluate_day_agent_challenger(
        DayAgentChallengerEvaluationRequest(
            champion=fixture.baseline,
            challenger=fixture.challenger,
            champion_capsule_id=fixture.champion_capsule.capsule_id,
            challenger_capsule_id=fixture.challenger_capsule.capsule_id,
            sessions=sessions,
            minimum_sessions=2,
            evaluated_at=fixture.policies[-1].payload.effective_at.replace(hour=20),
        ),
        fixture.store,
        fixture.controller,
    )

    # Then: proposal, version, capsule, policies, controller, and recommendation retain one identity chain.
    assert fixture.proposal.version_id == fixture.challenger.version_id
    assert fixture.challenger.playbook_ids == (fixture.challenger_capsule.capsule_id,)
    assert fixture.challenger_capsule.trading_authority is False
    assert all(
        policy.payload.active_capsule_ids
        == tuple(sorted((fixture.champion_capsule.capsule_id, fixture.challenger_capsule.capsule_id)))
        for policy in fixture.policies
    )
    assert len(fixture.policies) == len(sessions) == 2
    assert recommendation.decision is AgentPromotionDecision.REJECT
    assert fixture.store.reader().recommendations(fixture.challenger.version_id) == (recommendation,)
    with pytest.raises(InvalidUsForwardShadowRuntimeError, match="policy_not_effective"):
        _ = fixture.controller.run_session(
            session_request(
                fixture.controller.services,
                fixture.policies[0].policy_id,
                SESSION,
            ),
            (fixture.champion_capsule.capsule_id, fixture.challenger_capsule.capsule_id),
        )
