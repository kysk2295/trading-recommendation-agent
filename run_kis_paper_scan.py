#!/usr/bin/env -S uv run --python 3.12 --with httpx2[http2,brotli,zstd] --with pydantic --with rich --with typer python

from __future__ import annotations

import datetime as dt
from pathlib import Path

import typer
from rich import print as rprint

from scr_backtest.kis_http import (
    begin_retry_capture,
    captured_retry_events,
    end_retry_capture,
)
from scr_backtest.kis_intraday import KisSession
from trading_agent.bar_archive import track_candidates, tracked_candidates
from trading_agent.candidate_input_audit import (
    CandidateInputCycleAudit,
    append_candidate_input_cycle,
)
from trading_agent.causality import exclude_backdated_recommendations
from trading_agent.contract_outbox import (
    append_opportunity_snapshot,
    append_trade_signal_publication,
)
from trading_agent.engine import RecommendationEngine
from trading_agent.kis_auth import (
    KisMode,
    create_kis_client,
    get_access_token,
    load_kis_credentials,
)
from trading_agent.kis_opportunity_projection import project_kis_us_opportunity
from trading_agent.kis_provider import KisRankedStock
from trading_agent.kis_rankings import (
    discover_rankings as _discover_rankings,
)
from trading_agent.kis_rankings import (
    timestamp_rankings,
)
from trading_agent.kis_retry_audit import append_kis_retry_audit
from trading_agent.kis_scan import KisPaperScanner, ScanObservation
from trading_agent.kis_scan_report import ScanSummary, write_scan_summary
from trading_agent.market_risk import (
    HaltSnapshot,
    MarketRiskConfig,
    MarketRiskGate,
    MarketRiskScreen,
    fetch_active_halts,
    write_market_risk_screen,
)
from trading_agent.models import Recommendation
from trading_agent.opening_gap import OpeningGapCapture, capture_opening_gaps
from trading_agent.ranking_journal import (
    RankingDiscovery,
    RankingSnapshot,
    append_ranking_coverage,
    append_ranking_snapshot,
)
from trading_agent.replay import write_alert_outbox, write_report
from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.risk import RiskConfig
from trading_agent.scan_cycle import scan_exit_code
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.store import PaperStore
from trading_agent.strategy_factory import StrategyMode, build_strategy
from trading_agent.trade_signal_publication import project_trade_signal_publications


def unselected_tracked_candidates(
    selected: tuple[KisRankedStock, ...],
    tracked: tuple[KisRankedStock, ...],
) -> tuple[KisRankedStock, ...]:
    selected_keys = {(stock.exchange, stock.symbol) for stock in selected}
    return tuple(stock for stock in tracked if (stock.exchange, stock.symbol) not in selected_keys)


def partition_halted_candidates(
    candidates: tuple[KisRankedStock, ...],
    halted_symbols: frozenset[str],
) -> tuple[tuple[KisRankedStock, ...], tuple[KisRankedStock, ...]]:
    allowed = tuple(stock for stock in candidates if stock.symbol.upper() not in halted_symbols)
    blocked = tuple(stock for stock in candidates if stock.symbol.upper() in halted_symbols)
    return allowed, blocked


def publish_opportunity_contract(
    output: Path,
    discovery: RankingDiscovery,
    halt_snapshot: HaltSnapshot,
    risk_screen: MarketRiskScreen,
    observed_at: dt.datetime,
) -> OpportunitySnapshot | None:
    if discovery.failures:
        return None
    snapshot = project_kis_us_opportunity(
        discovery,
        halt_snapshot=halt_snapshot,
        risk_screen=risk_screen,
        observed_at=observed_at,
    )
    if snapshot is not None:
        _ = append_opportunity_snapshot(
            output / "opportunities.v1.jsonl",
            snapshot,
        )
    return snapshot


def publish_trade_signal_contracts(
    output: Path,
    recommendations: tuple[Recommendation, ...],
    opportunity: OpportunitySnapshot | None,
    strategy: StrategyMode,
    published_at: dt.datetime,
    created_after: dt.datetime,
) -> int:
    if opportunity is None:
        return 0
    strategy_lane = StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id=strategy.value,
    )
    publications = project_trade_signal_publications(
        recommendations,
        strategy_lane=strategy_lane,
        strategy_version=f"{strategy.value}-v1",
        opportunity=opportunity,
        published_at=published_at,
        created_after=created_after,
    )
    return sum(
        append_trade_signal_publication(
            output / "trade-signals.v1.jsonl",
            output / "trade-signal-cards-ko",
            publication,
        )
        for publication in publications
    )


