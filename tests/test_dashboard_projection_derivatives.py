from __future__ import annotations

import datetime as dt
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
from trading_agent.dashboard_derivatives_current_quote import (
    CurrentOptionQuoteAuthority,
    read_current_option_quotes,
)
from trading_agent.dashboard_derivatives_section import DerivativesSection
from trading_agent.dashboard_projection_derivatives import project_derivatives
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)


def test_corrupt_derivatives_authority_wins_over_missing_section(tmp_path: Path) -> None:
    # Given a missing option authority and a corrupt futures master
    root = tmp_path / "outputs" / "derivatives"
    root.mkdir(parents=True)
    master = root / "futures-roll-security-master.v1.json"
    master.write_text("{", encoding="utf-8")
    master.chmod(0o600)

    # When the read-only workspace projection resolves section state
    projection = project_derivatives(tmp_path / "outputs", now=NOW)

    # Then the corrupt authority and its blocker take precedence
    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == "futures_master_invalid"


def test_derivatives_projection_is_bounded_to_schema_cap(tmp_path: Path) -> None:
    # Given no authoritative derivatives artifacts
    outputs = tmp_path / "outputs"

    # When the workspace projection is built
    projection = project_derivatives(outputs, now=NOW)

    # Then its collection metadata is exact and bounded
    assert projection.workspace.projected_count == len(projection.workspace.items)
    assert projection.workspace.projected_count <= 24
    assert projection.workspace.truncated is (projection.workspace.total_count > projection.workspace.projected_count)


def test_corrupt_volatility_artifact_fails_closed(tmp_path: Path) -> None:
    # Given a malformed content-addressed IV surface artifact
    root = tmp_path / "outputs" / "derivatives"
    root.mkdir(parents=True)
    artifact = root / f"option_surface_{'a' * 64}.json"
    artifact.write_text("{", encoding="utf-8")
    artifact.chmod(0o600)

    # When the derivatives projection reads the artifact
    projection = project_derivatives(tmp_path / "outputs", now=NOW)

    # Then corrupt volatility authority outranks other missing sections
    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == "options_receipt_invalid"


def test_current_quote_requires_bound_healthy_realtime_authority(tmp_path: Path) -> None:
    # Given OPRA chain/catalog stores plus an exact fresh redistribution authority receipt
    authority_quote_at = dt.datetime(2026, 7, 23, 14, 31, tzinfo=dt.UTC)
    section = _licensed_quote_section(tmp_path, quote_at=authority_quote_at)

    # When the read-only current quote section is projected
    # Then the bound fresh quote is current and every authority conjunct is explicit
    assert section.state == "populated"
    assert {item.value for item in section.items[:4]} == {
        "entitlement:active_realtime",
        "redistribution:allowed",
        "capability:healthy_current",
        "quote:fresh",
    }
    assert any(item.item_id.startswith("derivative.quote.") for item in section.items[4:])


def test_fresh_authority_never_launders_stale_underlying_quote(tmp_path: Path) -> None:
    # Given a fresh licensed authority whose exact chain contains an older quote timestamp
    stale_quote_at = dt.datetime(2026, 7, 23, 14, 20, tzinfo=dt.UTC)
    section = _licensed_quote_section(tmp_path, quote_at=stale_quote_at)

    # When the current quote section binds the payload to its authority receipt
    # Then the mismatched stale quote is research-only and no quote value is emitted
    assert section.state == "blocked"
    assert section.blocker_code == "current_quote_source_mismatch"
    assert section.items == ()


def test_indicative_chain_is_explicit_research_only_evidence(tmp_path: Path) -> None:
    # Given a successful bounded Alpaca indicative chain and matching contract catalog
    outputs = tmp_path / "outputs"
    observed_at = dt.datetime(2026, 7, 23, 14, 35, tzinfo=dt.UTC)
    seed_indicative_options(outputs, observed_at)

    # When the derivatives workspace projects the free feed without OPRA authority
    projection = project_derivatives(outputs, now=observed_at + dt.timedelta(minutes=1))

    # Then the data is usable only as a populated non-OPRA research shadow
    assert projection.workspace.state == "blocked"
    assert projection.workspace.blocker_code == "indicative_research_only"
    assert projection.workspace.projected_count > 0


