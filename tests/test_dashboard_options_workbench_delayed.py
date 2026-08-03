from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

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
from trading_agent.dashboard_options_workbench_projection import project_options_workbench

NOW = dt.datetime(2026, 8, 3, 15, tzinfo=dt.UTC)
FIXTURES = Path(__file__).parent / "fixtures"


def test_projection_merges_latest_indicative_calls_and_puts_for_research(tmp_path: Path) -> None:
    # Given: fresh call and put receipts for one underlying, expiration, and strike.
    outputs = tmp_path / "outputs"
    _seed_side(outputs, OptionContractType.CALL)
    _seed_side(outputs, OptionContractType.PUT)

    # When: the Workbench projects the latest bounded delayed research scope.
    result = project_options_workbench(
        outputs=outputs,
        now=NOW + dt.timedelta(minutes=1),
        derivatives_trace_id="trace-derivatives",
    )

    # Then: calls and puts share one selectable research-only strike row.
    assert result.chain.state == "populated"
    assert result.chain.blocker_code is None
    assert result.chain.total_count == result.chain.projected_count == 1
    assert len(result.chain.rows) == 1
    row = result.chain.rows[0]
    assert row.call is not None and row.put is not None
    assert row.call.selectable is row.put.selectable is True
    assert row.call.state == row.put.state == "indicative"


def _seed_side(outputs: Path, contract_type: OptionContractType) -> None:
    chain_request = OptionChainRequest(
        collection_id=f"delayed-chain-{contract_type.value}",
        underlying_symbol="AAPL",
        feed=OptionFeed.INDICATIVE,
        expiration_date=dt.date(2026, 7, 24),
        contract_type=contract_type,
        limit=100,
        max_pages=2,
    )
    catalog_request = OptionContractCatalogRequest(
        collection_id=f"delayed-catalog-{contract_type.value}",
        underlying_symbol="AAPL",
        expiration_date=dt.date(2026, 7, 24),
        contract_type=contract_type,
        limit=100,
        max_pages=2,
    )
    chain_store = AlpacaOptionChainStore(outputs / "derivatives" / "option-chain.sqlite3")
    catalog_store = AlpacaOptionContractStore(outputs / "derivatives" / "option-contracts.sqlite3")
    chain_store.preflight_write()
    catalog_store.preflight_write()
    _ = collect_alpaca_option_chain(
        _ChainFetcher(contract_type),
        chain_store,
        chain_request,
        _clock=iter((NOW - dt.timedelta(seconds=2), NOW)).__next__,
    )
    _ = collect_alpaca_option_contracts(
        _ContractFetcher(contract_type),
        catalog_store,
        catalog_request,
        _clock=iter((NOW - dt.timedelta(seconds=2), NOW)).__next__,
    )


@dataclass(frozen=True, slots=True)
class _ChainFetcher:
    contract_type: OptionContractType

    def fetch_page(
        self,
        request: OptionChainRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionChainRawResponse:
        payload = (FIXTURES / "alpaca_option_chain" / "page-001.json").read_bytes()
        if self.contract_type is OptionContractType.PUT:
            payload = payload.replace(b"C00200000", b"P00200000")
        return OptionChainRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=NOW - dt.timedelta(seconds=1),
            status_code=200,
            content_type="application/json",
            raw_payload=payload.replace(b"2026-07-23T14:31:00Z", b"2026-08-03T14:59:30Z"),
        )


@dataclass(frozen=True, slots=True)
class _ContractFetcher:
    contract_type: OptionContractType

    def fetch_page(
        self,
        request: OptionContractCatalogRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionContractRawResponse:
        payload = (FIXTURES / "alpaca_option_contract" / "page-001.json").read_bytes()
        if self.contract_type is OptionContractType.PUT:
            payload = (
                payload.replace(b"6e58f870-fe73-4583-81e4-b9a37892c36f", b"7e58f870-fe73-4583-81e4-b9a37892c36f")
                .replace(b"C00200000", b"P00200000")
                .replace(b"200 Call", b"200 Put")
                .replace(b'"type":"call"', b'"type":"put"')
            )
        return OptionContractRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=NOW - dt.timedelta(seconds=1),
            status_code=200,
            content_type="application/json",
            raw_payload=payload,
        )