def main(
    output_dir: str | None = None,
    top: int = 3,
    mode: KisMode = KisMode.LIVE,
    range_minutes: int = 5,
    max_pages: int = 10,
    strategy: StrategyMode = StrategyMode.ORB,
) -> None:
    if not 1 <= top <= 10:
        raise typer.BadParameter("top은 1~10이어야 합니다")
    if not 1 <= range_minutes <= 30:
        raise typer.BadParameter("range-minutes는 1~30이어야 합니다")
    if not 1 <= max_pages <= 10:
        raise typer.BadParameter("max-pages는 1~10이어야 합니다")
    started_at = dt.datetime.now().astimezone()
    output = _output_path(output_dir, started_at)
    database = output / "paper_recommendations.sqlite3"
    credentials = load_kis_credentials(mode)
    store = PaperStore(database)
    causality_exclusions = exclude_backdated_recommendations(store, started_at)
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig()),
        build_strategy(strategy, range_minutes),
        RiskConfig(),
        store,
    )
    observations: list[ScanObservation] = []
    candidate_observations: list[ScanObservation] = []
    selected_count = 0
    scan_completed = False
    opportunity: OpportunitySnapshot | None = None
    retry_capture = begin_retry_capture()
    try:
        with create_kis_client(mode) as client:
            token = get_access_token(client, credentials, mode)
            discovery, checked_at = timestamp_rankings(
                lambda: _discover_rankings(client, credentials, token),
                lambda: dt.datetime.now().astimezone(),
            )
            groups = discovery.groups
            halt_snapshot = fetch_active_halts(client)
            checked_at = max(checked_at, halt_snapshot.observed_at).astimezone()
            risk_screen = MarketRiskGate(
                halt_snapshot,
                MarketRiskConfig(),
            ).screen(
                tuple(group.stocks for group in groups),
                top,
            )
            gap_cycle = capture_opening_gaps(
                client,
                OpeningGapCapture(
                    output,
                    KisSession(credentials, token),
                    checked_at,
                    risk_screen,
                ),
            )
            candidates = risk_screen.selected
            selected_count = len(candidates)
            write_market_risk_screen(output / "market_risk_screen.csv", risk_screen)
            append_ranking_snapshot(
                output / "kis_ranking_snapshots.csv",
                RankingSnapshot(checked_at, groups, candidates),
            )
            append_ranking_coverage(
                output / "kis_ranking_request_coverage.csv",
                checked_at,
                discovery,
            )
            opportunity = publish_opportunity_contract(
                output,
                discovery,
                halt_snapshot,
                risk_screen,
                checked_at,
            )
            _ = track_candidates(database, checked_at, candidates)
            followers = unselected_tracked_candidates(
                candidates,
                tracked_candidates(database, checked_at),
            )
            followers, blocked_followers = partition_halted_candidates(
                followers,
                halt_snapshot.symbols,
            )
            scanner = KisPaperScanner(
                client,
                KisSession(credentials, token),
                engine,
            )
            for stock in candidates:
                observation = scanner.observe(stock, max_pages)
                candidate_observations.append(observation)
                observations.append(observation)
            for stock in followers:
                observations.append(scanner.follow(stock, max_pages))
            observations.extend(
                ScanObservation(
                    stock.exchange,
                    stock.symbol,
                    stock.change_pct,
                    stock.price,
                    stock.spread_bps,
                    0,
                    "공식 현재 거래정지: 추적 중단",
                )
                for stock in blocked_followers
            )
            scan_completed = True
    finally:
        retry_events = captured_retry_events()
        end_retry_capture(retry_capture)
        append_candidate_input_cycle(
            output / "candidate_input_cycles.csv",
            CandidateInputCycleAudit(
                started_at,
                selected_count,
                sum(row.candidate_input_archived for row in candidate_observations),
                scan_completed,
            ),
        )
        append_kis_retry_audit(output, started_at, retry_events)
    write_report(output / "recommendations_ko.md", store)
    published_at = dt.datetime.now().astimezone()
    queued = write_alert_outbox(output, store, published_at)
    contract_signals = publish_trade_signal_contracts(
        output,
        store.recommendations(),
        opportunity,
        strategy,
        published_at,
        started_at,
    )
    write_scan_summary(
        output / "kis_scan_summary_ko.md",
        ScanSummary(
            checked_at,
            mode,
            strategy,
            len(halt_snapshot.symbols),
            risk_screen,
            tuple(observations),
            len(store.recommendations()),
            discovery.failures,
        ),
    )
    rprint(
        f"[green]완료[/green] 현재 후보 {len(candidates)}개, "
        + f"추적 {len(followers) + len(blocked_followers)}개, 추천 "
        + f"{len(store.recommendations())}개, 인과성 제외 {causality_exclusions}개, "
        + f"신규 카드 {queued}개, v2 기회 {int(opportunity is not None)}개, "
        + f"신규 v2 조건부 신호 {contract_signals}개, {output}"
    )
    exit_code = scan_exit_code(
        tuple(observations),
        gap_cycle.failure_count,
        len(discovery.failures),
    )
    if exit_code:
        raise typer.Exit(code=exit_code)


def _output_path(value: str | None, checked_at: dt.datetime) -> Path:
    if value is not None:
        return Path(value)
    stamp = checked_at.strftime("%Y%m%d_%H%M%S")
    return Path("outputs/live_runs") / stamp


if __name__ == "__main__":
    typer.run(main)
