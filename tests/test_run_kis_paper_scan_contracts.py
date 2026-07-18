from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest
import typer

import run_kis_paper_scan
from run_kis_paper_scan import (
    append_quote_actionability_contracts,
    append_trade_signal_contracts,
    build_trade_signal_contracts,
    configure_research_projection,
    project_opportunity_research_input,
    publish_opportunity_contract,
    publish_trade_signal_contracts,
)
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_security_master import collect_alpaca_security_master
from trading_agent.alpaca_security_master_store import AlpacaSecurityMasterStore
from trading_agent.contract_outbox import ContractOutboxConflictError
from trading_agent.kis_provider import KisRankedStock
from trading_agent.kis_research_projection import ResearchProjectionOptions
from trading_agent.kis_us_quote import KisUsLevelOneQuote
from trading_agent.market_risk import (
    HaltSnapshot,
    MarketRiskConfig,
    MarketRiskScreen,
)
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.ranking_journal import (
    RankingDiscovery,
    RankingFailure,
    RankingGroup,
    RankingSource,
)
from trading_agent.store import PaperStore
from trading_agent.strategy_factory import StrategyMode
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerProjectionError
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore
from trading_agent.us_quote_publication import evaluate_quote_publications

OBSERVED_AT = dt.datetime(2026, 7, 15, 14, 0, tzinfo=dt.UTC)
PROJECT = Path(__file__).resolve().parents[1]
FOUNDATION = PROJECT / "examples/data/us-orb-data-foundation-v1.json"


def test_legacy_alert_timestamp_is_captured_at_outbox_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    queued_at = OBSERVED_AT + dt.timedelta(seconds=30)
    captured: list[dt.datetime] = []

    def capture(
        output: Path,
        current_store: PaperStore,
        current_queued_at: dt.datetime,
    ) -> int:
        assert output == tmp_path
        assert current_store is store
        captured.append(current_queued_at)
        return 7

    monkeypatch.setattr("run_kis_paper_scan.write_alert_outbox", capture)

    count = run_kis_paper_scan.write_current_alert_outbox(
        tmp_path,
        store,
        clock=lambda: queued_at,
    )

    assert count == 7
    assert captured == [queued_at]


