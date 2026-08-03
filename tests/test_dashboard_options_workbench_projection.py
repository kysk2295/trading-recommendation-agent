from __future__ import annotations

import datetime as dt

import pytest

from trading_agent.dashboard_options_workbench_models import OptionsWorkbenchV2
from trading_agent.dashboard_options_workbench_projection import (
    InvalidOptionsWorkbenchProjectionError,
    project_options_workbench,
)

NOW = dt.datetime(2026, 8, 3, tzinfo=dt.UTC)
TRACE_ID = "trace-derivatives"


def test_projection_is_fail_closed_without_canonical_sources() -> None:
    # Given / When
    result = project_options_workbench(now=NOW, derivatives_trace_id=TRACE_ID)

    # Then
    assert result.schema_version == 1
    assert result.selected_view == "market_pulse"
    assert result.market.state == "unavailable"
    assert result.market.blocker_code == "canonical_option_market_missing"
    assert result.chain.state == "unavailable"
    assert result.chain.blocker_code == "canonical_option_chain_missing"
    assert result.chain.underlying is None
    assert result.chain.selected_expiration is None
    assert result.chain.expirations == result.chain.rows == ()
    assert result.chain.total_count == result.chain.projected_count == 0
    assert result.chain.truncated is False
    assert result.scenario is None
    assert result.agent.state == "unavailable"
    assert result.agent.blocker_code == "derivatives_agent_receipt_missing"
    assert result.experiment.state == "unavailable"
    assert result.experiment.blocker_code == "options_experiment_missing"
    assert result.promotions == ()


def test_projection_preserves_one_trace_and_roundtrips() -> None:
    # Given / When
    result = project_options_workbench(now=NOW, derivatives_trace_id=TRACE_ID)

    # Then
    assert {result.market.trace_id, result.chain.trace_id, result.agent.trace_id, result.experiment.trace_id} == {
        TRACE_ID
    }
    assert OptionsWorkbenchV2.model_validate_json(result.model_dump_json()) == result


def test_projection_rejects_naive_time() -> None:
    # Given
    naive = dt.datetime(2026, 8, 3)

    # When / Then
    with pytest.raises(InvalidOptionsWorkbenchProjectionError, match="projection_time_not_aware"):
        project_options_workbench(now=naive, derivatives_trace_id=TRACE_ID)
