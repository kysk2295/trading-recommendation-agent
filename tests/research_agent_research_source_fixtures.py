from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tests.dashboard_projection_experiment_fixture import append_reviewer_and_lifecycle, complete_experiment_outputs
from tests.test_cftc_tff_parser import FIXTURE as CFTC_FIXTURE
from tests.test_futures_roll_security_master_cli import _manifest
from tests.test_swing_shadow_reviewer import _completed_trial
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
from trading_agent.dashboard_derivatives_current_quote import CurrentOptionQuoteAuthority
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.futures_roll_security_master import load_futures_roll_security_master
from trading_agent.research_agent_sources import ResearchAgentSourcePaths
from trading_agent.swing_shadow_review_store import SwingShadowReviewStore
from trading_agent.swing_shadow_reviewer import review_swing_shadow_trial

NOW = dt.datetime(2026, 7, 24, 14, 35, tzinfo=dt.UTC)
_TESTS = Path(__file__).parent


def populated_source_paths(tmp_path: Path) -> ResearchAgentSourcePaths:
    outputs = complete_experiment_outputs(tmp_path)
    append_reviewer_and_lifecycle(outputs)
    experiments, shadow, signal, terminal = _completed_trial(outputs / "swing")
    reviews = SwingShadowReviewStore(outputs / "swing" / "review.sqlite3")
    _ = review_swing_shadow_trial(
        experiment_ledger=ExperimentLedgerReader(experiments.path),
        shadow_ledger=shadow,
        reviews=reviews,
        signal_id=signal.signal_id,
        reviewed_at=terminal.observed_at + dt.timedelta(minutes=2),
    )
    _seed_derivatives(outputs)
    return ResearchAgentSourcePaths(
        outputs_root=outputs,
        market_context_root=outputs / "market-context",
        day_session_root=outputs / "live-sessions",
        swing_shadow_database=shadow.path,
        swing_review_database=reviews.path,
        experiment_ledger=outputs / "experiment_control" / "experiment_ledger.sqlite3",
        lane_review_database=outputs / "lane_control" / "lane_review.sqlite3",
    )


def _seed_derivatives(outputs: Path) -> None:
    outputs.chmod(0o700)
    derivatives = outputs / "derivatives"
    chain_request = OptionChainRequest(
        collection_id="research-inspection-chain",
        underlying_symbol="AAPL",
        feed=OptionFeed.OPRA,
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    catalog_request = OptionContractCatalogRequest(
        collection_id="research-inspection-catalog",
        underlying_symbol="AAPL",
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    chain_store = AlpacaOptionChainStore(derivatives / "option-chain.sqlite3")
    catalog_store = AlpacaOptionContractStore(derivatives / "option-contracts.sqlite3")
    chain_store.preflight_write()
    catalog_store.preflight_write()
    chain = collect_alpaca_option_chain(
        _ChainFixtureFetcher(),
        chain_store,
        chain_request,
        _clock=iter((NOW - dt.timedelta(seconds=2), NOW)).__next__,
    ).run
    catalog = collect_alpaca_option_contracts(
        _CatalogFixtureFetcher(),
        catalog_store,
        catalog_request,
        _clock=iter((NOW - dt.timedelta(seconds=2), NOW)).__next__,
    ).run
    authority = CurrentOptionQuoteAuthority(
        chain_run_id=chain.run_id,
        catalog_run_id=catalog.run_id,
        entitlement="active_realtime",
        redistribution="allowed",
        capability_health="healthy",
        capability_observed_at=NOW,
        capability_ttl_seconds=60,
        quote_observed_at=NOW - dt.timedelta(seconds=30),
        quote_ttl_seconds=60,
        safe_ref="c" * 64,
    )
    authority_path = derivatives / f"option_current_authority_{authority.authority_id}.json"
    authority_path.write_text(canonical_experiment_ledger_json(authority) + "\n", encoding="utf-8")
    authority_path.chmod(0o600)
    manifest = _manifest()
    observed_at = (NOW - dt.timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    manifest["source_observed_at"] = observed_at
    for contract in manifest["contracts"]:
        contract["observed_at"] = observed_at
    manifest_path = outputs / "research-inspection-futures.json"
    manifest_path.write_text(json.dumps(manifest, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    manifest_path.chmod(0o600)
    master = load_futures_roll_security_master(manifest_path)
    master_path = derivatives / "futures-roll-security-master.v1.json"
    master_path.write_text(master.model_dump_json(), encoding="utf-8")
    master_path.chmod(0o600)
    request = CftcTffRequest(
        collection_id="research-inspection-cftc",
        contract_market_code="13874A",
        through_date=dt.date(2026, 7, 24),
    )
    store = CftcTffStore(outputs / "source_evidence" / "cftc-tff.sqlite3")
    store.preflight_write()
    _ = collect_cftc_tff(
        _CftcFixtureFetcher(),
        store,
        request,
        _clock=iter((NOW - dt.timedelta(seconds=2), NOW - dt.timedelta(seconds=1))).__next__,
    )


class _ChainFixtureFetcher:
    def fetch_page(
        self,
        request: OptionChainRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionChainRawResponse:
        raw = (_TESTS / "fixtures" / "alpaca_option_chain" / "page-001.json").read_bytes()
        quote_at = (NOW - dt.timedelta(seconds=30)).isoformat().replace("+00:00", "Z").encode()
        return OptionChainRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=NOW - dt.timedelta(seconds=1),
            status_code=200,
            content_type="application/json",
            raw_payload=raw.replace(b"2026-07-23T14:31:00Z", quote_at),
        )


class _CatalogFixtureFetcher:
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
            received_at=NOW - dt.timedelta(seconds=1),
            status_code=200,
            content_type="application/json",
            raw_payload=(_TESTS / "fixtures" / "alpaca_option_contract" / "page-001.json").read_bytes(),
        )


class _CftcFixtureFetcher:
    def fetch(self, request: CftcTffRequest) -> CftcTffRawResponse:
        return CftcTffRawResponse(
            request_id=request.request_id,
            received_at=NOW - dt.timedelta(seconds=1),
            status_code=200,
            content_type="application/json",
            raw_payload=CFTC_FIXTURE.read_bytes(),
        )
