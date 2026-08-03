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
from trading_agent.dashboard_options_workbench_models import OptionsWorkbenchV2
from trading_agent.dashboard_options_workbench_projection import (
    InvalidOptionsWorkbenchProjectionError,
    project_options_workbench,
)

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


def test_projection_preserves_one_trace_and_roundtrips(tmp_path: Path) -> None:
    # Given / When
    result = project_options_workbench(outputs=tmp_path / "outputs", now=NOW, derivatives_trace_id=TRACE_ID)

    # Then
    assert {result.market.trace_id, result.chain.trace_id, result.agent.trace_id, result.experiment.trace_id} == {
        TRACE_ID
    }
    assert OptionsWorkbenchV2.model_validate_json(result.model_dump_json()) == result


def test_projection_rejects_naive_time(tmp_path: Path) -> None:
    # Given
    naive = dt.datetime(2026, 8, 3)

    # When / Then
    with pytest.raises(InvalidOptionsWorkbenchProjectionError, match="projection_time_not_aware"):
        project_options_workbench(outputs=tmp_path / "outputs", now=naive, derivatives_trace_id=TRACE_ID)


def test_projection_shows_actual_indicative_private_store_quotes_as_research_only(
    tmp_path: Path,
) -> None:
    # Given a successful matching indicative chain and contract catalog in private stores
    observed_at = dt.datetime(2026, 7, 23, 14, 32, tzinfo=dt.UTC)
    outputs = tmp_path / "outputs"
    _seed_option_stores(outputs, observed_at=observed_at, feed=OptionFeed.INDICATIVE)

    # When the workbench projects the stored research evidence
    result = project_options_workbench(
        outputs=outputs,
        now=observed_at + dt.timedelta(minutes=1),
        derivatives_trace_id=TRACE_ID,
    )

    # Then it preserves the canonical cell fields but cannot make the quote selectable
    assert result.market.state == result.chain.state == "blocked"
    assert result.market.blocker_code == result.chain.blocker_code == "indicative_research_only"
    assert result.chain.selected_expiration == "2026-07-24"
    assert result.chain.expirations == ("2026-07-24",)
    assert result.chain.total_count == result.chain.projected_count == 1
    cell = result.chain.rows[0].call
    assert cell is not None
    assert (cell.contract_id, cell.provider, cell.side) == (
        "alpaca:6e58f870-fe73-4583-81e4-b9a37892c36f",
        "alpaca",
        "call",
    )
    assert (result.chain.rows[0].strike, cell.bid, cell.ask) == (
        "200",
        "5",
        "5.2",
    )
    assert cell.observed_at == observed_at - dt.timedelta(seconds=30)
    assert cell.trace_id == TRACE_ID
    assert cell.state == "indicative"
    assert cell.selectable is False


def test_projection_blocks_unlicensed_opra_without_rows(tmp_path: Path) -> None:
    # Given matching OPRA private stores without a redistribution authority receipt
    observed_at = dt.datetime(2026, 7, 23, 14, 32, tzinfo=dt.UTC)
    outputs = tmp_path / "outputs"
    _seed_option_stores(outputs, observed_at=observed_at, feed=OptionFeed.OPRA)

    # When the workbench reads the latest matching scope
    result = project_options_workbench(
        outputs=outputs,
        now=observed_at + dt.timedelta(minutes=1),
        derivatives_trace_id=TRACE_ID,
    )

    # Then no current quote is surfaced or selectable
    assert result.market.state == result.chain.state == "blocked"
    assert result.market.blocker_code == result.chain.blocker_code == "current_quote_not_licensed"
    assert result.chain.rows == ()
    assert result.chain.total_count == result.chain.projected_count == 0


@pytest.mark.parametrize(
    ("now_offset", "chain_mode", "state", "blocker"),
    (
        (dt.timedelta(days=1), 0o600, "stale", "derivative_surface_stale"),
        (dt.timedelta(minutes=1), 0o644, "corrupt", "derivatives_source_invalid"),
    ),
)
def test_projection_fails_closed_for_stale_or_corrupt_private_stores(
    tmp_path: Path,
    now_offset: dt.timedelta,
    chain_mode: int,
    state: str,
    blocker: str,
) -> None:
    # Given a matching indicative store that is stale or fails its private-store validation
    observed_at = dt.datetime(2026, 7, 23, 14, 32, tzinfo=dt.UTC)
    outputs = tmp_path / "outputs"
    _seed_option_stores(outputs, observed_at=observed_at, feed=OptionFeed.INDICATIVE)
    (outputs / "derivatives" / "option-chain.sqlite3").chmod(chain_mode)

    # When / Then
    result = project_options_workbench(outputs=outputs, now=observed_at + now_offset, derivatives_trace_id=TRACE_ID)
    assert (result.chain.state, result.chain.blocker_code, result.chain.rows) == (state, blocker, ())
    assert result.chain.trace_id == TRACE_ID


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
