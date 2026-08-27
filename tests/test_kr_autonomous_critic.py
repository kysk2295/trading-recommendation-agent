from __future__ import annotations

import datetime as dt

from tests.test_kr_autonomous_trade_planner import _request
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousCriticStatus,
    KrAutonomousRejected,
    KrAutonomousTradeThesis,
    KrCriticReason,
    thesis_id,
)
from trading_agent.kr_autonomous_trade_planner import criticize_kr_autonomous_trade, plan_kr_autonomous_trade
from trading_agent.kr_social_signal_models import KrSocialVerificationState


def test_critic_approves_exact_lineage_without_editing_levels() -> None:
    # Given: a deterministic proposal with exact task/social/market/evidence lineage.
    request = _request()

    # When: the Critic evaluates the proposal twice.
    first = criticize_kr_autonomous_trade(request)
    second = criticize_kr_autonomous_trade(request)

    # Then: approval is content-addressed and deterministic.
    assert first.status is KrAutonomousCriticStatus.APPROVED
    assert first == second
    assert first.verdict_id == second.verdict_id


def test_post_reaction_social_publication_is_rejected() -> None:
    # Given: a signal whose claimed publication occurs after the market response.
    request = _request()
    signal = request.social_signal.model_copy(
        update={"earliest_published_at": request.market.market_response_at + dt.timedelta(microseconds=1)}
    )

    # When: the Critic evaluates causal chronology.
    verdict = criticize_kr_autonomous_trade(request.model_copy(update={"social_signal": signal}))

    # Then: post-reaction discovery cannot become an approved recommendation.
    assert verdict.status is KrAutonomousCriticStatus.REJECTED


def test_contradictory_rationale_is_rejected_and_persistable() -> None:
    # Given: the hypothesis is repeated verbatim as its own counterevidence.
    request = _request()
    thesis = request.thesis.model_copy(update={"counterevidence": (request.thesis.hypothesis,)})
    thesis = KrAutonomousTradeThesis.model_validate(
        thesis.model_copy(update={"thesis_id": thesis_id(thesis)}).model_dump(mode="python")
    )
    unsafe = request.model_copy(update={"thesis": thesis})

    # When: the full planning boundary runs the Critic.
    result = plan_kr_autonomous_trade(unsafe)

    # Then: rejection is an immutable event-shaped outcome, not an edited plan.
    assert isinstance(result, KrAutonomousRejected)
    assert result.critic_verdict_id == criticize_kr_autonomous_trade(unsafe).verdict_id


def test_lineage_mismatch_is_rejected() -> None:
    # Given: the thesis points at a different content-addressed task.
    request = _request()
    thesis = request.thesis.model_copy(update={"task_id": "f" * 64})
    thesis = KrAutonomousTradeThesis.model_validate(
        thesis.model_copy(update={"thesis_id": thesis_id(thesis)}).model_dump(mode="python")
    )

    # When: the Critic checks exact task lineage.
    verdict = criticize_kr_autonomous_trade(request.model_copy(update={"thesis": thesis}))

    # Then: no mismatched lineage is approved.
    assert verdict.status is KrAutonomousCriticStatus.REJECTED


def test_symbol_lineage_mismatch_is_rejected() -> None:
    # Given: the thesis symbol differs from its exact social and market lineage.
    request = _request()
    thesis = request.thesis.model_copy(update={"symbol": "000660"})
    thesis = KrAutonomousTradeThesis.model_validate(
        thesis.model_copy(update={"thesis_id": thesis_id(thesis)}).model_dump(mode="python")
    )

    # When: the Critic checks exact artifact lineage.
    verdict = criticize_kr_autonomous_trade(request.model_copy(update={"thesis": thesis}))

    # Then: content IDs cannot authorize a different symbol.
    assert verdict.status is KrAutonomousCriticStatus.REJECTED


def test_forged_nested_verification_state_is_rejected_before_critic() -> None:
    # Given: model_copy upgrades an unverified signal without recomputing its content address.
    request = _request(verified=False)
    forged_signal = request.social_signal.model_copy(
        update={"verification_state": KrSocialVerificationState.MULTI_SOURCE_CORROBORATED}
    )

    # When: the forged request crosses the public Critic boundary.
    verdict = criticize_kr_autonomous_trade(request.model_copy(update={"social_signal": forged_signal}))

    # Then: forged verified sizing cannot receive approval.
    assert verdict.status is KrAutonomousCriticStatus.REJECTED
    assert verdict.reason_codes == (KrCriticReason.EVIDENCE_LINEAGE,)
