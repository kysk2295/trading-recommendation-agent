from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from trading_agent.strategy_research_methodologies import (
    ResamplingMethod,
    strategy_research_methodology,
)
from trading_agent.strategy_research_observation_builders import (
    MethodologyObservationInput,
    SourceAuthorityReceipt,
    build_methodology_observation,
)
from trading_agent.strategy_research_policy import (
    FeedbackAction,
    MethodologyPolicyError,
    OwnerFeedbackRouter,
)
from trading_agent.strategy_research_results import TerminalResearchResult
from trading_agent.strategy_research_statistics import (
    BootstrapPolicy,
    ResamplingMetadataError,
    fixed_seed_resampled_mean,
)
from trading_agent.strategy_research_types import (
    ResearchAgentId,
    SafeTerminalReason,
    TerminalOutcome,
)

NOW = dt.datetime(2026, 8, 19, 20, 5, tzinfo=dt.UTC)
IN_SESSION = dt.datetime(2026, 8, 19, 15, 0, tzinfo=dt.UTC)
AGENTS = tuple(ResearchAgentId)


@pytest.mark.parametrize("agent_id", AGENTS)
def test_methodology_policy_is_first_class_for_each_agent(agent_id: ResearchAgentId) -> None:
    # Given: one of the six independent strategy-research identities.
    # When: its deterministic methodology policy is selected.
    policy = strategy_research_methodology(agent_id)

    # Then: the policy is owned by that identity and carries its full test contract.
    assert policy.agent_id is agent_id
    assert policy.accepted_source_authorities
    assert policy.observation_grammar
    assert policy.predictor_grammar
    assert policy.target_horizon > dt.timedelta(0)
    assert policy.maturity_rule
    assert policy.cost_model_id
    assert policy.baseline_id
    assert policy.next_test_policy


def test_six_policies_are_methodologically_distinct() -> None:
    # Given: all six registered policies.
    policies = tuple(strategy_research_methodology(agent_id) for agent_id in AGENTS)

    # When: their methodology-bearing dimensions are compared.
    dimensions = tuple(
        (
            policy.accepted_source_authorities,
            policy.observation_grammar,
            policy.predictor_grammar,
            policy.target_horizon,
            policy.cadence_key,
            policy.maturity_rule,
            policy.resampling_method,
            policy.baseline_id,
            policy.next_test_policy,
        )
        for policy in policies
    )

    # Then: no identity is an alias for another methodology.
    assert len(set(dimensions)) == len(AGENTS)


@pytest.mark.parametrize(
    ("agent_id", "expected"),
    [
        (ResearchAgentId.INTRADAY_MOMENTUM, ResamplingMethod.SESSION_MOVING_BLOCK),
        (ResearchAgentId.INTRADAY_MEAN_REVERSION, ResamplingMethod.SESSION_MOVING_BLOCK),
        (ResearchAgentId.CATALYST_EVENT, ResamplingMethod.EVENT_CLUSTER),
        (ResearchAgentId.SWING_TREND_REGIME, ResamplingMethod.SESSION_MOVING_BLOCK),
        (ResearchAgentId.CROSS_SECTIONAL_QUANT, ResamplingMethod.DATE_CLUSTER),
        (ResearchAgentId.DERIVATIVES_VOLATILITY, ResamplingMethod.UNDERLYING_MATURITY_CLUSTER),
    ],
)
def test_resampling_matches_methodology_dependence(
    agent_id: ResearchAgentId,
    expected: ResamplingMethod,
) -> None:
    # Given/When: the owner policy preregisters uncertainty handling.
    actual = strategy_research_methodology(agent_id).resampling_method

    # Then: dependent observations are not blindly treated as IID.
    assert actual is expected


def test_authority_mismatch_is_rejected_before_observation() -> None:
    # Given: catalyst input carrying an intraday bar authority.
    input_ = MethodologyObservationInput(
        agent_id=ResearchAgentId.CATALYST_EVENT,
        observed_at=NOW,
        source_receipts=(
            SourceAuthorityReceipt(
                authority="consolidated_completed_bar",
                source_id="bar-001",
                as_of=NOW - dt.timedelta(minutes=5),
                available_at=NOW - dt.timedelta(minutes=4),
                immutable=True,
                complete=True,
            ),
        ),
    )

    # When/Then: policy parsing rejects the wrong source authority.
    with pytest.raises(MethodologyPolicyError, match="source_authority_mismatch"):
        build_methodology_observation(input_)


