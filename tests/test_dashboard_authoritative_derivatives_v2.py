from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tests.test_cftc_tff_parser import FIXTURE as CFTC_FIXTURE
from tests.test_futures_roll_security_master_cli import _manifest
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
from trading_agent.cftc_tff_collection import collect_cftc_tff
from trading_agent.cftc_tff_models import CftcTffRawResponse, CftcTffRequest
from trading_agent.cftc_tff_store import CftcTffStore
from trading_agent.dashboard_derivatives_futures import FUTURES_MASTER_FILE, read_futures_section
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.futures_roll_security_master import load_futures_roll_security_master

NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)


def test_option_stores_do_not_grant_current_redistribution(tmp_path: Path) -> None:
    # Given complete typed Alpaca option chain and contract stores
    outputs = tmp_path / "outputs"
    chain_request = OptionChainRequest(
        collection_id="dashboard-chain",
        underlying_symbol="AAPL",
        feed=OptionFeed.INDICATIVE,
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    contract_request = OptionContractCatalogRequest(
        collection_id="dashboard-contracts",
        underlying_symbol="AAPL",
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    chain_store = AlpacaOptionChainStore(outputs / "derivatives" / "option-chain.sqlite3")
    contract_store = AlpacaOptionContractStore(
        outputs / "derivatives" / "option-contracts.sqlite3"
    )
    chain_store.preflight_write()
    contract_store.preflight_write()
    _ = collect_alpaca_option_chain(
        _OptionChainFetcher(
            (Path(__file__).parent / "fixtures/alpaca_option_chain/page-001.json").read_bytes()
        ),
        chain_store,
        chain_request,
        _clock=iter((NOW - dt.timedelta(minutes=2), NOW)).__next__,
    )
    _ = collect_alpaca_option_contracts(
        _OptionContractFetcher(
            (Path(__file__).parent / "fixtures/alpaca_option_contract/page-001.json").read_bytes()
        ),
        contract_store,
        contract_request,
        _clock=iter((NOW - dt.timedelta(minutes=2), NOW)).__next__,
    )

    # When derivatives and source capability projections read those stores
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then research evidence is visible but current redistribution remains blocked
    derivatives = snapshot.workspaces.derivatives
    assert derivatives.blocker_code == "current_quote_not_licensed"
    assert any(item.item_id.startswith("derivative.option.") for item in derivatives.items)
    alpaca = next(
        capability
        for capability in snapshot.workspaces.data_sources.capabilities
        if capability.provider == "alpaca"
    )
    assert alpaca.state == "blocked"
    assert alpaca.entitlement == "research_only"


def test_futures_master_and_cftc_project_with_explicit_currentness(tmp_path: Path) -> None:
    # Given a private typed futures master and successful CFTC terminal
    outputs = tmp_path / "outputs"
    derivatives = outputs / "derivatives"
    source_evidence = outputs / "source_evidence"
    derivatives.mkdir(parents=True)
    source_evidence.mkdir(parents=True)
    manifest_path = tmp_path / "futures-source.json"
    manifest_path.write_text(
        json.dumps(_manifest(), separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    master = load_futures_roll_security_master(manifest_path)
    master_path = derivatives / FUTURES_MASTER_FILE
    master_path.write_text(master.model_dump_json(), encoding="utf-8")
    master_path.chmod(0o600)
    request = CftcTffRequest(
        collection_id="dashboard-cftc",
        contract_market_code="13874A",
        through_date=dt.date(2026, 7, 24),
    )
    store = CftcTffStore(source_evidence / "cftc-tff.sqlite3")
    store.preflight_write()
    _ = collect_cftc_tff(
        _CftcFetcher(CFTC_FIXTURE.read_bytes()),
        store,
        request,
        _clock=iter((NOW - dt.timedelta(minutes=2), NOW - dt.timedelta(minutes=1))).__next__,
    )

    # When the derivatives workspace reads both native authorities
    futures = read_futures_section(outputs, NOW)
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then futures and positioning are projected while stale CFTC age fails closed
    section = snapshot.workspaces.derivatives
    assert futures.blocker_code == "cftc_report_stale"
    assert any(item.item_id.startswith("derivative.future.") for item in section.items)
    assert any(item.item_id == "derivative.cftc.positioning" for item in section.items)

class _OptionChainFetcher:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def fetch_page(
        self,
        request: OptionChainRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionChainRawResponse:
        return OptionChainRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=NOW - dt.timedelta(minutes=1),
            status_code=200,
            content_type="application/json",
            raw_payload=self._raw,
        )


class _OptionContractFetcher:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

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
            received_at=NOW - dt.timedelta(minutes=1),
            status_code=200,
            content_type="application/json",
            raw_payload=self._raw,
        )


class _CftcFetcher:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def fetch(self, request: CftcTffRequest) -> CftcTffRawResponse:
        return CftcTffRawResponse(
            request_id=request.request_id,
            received_at=NOW - dt.timedelta(minutes=1),
            status_code=200,
            content_type="application/json",
            raw_payload=self._raw,
        )
