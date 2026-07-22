from __future__ import annotations

import trading_agent.intraday_research_loop_models as models


def test_reviewer_promotes_only_mature_cost_adjusted_oos_evidence() -> None:
    # Given: mature OOS evidence that clears the existing comparison-ready gates.
    evidence = models.IntradayReviewEvidence(
        observed_sessions=20,
        trade_count=30,
        average_return=0.004,
        profit_factor=1.25,
        mean_ci_low=0.001,
        mean_ci_high=0.007,
    )

    # When: the independent policy reviews it.
    decision = models.intraday_reviewer_decision(evidence)

    # Then: promotion is recommended without granting state authority.
    assert decision is models.IntradayReviewerDecision.PROMOTE


def test_reviewer_demotes_only_clear_cost_adjusted_failure() -> None:
    # Given: five OOS sessions and ten trades with a fully negative interval.
    evidence = models.IntradayReviewEvidence(
        observed_sessions=5,
        trade_count=10,
        average_return=-0.004,
        profit_factor=0.5,
        mean_ci_low=-0.007,
        mean_ci_high=-0.001,
    )

    # When: the independent policy reviews it.
    decision = models.intraday_reviewer_decision(evidence)

    # Then: demotion is recommended.
    assert decision is models.IntradayReviewerDecision.DEMOTE


def test_reviewer_holds_immature_evidence() -> None:
    # Given: a single profitable OOS trade.
    evidence = models.IntradayReviewEvidence(
        observed_sessions=1,
        trade_count=1,
        average_return=0.02,
        profit_factor=None,
        mean_ci_low=None,
        mean_ci_high=None,
    )

    # When: the independent policy reviews it.
    decision = models.intraday_reviewer_decision(evidence)

    # Then: sample maturity keeps the hypothesis on hold.
    assert decision is models.IntradayReviewerDecision.HOLD