def seed_indicative_options(outputs: Path, observed_at: dt.datetime) -> None:
    chain_request = OptionChainRequest(
        collection_id="indicative-research-chain",
        underlying_symbol="AAPL",
        feed=OptionFeed.INDICATIVE,
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    catalog_request = OptionContractCatalogRequest(
        collection_id="indicative-research-catalog",
        underlying_symbol="AAPL",
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    chain_store = AlpacaOptionChainStore(
        outputs / "derivatives" / "option-chain.sqlite3"
    )
    catalog_store = AlpacaOptionContractStore(
        outputs / "derivatives" / "option-contracts.sqlite3"
    )
    chain_store.preflight_write()
    catalog_store.preflight_write()
    _ = collect_alpaca_option_chain(
        _ChainFetcher(observed_at, observed_at - dt.timedelta(minutes=15)),
        chain_store,
        chain_request,
        _clock=iter((observed_at - dt.timedelta(seconds=2), observed_at)).__next__,
    )
    _ = collect_alpaca_option_contracts(
        _CatalogFetcher(observed_at),
        catalog_store,
        catalog_request,
        _clock=iter((observed_at - dt.timedelta(seconds=2), observed_at)).__next__,
    )


def _licensed_quote_section(tmp_path: Path, *, quote_at: dt.datetime) -> DerivativesSection:
    observed_at = dt.datetime(2026, 7, 23, 14, 31, 30, tzinfo=dt.UTC)
    outputs = tmp_path / "outputs"
    chain_request = OptionChainRequest(
        collection_id="current-chain",
        underlying_symbol="AAPL",
        feed=OptionFeed.OPRA,
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    catalog_request = OptionContractCatalogRequest(
        collection_id="current-catalog",
        underlying_symbol="AAPL",
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    chain_store = AlpacaOptionChainStore(outputs / "derivatives" / "option-chain.sqlite3")
    catalog_store = AlpacaOptionContractStore(outputs / "derivatives" / "option-contracts.sqlite3")
    chain_store.preflight_write()
    catalog_store.preflight_write()
    chain = collect_alpaca_option_chain(
        _ChainFetcher(observed_at, quote_at),
        chain_store,
        chain_request,
        _clock=iter((observed_at - dt.timedelta(seconds=2), observed_at)).__next__,
    )
    catalog = collect_alpaca_option_contracts(
        _CatalogFetcher(observed_at),
        catalog_store,
        catalog_request,
        _clock=iter((observed_at - dt.timedelta(seconds=2), observed_at)).__next__,
    )
    authority = CurrentOptionQuoteAuthority(
        chain_run_id=chain.run.run_id,
        catalog_run_id=catalog.run.run_id,
        entitlement="active_realtime",
        redistribution="allowed",
        capability_health="healthy",
        capability_observed_at=observed_at,
        capability_ttl_seconds=60,
        quote_observed_at=observed_at - dt.timedelta(seconds=30),
        quote_ttl_seconds=60,
        safe_ref="c" * 64,
    )
    authority_path = outputs / "derivatives" / f"option_current_authority_{authority.authority_id}.json"
    authority_path.write_text(
        canonical_experiment_ledger_json(authority) + "\n",
        encoding="utf-8",
    )
    authority_path.chmod(0o600)

    return read_current_option_quotes(outputs, observed_at)


class _ChainFetcher:
    def __init__(self, observed_at: dt.datetime, quote_at: dt.datetime) -> None:
        self._observed_at = observed_at
        self._quote_at = quote_at

    def fetch_page(
        self,
        request: OptionChainRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionChainRawResponse:
        payload = (Path(__file__).parent / "fixtures/alpaca_option_chain/page-001.json").read_bytes()
        quote_at = self._quote_at.isoformat().replace("+00:00", "Z").encode()
        return OptionChainRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=self._observed_at - dt.timedelta(seconds=1),
            status_code=200,
            content_type="application/json",
            raw_payload=payload.replace(b"2026-07-23T14:31:00Z", quote_at),
        )


class _CatalogFetcher:
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
            raw_payload=(Path(__file__).parent / "fixtures/alpaca_option_contract/page-001.json").read_bytes(),
        )