def test_maturity_and_required_coverage_are_auditable() -> None:
    # Given: a derivatives surface without its required spot/hedge receipt.
    input_ = MethodologyObservationInput(
        agent_id=ResearchAgentId.DERIVATIVES_VOLATILITY,
        observed_at=NOW,
        source_receipts=(
            SourceAuthorityReceipt(
                authority="official_option_surface",
                source_id="surface-001",
                as_of=NOW - dt.timedelta(minutes=5),
                available_at=NOW - dt.timedelta(minutes=1),
                immutable=True,
                complete=True,
            ),
        ),
    )

    # When/Then: incomplete required coverage returns a named waiting reason.
    observation = build_methodology_observation(input_)
    assert observation.ready is False
    assert observation.waiting_reason == "waiting_source_authority:spot_hedge_convention"
    assert observation.matures_at > observation.predictor_available_at


def _terminal(agent_id: ResearchAgentId, outcome: TerminalOutcome) -> TerminalResearchResult:
    reason = {
        TerminalOutcome.SUPPORTED: SafeTerminalReason.PREREGISTERED_SUPPORT_MET,
        TerminalOutcome.REFUTED: SafeTerminalReason.PREREGISTERED_FALSIFICATION_MET,
        TerminalOutcome.INCONCLUSIVE: SafeTerminalReason.INSUFFICIENT_OBSERVATIONS,
    }[outcome]
    return TerminalResearchResult(
        result_id=f"result-{agent_id.value}-{outcome.value}",
        hypothesis_id=f"hypothesis-{agent_id.value}-001",
        owner_agent_id=agent_id,
        outcome=outcome,
        reason_codes=(reason,),
        artifact_refs=("artifact://safe/" + "a" * 64,),
        evaluated_at=NOW,
    )


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (TerminalOutcome.SUPPORTED, FeedbackAction.FUTURE_ONLY_REPLICATION),
        (TerminalOutcome.REFUTED, FeedbackAction.NEW_LINEAGE_METHOD_CHANGE),
        (TerminalOutcome.INCONCLUSIVE, FeedbackAction.WAIT_NAMED_EVIDENCE),
    ],
)
def test_terminal_feedback_changes_only_owners_next_action(
    outcome: TerminalOutcome,
    expected: FeedbackAction,
) -> None:
    # Given: one sanitized terminal result and a feedback router.
    result = _terminal(ResearchAgentId.CATALYST_EVENT, outcome)
    router = OwnerFeedbackRouter((result,))

    # When: the owner and a different identity request feedback.
    owner = router.for_owner(ResearchAgentId.CATALYST_EVENT)
    other = router.for_owner(ResearchAgentId.INTRADAY_MOMENTUM)

    # Then: only the owner receives a policy action and no exact metric field exists.
    assert owner is not None
    assert owner.action is expected
    assert owner.owner_agent_id is ResearchAgentId.CATALYST_EVENT
    assert not hasattr(owner, "holdout_value")
    assert not hasattr(owner, "interval")
    assert other is None


def test_fixture_receipt_is_wiring_only_and_never_profitability_evidence() -> None:
    # Given: an otherwise valid completed-bar fixture receipt.
    input_ = MethodologyObservationInput(
        agent_id=ResearchAgentId.INTRADAY_MOMENTUM,
        observed_at=NOW,
        source_receipts=(
            SourceAuthorityReceipt(
                authority="consolidated_completed_bar",
                source_id="fixture-bar-001",
                as_of=NOW - dt.timedelta(minutes=5),
                available_at=NOW - dt.timedelta(minutes=4),
                immutable=True,
                complete=True,
                wiring_only=True,
            ),
            SourceAuthorityReceipt(
                authority="fresh_actionable_spread",
                source_id="fixture-spread-001",
                as_of=NOW - dt.timedelta(minutes=1),
                available_at=NOW - dt.timedelta(seconds=30),
                immutable=True,
                complete=True,
                wiring_only=True,
            ),
            SourceAuthorityReceipt(
                authority="current_market_session",
                source_id="fixture-session-001",
                as_of=NOW,
                available_at=NOW,
                immutable=True,
                complete=True,
                wiring_only=True,
            ),
        ),
    )

    # When: the methodology observation is built.
    observation = build_methodology_observation(input_)

    # Then: it proves wiring only and cannot carry a profitability claim.
    assert observation.wiring_only is True
    assert observation.profitability_claim is False


