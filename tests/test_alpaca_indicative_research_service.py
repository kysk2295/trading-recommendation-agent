from __future__ import annotations

import datetime as dt
import plistlib
import shutil
import stat
from pathlib import Path

import run_alpaca_indicative_research as service_cli
from trading_agent.alpaca_indicative_research import (
    IndicativeResearchCollection,
    IndicativeResearchPlan,
    collect_indicative_research,
    indicative_research_requests,
    plan_indicative_research,
)
from trading_agent.alpaca_indicative_research_service_config import (
    load_indicative_research_service_config,
    verify_indicative_research_launch_agent,
)
from trading_agent.alpaca_option_chain_models import (
    OptionChainRawResponse,
    OptionChainRequest,
    OptionContractType,
)
from trading_agent.alpaca_option_contract_collection import collect_alpaca_option_contracts
from trading_agent.alpaca_option_contract_models import (
    OptionCatalogFailure,
    OptionCatalogStatus,
    OptionContractCatalogRequest,
    OptionContractRawResponse,
)
from trading_agent.alpaca_option_contract_store import AlpacaOptionContractStore

PROJECT = Path(__file__).parents[1]
FIXTURES = PROJECT / "tests" / "fixtures"


class _FixtureChainFetcher:
    def fetch_page(
        self,
        request: OptionChainRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionChainRawResponse:
        payload = (FIXTURES / "alpaca_option_chain" / "page-001.json").read_bytes()
        if request.contract_type is OptionContractType.PUT:
            payload = payload.replace(b"AAPL260724C00200000", b"AAPL260724P00200000")
        return OptionChainRawResponse(
            request.request_id,
            page_index,
            page_token,
            dt.datetime(2026, 7, 23, 14, 35, tzinfo=dt.UTC),
            200,
            "application/json",
            payload,
        )


class _FixtureCatalogFetcher:
    def fetch_page(
        self,
        request: OptionContractCatalogRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionContractRawResponse:
        payload = (FIXTURES / "alpaca_option_contract" / "page-001.json").read_bytes()
        if request.contract_type is OptionContractType.PUT:
            payload = (
                payload.replace(b"AAPL260724C00200000", b"AAPL260724P00200000")
                .replace(b'"type":"call"', b'"type":"put"')
                .replace(b"200 Call", b"200 Put")
            )
        return OptionContractRawResponse(
            request.request_id,
            page_index,
            page_token,
            dt.datetime(2026, 7, 23, 14, 35, tzinfo=dt.UTC),
            200,
            "application/json",
            payload,
        )


class _ThreePageCatalogFetcher:
    def fetch_page(
        self,
        request: OptionContractCatalogRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionContractRawResponse:
        replacements = (
            (b"6e58f870-fe73-4583-81e4-b9a37892c36f", b"00200000", b"200 Call", b'"200"'),
            (b"6e58f870-fe73-4583-81e4-b9a37892c370", b"00201000", b"201 Call", b'"201"'),
            (b"6e58f870-fe73-4583-81e4-b9a37892c371", b"00202000", b"202 Call", b'"202"'),
        )
        identifier, strike_code, name, strike = replacements[page_index]
        payload = (FIXTURES / "alpaca_option_contract" / "page-001.json").read_bytes()
        payload = (
            payload.replace(b"6e58f870-fe73-4583-81e4-b9a37892c36f", identifier)
            .replace(b"00200000", strike_code)
            .replace(b"200 Call", name)
            .replace(b'"strike_price":"200"', b'"strike_price":' + strike)
        )
        next_token = (b'"page_token":"page-2"', b'"page_token":"page-3"', b'"page_token":null')[
            page_index
        ]
        payload = payload.replace(b'"page_token":null', next_token)
        return OptionContractRawResponse(
            request.request_id,
            page_index,
            page_token,
            dt.datetime(2026, 7, 23, 14, 35, tzinfo=dt.UTC),
            200,
            "application/json",
            payload,
        )


def test_session_plan_waits_for_delayed_feed_and_uses_next_regular_friday() -> None:
    # Given a regular New York session immediately before and at the 15-minute delay boundary
    before = dt.datetime(2026, 7, 21, 13, 44, 59, tzinfo=dt.UTC)
    eligible = dt.datetime(2026, 7, 21, 13, 45, tzinfo=dt.UTC)

    # When the free indicative collection plan is evaluated
    waiting = plan_indicative_research(before)
    plan = plan_indicative_research(eligible)

    # Then no premature collection occurs and the next tradable Friday expiry is bounded
    assert waiting is None
    assert plan is not None
    assert plan.session_date == dt.date(2026, 7, 21)
    assert plan.expiration_date == dt.date(2026, 7, 24)


def test_fixture_collection_persists_both_sides_once_and_replays_without_fetchers(tmp_path: Path) -> None:
    # Given one bounded AAPL option-chain plan backed by provider-shaped fixtures
    plan = IndicativeResearchPlan(
        session_date=dt.date(2026, 7, 23),
        expiration_date=dt.date(2026, 7, 24),
        underlying_symbol="AAPL",
    )
    outputs = tmp_path / "outputs"

    # When it is collected and then repeated with no network fetchers
    first = collect_indicative_research(plan, outputs, _FixtureCatalogFetcher(), _FixtureChainFetcher())
    replay = collect_indicative_research(plan, outputs, None, None)

    # Then call and put evidence are persisted in both stores and the repeat is local-only
    assert first.chain_snapshots == first.contracts == 2
    assert first.network_sources == 4
    assert first.replayed is False
    assert replay == IndicativeResearchCollection(
        session_date=plan.session_date,
        expiration_date=plan.expiration_date,
        chain_snapshots=2,
        contracts=2,
        replayed=True,
        network_sources=0,
    )
    request_pairs = indicative_research_requests(plan)
    assert tuple(pair[0].contract_type for pair in request_pairs) == (
        OptionContractType.CALL,
        OptionContractType.PUT,
    )
    assert all(pair[0].max_pages == 3 for pair in request_pairs)
    assert all(pair[1].max_pages == 2 for pair in request_pairs)
    assert stat.S_IMODE((outputs / "derivatives" / "option-chain.sqlite3").stat().st_mode) == 0o600
    assert stat.S_IMODE((outputs / "derivatives" / "option-contracts.sqlite3").stat().st_mode) == 0o600


def test_three_page_budget_recovers_without_overwriting_failed_request(tmp_path: Path) -> None:
    # Given the same catalog identity with an exhausted two-page request and a new three-page request.
    plan = IndicativeResearchPlan(
        session_date=dt.date(2026, 7, 23),
        expiration_date=dt.date(2026, 7, 24),
        underlying_symbol="AAPL",
    )
    request = indicative_research_requests(plan)[0][0]
    exhausted_request = request.model_copy(update={"max_pages": 2})
    store = AlpacaOptionContractStore(tmp_path / "option-contracts.sqlite3")
    store.preflight_write()

    # When both bounded requests consume provider pages from the same append-only store.
    exhausted = collect_alpaca_option_contracts(_ThreePageCatalogFetcher(), store, exhausted_request)
    recovered = collect_alpaca_option_contracts(_ThreePageCatalogFetcher(), store, request)

    # Then the old failure remains and the larger request completes under a distinct identity.
    assert exhausted_request.request_id != request.request_id
    assert exhausted.run.status is OptionCatalogStatus.FAILED
    assert exhausted.run.failure_code is OptionCatalogFailure.PAGE_LIMIT
    assert len(exhausted.run.receipt_ids) == len(exhausted.run.contracts) == 2
    assert recovered.run.status is OptionCatalogStatus.SUCCESS
    assert recovered.run.failure_code is None
    assert len(recovered.run.receipt_ids) == len(recovered.run.contracts) == 3
    assert store.run(exhausted_request.request_id) == exhausted.run


def test_provision_is_secret_free_and_tick_waits_without_credentials(tmp_path: Path) -> None:
    # Given a private service destination and no credentials file
    config_path = tmp_path / "private" / "service.json"
    plist_path = tmp_path / "private" / "com.example.indicative.plist"
    reports = tmp_path / "reports"
    uv_path = Path(shutil.which("uv") or "")
    args = (
        "provision",
        "--label",
        "com.example.indicative",
        "--project-root",
        str(PROJECT),
        "--uv-path",
        str(uv_path),
        "--outputs-root",
        str(tmp_path / "outputs"),
        "--credentials-path",
        str(tmp_path / "missing.env"),
        "--runtime-output-root",
        str(tmp_path / "runtime"),
        "--config",
        str(config_path),
        "--plist",
        str(plist_path),
        "--output-dir",
        str(reports),
    )

    # When the service is provisioned and ticked before the collection window
    provisioned = service_cli.main(args)
    ticked = service_cli.main(
        ("tick", "--config", str(config_path), "--output-dir", str(reports)),
        clock=lambda: dt.datetime(2026, 7, 21, 13, 44, tzinfo=dt.UTC),
    )

    # Then launchd polls every 15 minutes without credentials or mutation authority in the plist
    assert provisioned == ticked == 0
    config = load_indicative_research_service_config(config_path)
    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["StartInterval"] == 900
    assert payload["RunAtLoad"] is True
    assert "EnvironmentVariables" not in payload
    assert "paper-api.alpaca.markets" not in plist_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600
    assert verify_indicative_research_launch_agent(config_path, plist_path).ready is True
    assert config.credentials_path.name == "missing.env"
    report = (reports / service_cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: waiting_session" in report
    assert "OPRA authority: false" in report
    assert "order mutation: none" in report
