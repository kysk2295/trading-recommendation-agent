from __future__ import annotations

import json

from trading_agent import researcher_llm
from trading_agent.lane_identity_models import LaneId
from trading_agent.researcher_agent import FailureDigest, ResearcherContext
from trading_agent.researcher_llm import LlmHypothesisDraft


def test_llm_draft_canonicalizes_set_like_sequences() -> None:
    # Given: an external completion with duplicated, unordered set-like values.
    payload = {
        "hypothesis_id": "candidate-v1",
        "hypothesis": "A measurable completed-bar effect exists.",
        "falsification_rule": "Reject when the registered threshold fails.",
        "cited_source_ids": ["source-b", "source-a", "source-b"],
        "economic_mechanism": "The cited claims motivate the effect.",
        "counterfactual_baseline": "existing_approved_strategy",
        "strategy_source": "return bars[index]",
        "free_parameters": ["window", "threshold", "window"],
    }

    # When: the response crosses the Pydantic boundary.
    draft = LlmHypothesisDraft.model_validate(payload)

    # Then: internal tuples have one deterministic canonical representation.
    assert draft.cited_source_ids == ("source-a", "source-b")
    assert draft.free_parameters == ("threshold", "window")


def test_researcher_prompt_exposes_the_critic_parameter_ceiling() -> None:
    # Given: a Researcher context subject to the deterministic Critic.
    context = ResearcherContext(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        sources=(),
        failure_digest=FailureDigest((), (), ()),
        regime_context="regular_session_high_liquidity",
        existing_hypothesis_texts=(),
    )

    # When: the generator serializes its machine constraints.
    contract = json.loads(researcher_llm._prompt(context))["contract"]

    # Then: candidates receive the same hard parameter ceiling used downstream.
    assert contract["maximum_free_parameters"] == 4
    assert contract["strategy_entrypoint"] == {
        "factory": "create_strategy(context)",
        "method": "observe(bar, candidate)",
    }