def test_opportunity_helper_writes_the_additive_v2_artifact(tmp_path: Path) -> None:
    stock = _stock()

    snapshot = publish_opportunity_contract(
        tmp_path,
        _complete_discovery(stock),
        _halts(),
        _screen(stock),
        OBSERVED_AT,
    )

    assert snapshot is not None
    lines = (tmp_path / "opportunities.v1.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["opportunity_id"] == snapshot.opportunity_id


def test_failed_discovery_publishes_no_v2_contract(tmp_path: Path) -> None:
    stock = _stock()
    complete = _complete_discovery(stock)
    failed = RankingDiscovery(
        complete.groups[:-1],
        (RankingFailure(RankingSource.VOLUME, "AMS", "timeout"),),
    )

    snapshot = publish_opportunity_contract(
        tmp_path,
        failed,
        _halts(),
        _screen(stock),
        OBSERVED_AT,
    )

    assert snapshot is None
    assert not (tmp_path / "opportunities.v1.jsonl").exists()
    assert not (tmp_path / "trade-signals.v1.jsonl").exists()


def test_research_projection_configuration_is_all_or_none(tmp_path: Path) -> None:
    assert configure_research_projection(ResearchProjectionOptions(None, None, None)) is None

    with pytest.raises(typer.BadParameter, match="research projection"):
        _ = configure_research_projection(
            ResearchProjectionOptions(
                str(FOUNDATION),
                str(tmp_path / "scanner.sqlite3"),
                None,
            )
        )
    with pytest.raises(typer.BadParameter, match="research projection"):
        _ = configure_research_projection(
            ResearchProjectionOptions(
                None,
                None,
                None,
                str(tmp_path / "security-master.sqlite3"),
            )
        )
    dynamic = configure_research_projection(
        ResearchProjectionOptions(
            None,
            str(tmp_path / "dynamic-scanner.sqlite3"),
            str(tmp_path / "dynamic-canonical"),
            str(tmp_path / "security-master.sqlite3"),
        )
    )
    assert dynamic is not None
    assert dynamic.foundation_manifest is None
    with pytest.raises(typer.BadParameter, match="mutually exclusive"):
        _ = configure_research_projection(
            ResearchProjectionOptions(
                str(FOUNDATION),
                str(tmp_path / "scanner.sqlite3"),
                str(tmp_path / "canonical"),
                str(tmp_path / "security-master.sqlite3"),
            )
        )


def test_opportunity_projects_to_durable_replay_bound_scanner_input(
    tmp_path: Path,
) -> None:
    observed_at = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)
    stock = KisRankedStock(
        exchange="NAS",
        symbol="FIXT",
        name="Fixture",
        change_pct=12.5,
        price=10.0,
        bid=9.99,
        ask=10.01,
        volume=1_500_000,
        dollar_volume=15_000_000.0,
        average_daily_volume=1_000_000,
        rank=1,
    )
    opportunity = publish_opportunity_contract(
        tmp_path,
        _complete_discovery(stock),
        HaltSnapshot(observed_at, frozenset()),
        MarketRiskScreen(
            observed_at=observed_at,
            config=MarketRiskConfig(),
            selected=(stock,),
            not_selected=(),
            rejected=(),
        ),
        observed_at,
    )
    assert opportunity is not None
    config = configure_research_projection(
        ResearchProjectionOptions(
            str(FOUNDATION),
            str(tmp_path / "scanner.sqlite3"),
            str(tmp_path / "canonical"),
        )
    )
    assert config is not None

    snapshot = project_opportunity_research_input(opportunity, config)

    assert snapshot is not None
    assert snapshot.identity.scope == "us_equities.broad_scanner"
    assert snapshot.candidates[0].instrument_id == "us-eq-fixture-0001"
    assert UsOpportunityScannerStore(config.store).latest_snapshot() == snapshot

    missing_security = configure_research_projection(
        ResearchProjectionOptions(
            None,
            str(tmp_path / "scanner-2.sqlite3"),
            str(tmp_path / "canonical-2"),
            str(tmp_path / "missing-security.sqlite3"),
        )
    )
    assert missing_security is not None
    with pytest.raises(UsOpportunityScannerProjectionError):
        _ = project_opportunity_research_input(opportunity, missing_security)


def test_dynamic_security_store_builds_ready_foundation_for_kis_opportunity(
    tmp_path: Path,
) -> None:
    observed_at = dt.datetime(2026, 7, 20, 13, 30, tzinfo=dt.UTC)
    stock = KisRankedStock(
        exchange="NAS",
        symbol="FIXT",
        name="Fixture",
        change_pct=0.125,
        price=10.0,
        bid=9.99,
        ask=10.01,
        volume=1_500_000,
        dollar_volume=15_000_000.0,
        average_daily_volume=1_000_000,
        rank=1,
    )
    opportunity = publish_opportunity_contract(
        tmp_path,
        _complete_discovery(stock),
        HaltSnapshot(observed_at, frozenset()),
        MarketRiskScreen(
            observed_at=observed_at,
            config=MarketRiskConfig(),
            selected=(stock,),
            not_selected=(),
            rejected=(),
        ),
        observed_at,
    )
    assert opportunity is not None
    security_path = tmp_path / "security-master.sqlite3"
    body = json.dumps(
        (
            {
                "id": "asset-fixt",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "symbol": "FIXT",
                "name": "Fixture",
                "status": "active",
                "tradable": True,
            },
        )
    ).encode()
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, content=body)),
        follow_redirects=False,
    ) as client:
        _ = collect_alpaca_security_master(
            client,
            AlpacaCredentials("fixture-key", "fixture-secret"),
            AlpacaSecurityMasterStore(security_path),
            observed_at=observed_at - dt.timedelta(minutes=1),
        )
    config = configure_research_projection(
        ResearchProjectionOptions(
            None,
            str(tmp_path / "scanner.sqlite3"),
            str(tmp_path / "canonical"),
            str(security_path),
        )
    )
    assert config is not None

    snapshot = project_opportunity_research_input(opportunity, config)

    assert snapshot is not None
    assert snapshot.candidates[0].instrument_id == "alpaca:asset-fixt"
    foundation = UsOpportunityScannerStore(config.store).latest_foundation()
    assert foundation is not None
    assert foundation.evaluate_data_readiness().status.value == "ready"
    assert tuple(item.source_id.canonical_id for item in foundation.capabilities) == (
        "alpaca/assets",
        "kis/us_ranking",
        "nyse/current_halts",
    )


