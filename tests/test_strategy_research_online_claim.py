from __future__ import annotations

import pytest

from trading_agent.strategy_research_policy import MethodologyPolicyError
from trading_agent.strategy_research_science_kernel import (
    validate_market_time_series_online_claim,
)


def test_online_error_control_claim_requires_separate_validated_time_series_evaluator() -> None:
    with pytest.raises(MethodologyPolicyError, match="e_value_evaluator_validation_required"):
        validate_market_time_series_online_claim(
            claimed=True,
            evaluator_version=None,
            validation_artifact_ref=None,
        )

    validate_market_time_series_online_claim(
        claimed=True,
        evaluator_version="market-time-series-evalue-v1",
        validation_artifact_ref=f"artifact://safe/{'a' * 64}",
    )