def test_duplicate_receipt_and_stale_spread_fail_closed() -> None:
    # Given: duplicate immutable IDs and a separately stale spread observation.
    duplicated = MethodologyObservationInput(
        agent_id=ResearchAgentId.INTRADAY_MOMENTUM,
        observed_at=IN_SESSION,
        source_receipts=tuple(
            SourceAuthorityReceipt(
                authority="consolidated_completed_bar",
                source_id="duplicate-source",
                as_of=IN_SESSION - dt.timedelta(minutes=1),
                available_at=IN_SESSION,
                immutable=True,
                complete=True,
            )
            for _ in range(2)
        ),
    )
    stale = MethodologyObservationInput(
        agent_id=ResearchAgentId.INTRADAY_MOMENTUM,
        observed_at=IN_SESSION,
        source_receipts=(
            SourceAuthorityReceipt(
                "consolidated_completed_bar",
                "bar-001",
                IN_SESSION - dt.timedelta(minutes=5),
                IN_SESSION - dt.timedelta(minutes=4),
                True,
                True,
            ),
            SourceAuthorityReceipt(
                "fresh_actionable_spread",
                "spread-001",
                IN_SESSION - dt.timedelta(minutes=3),
                IN_SESSION - dt.timedelta(minutes=2),
                True,
                True,
            ),
            SourceAuthorityReceipt(
                "current_market_session",
                "session-001",
                IN_SESSION,
                IN_SESSION,
                True,
                True,
            ),
        ),
    )

    # When/Then: replay ambiguity and stale spread both reject with typed reasons.
    with pytest.raises(MethodologyPolicyError, match="source_receipt_duplicate"):
        build_methodology_observation(duplicated)
    with pytest.raises(MethodologyPolicyError, match="intraday_momentum_source_stale:fresh_actionable_spread"):
        build_methodology_observation(stale)


@pytest.mark.parametrize("method", tuple(ResamplingMethod))
def test_preregistered_resampling_is_fixed_seed_deterministic(method: ResamplingMethod) -> None:
    # Given: dependent values, explicit cluster labels, and one frozen seed policy.
    values = (0.01, 0.02, -0.01, -0.02, 0.03, 0.04)
    keys = ("a", "a", "b", "b", "c", "c")
    policy = BootstrapPolicy(repetitions=1_000, seed=17, familywise_alpha=0.05, adjustment_tests=2)

    # When: the exact preregistered uncertainty calculation is replayed.
    first = fixed_seed_resampled_mean(values, policy, method, keys)
    replay = fixed_seed_resampled_mean(values, policy, method, keys)

    # Then: every methodology resampling family is byte-for-byte deterministic.
    assert replay == first


@pytest.mark.parametrize("keys", [(), ("a",), ("a", "", "b", "b", "c", "c")])
def test_resampling_rejects_missing_wrong_or_empty_dependency_keys(keys: tuple[str, ...]) -> None:
    # Given: observations whose dependency metadata is absent or malformed.
    values = (0.01, 0.02, -0.01, -0.02, 0.03, 0.04)
    policy = BootstrapPolicy(repetitions=1_000, seed=17, familywise_alpha=0.05, adjustment_tests=2)

    # When/Then: preregistered resampling fails instead of inventing implicit clusters.
    with pytest.raises(ResamplingMetadataError, match="resampling_cluster_keys_invalid"):
        fixed_seed_resampled_mean(values, policy, ResamplingMethod.DATE_CLUSTER, keys)