def test_signal_helper_is_idempotent_and_does_not_touch_the_v1_outbox(
    tmp_path: Path,
) -> None:
    stock = _stock()
    opportunity = publish_opportunity_contract(
        tmp_path,
        _complete_discovery(stock),
        _halts(),
        _screen(stock),
        OBSERVED_AT,
    )
    assert opportunity is not None
    v1_jsonl = tmp_path / "recommendation_alerts.jsonl"
    v1_markdown = tmp_path / "recommendation_alerts_ko.md"
    v1_jsonl.write_text('{"legacy":true}\n', encoding="utf-8")
    v1_markdown.write_text("legacy card\n", encoding="utf-8")
    recommendation = Recommendation(
        recommendation_id="rec-1",
        symbol="ACME",
        strategy="opening_range_breakout",
        created_at=OBSERVED_AT + dt.timedelta(seconds=10),
        entry=10.5,
        stop=10.0,
        target_1r=11.0,
        target_2r=11.5,
        state=RecommendationState.SETUP,
        rationale="ORB와 거래량 확대",
    )
    published_at = OBSERVED_AT + dt.timedelta(seconds=15)

    first = publish_trade_signal_contracts(
        tmp_path,
        (recommendation,),
        opportunity,
        StrategyMode.ORB,
        published_at,
        OBSERVED_AT,
    )
    second = publish_trade_signal_contracts(
        tmp_path,
        (recommendation,),
        opportunity,
        StrategyMode.ORB,
        published_at,
        OBSERVED_AT,
    )

    assert first == 1
    assert second == 0
    lines = (tmp_path / "trade-signals.v1.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["signal"]["signal_id"] == "rec-1"
    assert len(tuple((tmp_path / "trade-signal-cards-ko").glob("*.ko.md"))) == 1
    assert v1_jsonl.read_text(encoding="utf-8") == '{"legacy":true}\n'
    assert v1_markdown.read_text(encoding="utf-8") == "legacy card\n"


def test_signal_build_is_pure_and_append_is_separate(tmp_path: Path) -> None:
    stock = _stock()
    opportunity = publish_opportunity_contract(
        tmp_path,
        _complete_discovery(stock),
        _halts(),
        _screen(stock),
        OBSERVED_AT,
    )
    assert opportunity is not None
    recommendation = _recommendation()

    publications = build_trade_signal_contracts(
        (recommendation,),
        opportunity,
        StrategyMode.ORB,
        OBSERVED_AT + dt.timedelta(seconds=15),
        OBSERVED_AT,
    )

    assert len(publications) == 1
    assert not (tmp_path / "trade-signals.v1.jsonl").exists()
    assert append_trade_signal_contracts(tmp_path, publications) == 1
    assert append_trade_signal_contracts(tmp_path, publications) == 0


def test_fake_quote_path_appends_conditional_then_validated_contracts(
    tmp_path: Path,
) -> None:
    stock = _stock()
    opportunity = publish_opportunity_contract(
        tmp_path,
        _complete_discovery(stock),
        _halts(),
        _screen(stock),
        OBSERVED_AT,
    )
    assert opportunity is not None
    published_at = OBSERVED_AT + dt.timedelta(seconds=15)
    evaluated_at = published_at + dt.timedelta(seconds=2)
    publications = build_trade_signal_contracts(
        (_recommendation(),),
        opportunity,
        StrategyMode.ORB,
        published_at,
        OBSERVED_AT,
    )
    calls: list[tuple[str, str]] = []
    batch = evaluate_quote_publications(
        publications,
        exchange_by_symbol={"ACME": "NAS"},
        fetch_quote=lambda exchange, symbol: (
            calls.append((exchange, symbol))
            or KisUsLevelOneQuote(
                exchange=exchange,
                symbol=symbol,
                provider_observed_at=evaluated_at - dt.timedelta(seconds=1),
                received_at=evaluated_at - dt.timedelta(milliseconds=500),
                bid=Decimal("10.49"),
                ask=Decimal("10.50"),
                bid_size=1_000,
                ask_size=900,
            )
        ),
        scan_started_at=OBSERVED_AT,
        clock=lambda: evaluated_at,
    )

    assert append_trade_signal_contracts(tmp_path, publications) == 1
    counts = append_quote_actionability_contracts(tmp_path, batch)

    assert calls == [("NAS", "ACME")]
    assert counts.snapshot_count == 1
    assert counts.validated_signal_count == 1
    assert counts.assessment_count == 1
    signals = tuple(
        json.loads(line) for line in (tmp_path / "trade-signals.v1.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert tuple(item["signal"]["actionability"] for item in signals) == (
        "conditional",
        "current_quote_validated",
    )
    assert len((tmp_path / "us-quote-snapshots.v2.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert len((tmp_path / "quote-actionability-assessments.v2.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert len(tuple((tmp_path / "trade-signal-cards-ko").glob("*.ko.md"))) == 2


def test_conflicting_terminal_batch_writes_no_partial_quote_artifacts(
    tmp_path: Path,
) -> None:
    stock = _stock()
    opportunity = publish_opportunity_contract(
        tmp_path,
        _complete_discovery(stock),
        _halts(),
        _screen(stock),
        OBSERVED_AT,
    )
    assert opportunity is not None
    published_at = OBSERVED_AT + dt.timedelta(seconds=15)
    evaluated_at = published_at + dt.timedelta(seconds=2)
    publications = build_trade_signal_contracts(
        (_recommendation(),),
        opportunity,
        StrategyMode.ORB,
        published_at,
        OBSERVED_AT,
    )
    first = _quote_batch(
        publications,
        evaluated_at=evaluated_at,
        received_at=evaluated_at - dt.timedelta(milliseconds=500),
    )
    second = _quote_batch(
        publications,
        evaluated_at=evaluated_at + dt.timedelta(milliseconds=100),
        received_at=evaluated_at - dt.timedelta(milliseconds=400),
    )

    assert append_trade_signal_contracts(tmp_path, publications) == 1
    _ = append_quote_actionability_contracts(tmp_path, first)
    paths = (
        tmp_path / "us-quote-snapshots.v2.jsonl",
        tmp_path / "trade-signals.v1.jsonl",
        tmp_path / "quote-actionability-assessments.v2.jsonl",
    )
    before = {path: path.read_bytes() for path in paths}
    cards_before = {path.name: path.read_bytes() for path in (tmp_path / "trade-signal-cards-ko").iterdir()}

    with pytest.raises(ContractOutboxConflictError):
        _ = append_quote_actionability_contracts(tmp_path, second)

    assert {path: path.read_bytes() for path in paths} == before
    assert {path.name: path.read_bytes() for path in (tmp_path / "trade-signal-cards-ko").iterdir()} == cards_before


def test_v2_quote_contracts_leave_legacy_v1_files_untouched(
    tmp_path: Path,
) -> None:
    legacy_snapshot = tmp_path / "us-quote-snapshots.v1.jsonl"
    legacy_assessment = tmp_path / "quote-actionability-assessments.v1.jsonl"
    legacy_snapshot.write_text("legacy snapshot bytes\n", encoding="utf-8")
    legacy_assessment.write_text("legacy assessment bytes\n", encoding="utf-8")
    stock = _stock()
    opportunity = publish_opportunity_contract(
        tmp_path,
        _complete_discovery(stock),
        _halts(),
        _screen(stock),
        OBSERVED_AT,
    )
    assert opportunity is not None
    published_at = OBSERVED_AT + dt.timedelta(seconds=15)
    evaluated_at = published_at + dt.timedelta(seconds=2)
    publications = build_trade_signal_contracts(
        (_recommendation(),),
        opportunity,
        StrategyMode.ORB,
        published_at,
        OBSERVED_AT,
    )
    assert append_trade_signal_contracts(tmp_path, publications) == 1

    counts = append_quote_actionability_contracts(
        tmp_path,
        _quote_batch(
            publications,
            evaluated_at=evaluated_at,
            received_at=evaluated_at - dt.timedelta(milliseconds=500),
        ),
    )

    assert counts.snapshot_count == 1
    assert counts.assessment_count == 1
    assert legacy_snapshot.read_text(encoding="utf-8") == "legacy snapshot bytes\n"
    assert legacy_assessment.read_text(encoding="utf-8") == "legacy assessment bytes\n"
    assert (tmp_path / "us-quote-snapshots.v2.jsonl").is_file()
    assert (tmp_path / "quote-actionability-assessments.v2.jsonl").is_file()


def _recommendation() -> Recommendation:
    return Recommendation(
        recommendation_id="rec-1",
        symbol="ACME",
        strategy="opening_range_breakout",
        created_at=OBSERVED_AT + dt.timedelta(seconds=10),
        entry=10.5,
        stop=10.0,
        target_1r=11.0,
        target_2r=11.5,
        state=RecommendationState.SETUP,
        rationale="ORB와 거래량 확대",
    )


def _quote_batch(
    publications,
    *,
    evaluated_at: dt.datetime,
    received_at: dt.datetime,
):
    return evaluate_quote_publications(
        publications,
        exchange_by_symbol={"ACME": "NAS"},
        fetch_quote=lambda exchange, symbol: KisUsLevelOneQuote(
            exchange=exchange,
            symbol=symbol,
            provider_observed_at=evaluated_at - dt.timedelta(seconds=1),
            received_at=received_at,
            bid=Decimal("10.49"),
            ask=Decimal("10.50"),
            bid_size=1_000,
            ask_size=900,
        ),
        scan_started_at=OBSERVED_AT,
        clock=lambda: evaluated_at,
    )


def _complete_discovery(stock: KisRankedStock) -> RankingDiscovery:
    return RankingDiscovery(
        tuple(
            RankingGroup(
                source,
                exchange,
                (stock,) if exchange == stock.exchange else (),
            )
            for source in RankingSource
            for exchange in ("NAS", "NYS", "AMS")
        ),
        (),
    )


def _halts() -> HaltSnapshot:
    return HaltSnapshot(OBSERVED_AT, frozenset())


def _screen(stock: KisRankedStock) -> MarketRiskScreen:
    return MarketRiskScreen(
        observed_at=OBSERVED_AT,
        config=MarketRiskConfig(),
        selected=(stock,),
        not_selected=(),
        rejected=(),
    )


def _stock() -> KisRankedStock:
    return KisRankedStock(
        exchange="NAS",
        symbol="ACME",
        name="Acme",
        price=10.0,
        change_pct=0.12,
        bid=9.99,
        ask=10.01,
        volume=1_500_000,
        dollar_volume=15_000_000.0,
        average_daily_volume=1_000_000,
        rank=1,
    )
