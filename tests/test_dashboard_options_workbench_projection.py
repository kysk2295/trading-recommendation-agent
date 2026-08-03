from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from trading_agent.alpaca_option_chain_collection import collect_alpaca_option_chain
from trading_agent.alpaca_option_chain_models import (
    OptionChainRawResponse,
    OptionChainRequest,
    OptionContractType,
    OptionFeed,
)
from trading_agent.alpaca_option_chain_store import AlpacaOptionChainStore
from trading_agent.alpaca_option_contract_collection import collect_alpaca_option_contracts
from trading_agent.alpaca_option_contract_models import (
    OptionContractCatalogRequest,
    OptionContractRawResponse,
)
from trading_agent.alpaca_option_contract_store import AlpacaOptionContractStore
from trading_agent.dashboard_models_v2 import FreshnessV2, SourceStateV2, WorkspaceItemV2
from trading_agent.dashboard_options_workbench_models import OptionsWorkbenchV2
from trading_agent.dashboard_options_workbench_projection import (
    InvalidOptionsWorkbenchProjectionError,
    project_options_workbench,
)
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.intraday_promotion_models import (
    IntradayPromotionAssessment,
    PromotionAssessmentContent,
    PromotionAssessmentStatus,
    assessment_id,
)
from trading_agent.intraday_promotion_store import publish_promotion_assessment

NOW = dt.datetime(2026, 8, 3, tzinfo=dt.UTC)
TRACE_ID = "trace-derivatives"
FIXTURES = Path(__file__).parent / "fixtures"


def test_projection_is_fail_closed_without_canonical_sources(tmp_path: Path) -> None:
    # Given / When
    result = project_options_workbench(outputs=tmp_path / "outputs", now=NOW, derivatives_trace_id=TRACE_ID)

    # Then
    assert result.schema_version == 1
    assert result.selected_view == "market_pulse"
    assert result.market.state == "unavailable"
    assert result.market.blocker_code == "canonical_option_chain_missing"
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


def test_projection_connects_runtime_experiment_and_manual_wait_surfaces(tmp_path: Path) -> None:
    # Given: authoritative six-family, experiment, strategy, and manual-wait projections.
    outputs = tmp_path / "outputs"
    promotion_root = outputs / "promotion_control"
    promotion_root.mkdir(parents=True)
    promotion_root.chmod(0o700)
    assessed_at = NOW + dt.timedelta(hours=14)
    content = PromotionAssessmentContent(
        strategy_version="generated-python:fixture",
        decision_session_date=assessed_at.astimezone(dt.timezone(dt.timedelta(hours=-4))).date(),
        assessed_at=assessed_at,
        target_state=StrategyLifecycleState.SHADOW_CHAMPION,
        evidence_keys=tuple(str(index) * 64 for index in range(1, 7)),
        status=PromotionAssessmentStatus.MANUAL_APPROVAL_PENDING,
        blockers=("manual_approval_required",),
    )
    assessment = IntradayPromotionAssessment(
        assessment_id=assessment_id(content),
        content=content,
    )
    _, created = publish_promotion_assessment(promotion_root, assessment)
    assert created
    agent = _workspace("command_center", "trace.command_center.runtime", 6)
    research = _workspace("research", "trace.research.ledger", 1)
    strategies = _workspace(
        "strategies",
        "trace.strategies.ledger",
        1,
        value="generated-python:fixture · code:abc",
        item_trace="trace.strategies.chain.0.source",
    )

    # When: the integrated workbench snapshot is projected.
    result = project_options_workbench(
        outputs=outputs,
        now=assessed_at,
        derivatives_trace_id=TRACE_ID,
        agent_workspace=agent,
        research_workspace=research,
        strategies_workspace=strategies,
    )

    # Then: the source-backed runtime, experiment trace, and manual gate are no longer placeholders.
    assert (result.agent.state, result.agent.trace_id) == ("populated", "trace.command_center.runtime")
    assert "six-family" in result.agent.summary
    assert (result.experiment.state, result.experiment.trace_id) == ("populated", "trace.research.ledger")
    assert len(result.promotions) == 1
    promotion = result.promotions[0]
    assert promotion.state == "held"
    assert promotion.blockers == ("manual_approval_required",)
    assert (promotion.passed_gate_count, promotion.total_gate_count) == (6, 7)
    assert promotion.trace_id == "trace.strategies.chain.0.source"


