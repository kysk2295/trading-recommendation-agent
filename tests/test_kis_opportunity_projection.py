from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from trading_agent.kis_opportunity_projection import (
    InvalidKisOpportunityProjectionError,
    project_kis_us_opportunity,
)
from trading_agent.kis_provider import KisRankedStock
from trading_agent.market_risk import (
    HaltSnapshot,
    MarketRiskConfig,
    MarketRiskScreen,
)
from trading_agent.ranking_journal import (
    RankingDiscovery,
    RankingFailure,
    RankingGroup,
    RankingSource,
)
from trading_agent.research_identity_models import AgentFamily, MarketId

OBSERVED_AT = dt.datetime(2026, 7, 15, 14, 0, tzinfo=dt.UTC)
HALT_AT = OBSERVED_AT - dt.timedelta(seconds=2)


def test_complete_kis_screen_projects_a_deterministic_opportunity() -> None:
    selected = _stock("NAS", "ACME", change_pct=0.1234)
    discovery = _complete_discovery(selected)
    screen = _screen(selected)

    first = project_kis_us_opportunity(
        discovery,
        halt_snapshot=_halts(),
        risk_screen=screen,
        observed_at=OBSERVED_AT,
    )
    second = project_kis_us_opportunity(
        discovery,
        halt_snapshot=_halts(),
        risk_screen=screen,
        observed_at=OBSERVED_AT,
    )

    assert first is not None
    assert second is not None
    assert first.opportunity_id == second.opportunity_id
    assert first.strategy_lane.market_id is MarketId.US_EQUITIES
    assert first.strategy_lane.agent_family is AgentFamily.OPPORTUNITY_MANAGER
    assert first.strategy_lane.strategy_id == "ranking_momentum"
    assert first.strategy_lane.canonical_id == "us_equities/opportunity_manager/ranking_momentum"
    assert first.producer_strategy_version == "kis-risk-screen-v1"
    assert first.valid_until == OBSERVED_AT + dt.timedelta(seconds=60)
    assert tuple(candidate.symbol for candidate in first.candidates) == ("ACME",)
    assert first.candidates[0].rank == 1
    assert first.candidates[0].score == Decimal("0.1234")
    assert tuple(feature.name for feature in first.candidates[0].features) == (
        "change_pct",
        "dollar_volume",
        "price",
        "spread_bps",
        "volume",
        "volume_to_adv",
    )
    assert tuple(source.source_id for source in first.source_coverage) == (
        "kis_updown_ams",
        "kis_updown_nas",
        "kis_updown_nys",
        "kis_volume_ams",
        "kis_volume_nas",
        "kis_volume_nys",
        "nyse_halts",
    )
    assert all(source.complete for source in first.source_coverage)
    assert all(evidence.observed_at <= first.observed_at for evidence in first.evidence_refs)
    assert {evidence.namespace for evidence in first.evidence_refs} == {
        "kis/market_risk",
        "kis/ranking",
        "nyse/halts",
    }


@pytest.mark.parametrize("fault", ["failure", "missing", "duplicate"])
def test_projection_fails_closed_when_ranking_coverage_is_not_exact(fault: str) -> None:
    stock = _stock("NAS", "ACME")
    discovery = _complete_discovery(stock)
    if fault == "failure":
        discovery = RankingDiscovery(
            discovery.groups,
            (RankingFailure(RankingSource.VOLUME, "NYS", "timeout"),),
        )
    elif fault == "missing":
        discovery = RankingDiscovery(discovery.groups[:-1], ())
    else:
        discovery = RankingDiscovery((*discovery.groups, discovery.groups[0]), ())

    with pytest.raises(InvalidKisOpportunityProjectionError):
        project_kis_us_opportunity(
            discovery,
            halt_snapshot=_halts(),
            risk_screen=_screen(stock),
            observed_at=OBSERVED_AT,
        )


def test_complete_screen_without_selected_candidates_has_no_opportunity() -> None:
    stock = _stock("NAS", "ACME")
    screen = MarketRiskScreen(
        observed_at=HALT_AT,
        config=MarketRiskConfig(),
        selected=(),
        not_selected=(stock,),
        rejected=(),
    )

    assert (
        project_kis_us_opportunity(
            _complete_discovery(stock),
            halt_snapshot=_halts(),
            risk_screen=screen,
            observed_at=OBSERVED_AT,
        )
        is None
    )


def test_selected_candidate_must_exist_in_the_exact_discovery() -> None:
    ranked = _stock("NAS", "ACME")
    unrelated = _stock("NAS", "OTHER")

    with pytest.raises(InvalidKisOpportunityProjectionError):
        project_kis_us_opportunity(
            _complete_discovery(ranked),
            halt_snapshot=_halts(),
            risk_screen=_screen(unrelated),
            observed_at=OBSERVED_AT,
        )


def test_selected_candidate_payload_must_match_an_exact_ranking_row() -> None:
    ranked = _stock("NAS", "ACME", change_pct=0.08)
    changed_after_discovery = _stock("NAS", "ACME", change_pct=0.25)

    with pytest.raises(InvalidKisOpportunityProjectionError):
        project_kis_us_opportunity(
            _complete_discovery(ranked),
            halt_snapshot=_halts(),
            risk_screen=_screen(changed_after_discovery),
            observed_at=OBSERVED_AT,
        )


def test_projection_rejects_future_or_naive_observations() -> None:
    stock = _stock("NAS", "ACME")
    future_halts = HaltSnapshot(OBSERVED_AT + dt.timedelta(seconds=1), frozenset())

    with pytest.raises(InvalidKisOpportunityProjectionError):
        project_kis_us_opportunity(
            _complete_discovery(stock),
            halt_snapshot=future_halts,
            risk_screen=_screen(stock),
            observed_at=OBSERVED_AT,
        )
    with pytest.raises(InvalidKisOpportunityProjectionError):
        project_kis_us_opportunity(
            _complete_discovery(stock),
            halt_snapshot=_halts(),
            risk_screen=_screen(stock),
            observed_at=OBSERVED_AT.replace(tzinfo=None),
        )


def _complete_discovery(stock: KisRankedStock) -> RankingDiscovery:
    groups = tuple(
        RankingGroup(
            source,
            exchange,
            (stock,) if exchange == stock.exchange else (),
        )
        for source in RankingSource
        for exchange in ("NAS", "NYS", "AMS")
    )
    return RankingDiscovery(groups, ())


def _screen(*selected: KisRankedStock) -> MarketRiskScreen:
    return MarketRiskScreen(
        observed_at=HALT_AT,
        config=MarketRiskConfig(),
        selected=selected,
        not_selected=(),
        rejected=(),
    )


def _halts() -> HaltSnapshot:
    return HaltSnapshot(HALT_AT, frozenset())


def _stock(
    exchange: str,
    symbol: str,
    *,
    change_pct: float = 0.08,
) -> KisRankedStock:
    return KisRankedStock(
        exchange=exchange,
        symbol=symbol,
        name=symbol.title(),
        price=10.0,
        change_pct=change_pct,
        bid=9.99,
        ask=10.01,
        volume=1_500_000,
        dollar_volume=15_000_000.0,
        average_daily_volume=1_000_000,
        rank=1,
    )