def test_session_boundaries_change_moving_block_interval() -> None:
    # Given: identical observations under two materially different session boundaries.
    values = (0.08, 0.07, 0.06, -0.08, -0.07, -0.06, 0.01, -0.01)
    policy = BootstrapPolicy(repetitions=1_000, seed=23, familywise_alpha=0.05, adjustment_tests=1)

    # When: session membership is mutated without changing any value.
    paired = fixed_seed_resampled_mean(
        values,
        policy,
        ResamplingMethod.SESSION_MOVING_BLOCK,
        ("a", "a", "a", "b", "b", "b", "c", "c"),
    )
    interleaved = fixed_seed_resampled_mean(
        values,
        policy,
        ResamplingMethod.SESSION_MOVING_BLOCK,
        ("a", "b", "a", "b", "a", "b", "c", "c"),
    )

    # Then: the interval responds to boundaries and no sampled block crosses a session.
    assert paired != interleaved


@pytest.mark.parametrize("agent_id", AGENTS)
def test_each_policy_rejects_stale_and_mutable_required_authority(agent_id: ResearchAgentId) -> None:
    # Given: complete methodology receipts with the first required authority mutated by trust failure.
    policy = strategy_research_methodology(agent_id)
    valid = tuple(
        SourceAuthorityReceipt(
            authority=authority,
            source_id=f"source-{index}",
            as_of=IN_SESSION,
            available_at=IN_SESSION,
            immutable=True,
            complete=True,
        )
        for index, authority in enumerate(policy.required_source_authorities)
    )
    freshness = dict(policy.freshness_by_authority)
    first = valid[0]
    stale = (
        first.__class__(
            first.authority,
            first.source_id,
            IN_SESSION - freshness[first.authority] - dt.timedelta(microseconds=1),
            IN_SESSION,
            True,
            True,
        ),
        *valid[1:],
    )
    mutable = (
        first.__class__(
            first.authority,
            first.source_id,
            first.as_of,
            first.available_at,
            False,
            True,
        ),
        *valid[1:],
    )

    # When/Then: each methodology reports its owner and exact failing authority.
    with pytest.raises(MethodologyPolicyError, match=f"{agent_id.value}_source_stale:{first.authority}"):
        build_methodology_observation(MethodologyObservationInput(agent_id, IN_SESSION, stale))
    with pytest.raises(MethodologyPolicyError, match=f"{agent_id.value}_source_mutable:{first.authority}"):
        build_methodology_observation(MethodologyObservationInput(agent_id, IN_SESSION, mutable))


def test_checked_in_matrix_cli_reproduces_six_semantic_rows() -> None:
    # Given: the checked-in credential-free matrix generator and one fixed aware timestamp.
    script = Path(__file__).parents[1] / "run_six_strategy_research_matrix.py"
    command = (sys.executable, str(script), "--observed-at", IN_SESSION.isoformat())

    # When: two independent subprocesses regenerate the runtime matrix.
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    replay = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: stdout is deterministic and contains six policy-specific runtime rows.
    assert (first.returncode, replay.returncode) == (0, 0)
    assert replay.stdout == first.stdout
    payload = json.loads(first.stdout)
    assert payload["temporary_store_cleanup"] == "TemporaryDirectory"
    assert payload["runner_calls"] == []
    assert len(payload["six_agents"]) == 6
    assert {row["agent_id"] for row in payload["six_agents"]} == {item.value for item in AGENTS}
    assert all(row["state"] == "waiting_evidence" for row in payload["six_agents"])
    assert all(row["evidence_refs"] and row["next_maturity"] for row in payload["six_agents"])


def test_checked_in_matrix_cli_help_and_bad_timestamp() -> None:
    # Given: the real matrix CLI surface.
    script = Path(__file__).parents[1] / "run_six_strategy_research_matrix.py"

    # When: help and a naive timestamp are invoked as separate subprocesses.
    help_result = subprocess.run((sys.executable, str(script), "--help"), check=False, capture_output=True, text=True)
    bad = subprocess.run(
        (sys.executable, str(script), "--observed-at", "2026-08-19T15:00:00"),
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: help succeeds and bad input fails with safe machine-readable output.
    assert help_result.returncode == 0
    assert bad.returncode == 2
    assert json.loads(bad.stderr) == {
        "broker_mutation": 0,
        "profitability_claim": False,
        "reason": "observation_time_invalid",
        "status": "invalid",
        "trading_mutation": 0,
    }