def test_projection_exposes_unassessed_strategy_as_held_candidate(tmp_path: Path) -> None:
    strategies = _workspace(
        "strategies",
        "trace.strategies.ledger",
        1,
        value="generated-python:unassessed · code:def",
        item_trace="trace.strategies.chain.1.source",
    )

    result = project_options_workbench(
        outputs=tmp_path / "outputs",
        now=NOW,
        derivatives_trace_id=TRACE_ID,
        strategies_workspace=strategies,
    )

    assert len(result.promotions) == 1
    promotion = result.promotions[0]
    assert promotion.state == "held"
    assert promotion.passed_gate_count == 0
    assert promotion.blockers == ("promotion_assessment_missing",)
    assert promotion.trace_id == "trace.strategies.chain.1.source"


def test_snapshot_binds_workbench_to_option_blocker_trace(tmp_path: Path) -> None:
    # Given / When
    snapshot = collect_dashboard_snapshot_v2(tmp_path / "outputs", now=NOW)
    result = snapshot.workspaces.derivatives.workbench

    # Then
    assert result.chain.trace_id == "trace.derivatives.options"
    assert result.chain.trace_id in {node.node_id for node in snapshot.traces.nodes}
    assert any(
        edge.from_node_id == result.chain.trace_id and edge.to_node_id == f"{result.chain.trace_id}.blocker"
        for edge in snapshot.traces.edges
    )
    assert OptionsWorkbenchV2.model_validate_json(result.model_dump_json()) == result


def test_projection_rejects_naive_time(tmp_path: Path) -> None:
    # Given
    naive = dt.datetime(2026, 8, 3)

    # When / Then
    with pytest.raises(InvalidOptionsWorkbenchProjectionError, match="projection_time_not_aware"):
        project_options_workbench(outputs=tmp_path / "outputs", now=naive, derivatives_trace_id=TRACE_ID)


def _seed_option_stores(outputs: Path, *, observed_at: dt.datetime, feed: OptionFeed) -> None:
    chain_request = OptionChainRequest(
        collection_id="workbench-chain",
        underlying_symbol="AAPL",
        feed=feed,
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    contract_request = OptionContractCatalogRequest(
        collection_id="workbench-contracts",
        underlying_symbol="AAPL",
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    chain_store = AlpacaOptionChainStore(outputs / "derivatives" / "option-chain.sqlite3")
    contract_store = AlpacaOptionContractStore(outputs / "derivatives" / "option-contracts.sqlite3")
    chain_store.preflight_write()
    contract_store.preflight_write()
    _ = collect_alpaca_option_chain(
        _ChainFetcher(observed_at),
        chain_store,
        chain_request,
        _clock=iter((observed_at - dt.timedelta(seconds=2), observed_at)).__next__,
    )
    _ = collect_alpaca_option_contracts(
        _ContractFetcher(observed_at),
        contract_store,
        contract_request,
        _clock=iter((observed_at - dt.timedelta(seconds=2), observed_at)).__next__,
    )


class _ChainFetcher:
    def __init__(self, observed_at: dt.datetime) -> None:
        self._observed_at = observed_at

    def fetch_page(
        self,
        request: OptionChainRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionChainRawResponse:
        quote_at = (self._observed_at - dt.timedelta(seconds=30)).isoformat().replace("+00:00", "Z").encode()
        return OptionChainRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=self._observed_at - dt.timedelta(seconds=1),
            status_code=200,
            content_type="application/json",
            raw_payload=(FIXTURES / "alpaca_option_chain" / "page-001.json")
            .read_bytes()
            .replace(b"2026-07-23T14:31:00Z", quote_at),
        )


class _ContractFetcher:
    def __init__(self, observed_at: dt.datetime) -> None:
        self._observed_at = observed_at

    def fetch_page(
        self,
        request: OptionContractCatalogRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionContractRawResponse:
        return OptionContractRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=self._observed_at - dt.timedelta(seconds=1),
            status_code=200,
            content_type="application/json",
            raw_payload=(FIXTURES / "alpaca_option_contract" / "page-001.json").read_bytes(),
        )


def _workspace(
    name: str,
    trace_id: str,
    count: int,
    *,
    value: str = "ready",
    item_trace: str | None = None,
) -> SourceStateV2:
    return SourceStateV2(
        state="populated",
        observed_at=NOW,
        freshness=FreshnessV2(policy_id=f"{name}-fixture", age_seconds=0, as_of=NOW),
        blocker_code=None,
        summary=f"{name} ready",
        total_count=count,
        projected_count=count,
        truncated=False,
        trace_id=trace_id,
        items=tuple(
            WorkspaceItemV2(
                item_id=f"{name}.{index}",
                kind="strategy" if name == "strategies" else "system",
                label=f"{name} {index}",
                state="populated",
                value=value,
                observed_at=NOW,
                trace_id=item_trace or trace_id,
            )
            for index in range(count)
        ),
    )
