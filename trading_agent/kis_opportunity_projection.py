from __future__ import annotations

import datetime as dt
import hashlib
import math
from decimal import Decimal
from typing import Final, override

from pydantic import ValidationError

from trading_agent.kis_provider import KisRankedStock
from trading_agent.kis_rankings import US_EXCHANGES
from trading_agent.market_risk import HaltSnapshot, MarketRiskScreen
from trading_agent.ranking_journal import RankingDiscovery, RankingSource
from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)

OPPORTUNITY_LANE: Final = StrategyLaneRef(
    market_id=MarketId.US_EQUITIES,
    agent_family=AgentFamily.OPPORTUNITY_MANAGER,
    strategy_id="ranking_momentum",
)
PRODUCER_STRATEGY_VERSION: Final = "kis-risk-screen-v1"
VALIDITY: Final = dt.timedelta(seconds=60)
_EXPECTED_RANKING_KEYS: Final = frozenset(
    (source, exchange)
    for source in RankingSource
    for exchange in US_EXCHANGES
)


class InvalidKisOpportunityProjectionError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"KIS 기회 스냅샷을 안전하게 만들 수 없습니다: {self.reason}"


def project_kis_us_opportunity(
    discovery: RankingDiscovery,
    *,
    halt_snapshot: HaltSnapshot,
    risk_screen: MarketRiskScreen,
    observed_at: dt.datetime,
) -> OpportunitySnapshot | None:
    _validate_observations(observed_at, halt_snapshot, risk_screen)
    _validate_complete_discovery(discovery)

    ranked_rows = {
        (stock.exchange, stock.symbol)
        for group in discovery.groups
        for stock in group.stocks
    }
    selected_keys = tuple((stock.exchange, stock.symbol) for stock in risk_screen.selected)
    if len(selected_keys) != len(set(selected_keys)) or len({symbol for _, symbol in selected_keys}) != len(
        selected_keys
    ):
        raise InvalidKisOpportunityProjectionError("선별 후보 식별자가 중복됩니다")
    if any(key not in ranked_rows for key in selected_keys):
        raise InvalidKisOpportunityProjectionError("선별 후보가 동일 랭킹 발견 결과에 없습니다")
    if not risk_screen.selected:
        return None

    try:
        candidates = tuple(
            _candidate(stock, rank)
            for rank, stock in enumerate(risk_screen.selected, start=1)
        )
        evidence_refs = _evidence_refs(
            discovery,
            risk_screen=risk_screen,
            halt_snapshot=halt_snapshot,
            observed_at=observed_at,
        )
        source_coverage = _source_coverage(
            discovery,
            halt_snapshot=halt_snapshot,
            observed_at=observed_at,
        )
        return OpportunitySnapshot(
            opportunity_id=_opportunity_id(observed_at, selected_keys),
            strategy_lane=OPPORTUNITY_LANE,
            producer_strategy_version=PRODUCER_STRATEGY_VERSION,
            observed_at=observed_at,
            valid_until=observed_at + VALIDITY,
            candidates=candidates,
            evidence_refs=evidence_refs,
            source_coverage=source_coverage,
        )
    except (ValidationError, ArithmeticError, ValueError) as error:
        if isinstance(error, InvalidKisOpportunityProjectionError):
            raise
        raise InvalidKisOpportunityProjectionError("후보 또는 근거 값이 유효하지 않습니다") from error


def _validate_observations(
    observed_at: dt.datetime,
    halt_snapshot: HaltSnapshot,
    risk_screen: MarketRiskScreen,
) -> None:
    if (
        not _aware(observed_at)
        or not _aware(halt_snapshot.observed_at)
        or not _aware(risk_screen.observed_at)
        or halt_snapshot.observed_at != risk_screen.observed_at
        or halt_snapshot.observed_at > observed_at
        or risk_screen.observed_at > observed_at
    ):
        raise InvalidKisOpportunityProjectionError("관측시각의 인과관계가 맞지 않습니다")


def _validate_complete_discovery(discovery: RankingDiscovery) -> None:
    keys = tuple((group.source, group.exchange) for group in discovery.groups)
    if (
        discovery.failures
        or len(keys) != len(set(keys))
        or frozenset(keys) != _EXPECTED_RANKING_KEYS
    ):
        raise InvalidKisOpportunityProjectionError("6개 KIS 랭킹 요청이 모두 성공하지 않았습니다")


def _candidate(stock: KisRankedStock, rank: int) -> OpportunityCandidate:
    score = _finite_decimal(stock.change_pct)
    spread_bps = _finite_decimal(stock.spread_bps)
    if stock.price <= 0 or stock.volume < 0 or stock.dollar_volume < 0:
        raise InvalidKisOpportunityProjectionError("선별 후보 수치가 유효하지 않습니다")
    volume_to_adv = (
        "unavailable"
        if stock.average_daily_volume <= 0
        else _decimal_text(stock.volume / stock.average_daily_volume)
    )
    features = (
        FeatureValue(name="change_pct", value=_decimal_text(score)),
        FeatureValue(name="dollar_volume", value=_decimal_text(stock.dollar_volume)),
        FeatureValue(name="price", value=_decimal_text(stock.price)),
        FeatureValue(name="spread_bps", value=_decimal_text(spread_bps)),
        FeatureValue(name="volume", value=str(stock.volume)),
        FeatureValue(name="volume_to_adv", value=volume_to_adv),
    )
    return OpportunityCandidate(
        symbol=stock.symbol,
        rank=rank,
        score=score,
        features=features,
    )


def _evidence_refs(
    discovery: RankingDiscovery,
    *,
    risk_screen: MarketRiskScreen,
    halt_snapshot: HaltSnapshot,
    observed_at: dt.datetime,
) -> tuple[EvidenceRef, ...]:
    selected_keys = {(stock.exchange, stock.symbol) for stock in risk_screen.selected}
    evidence = [
        EvidenceRef(
            namespace="nyse/halts",
            record_id=halt_snapshot.observed_at.isoformat(),
            observed_at=halt_snapshot.observed_at,
        )
    ]
    for group in discovery.groups:
        for stock in group.stocks:
            if (stock.exchange, stock.symbol) in selected_keys:
                evidence.append(
                    EvidenceRef(
                        namespace="kis/ranking",
                        record_id=(
                            f"{group.source.value}:{group.exchange}:{stock.rank}:{stock.symbol}"
                        ),
                        observed_at=observed_at,
                    )
                )
    evidence.extend(
        EvidenceRef(
            namespace="kis/market_risk",
            record_id=f"{stock.exchange}:{stock.symbol}:selected",
            observed_at=risk_screen.observed_at,
        )
        for stock in risk_screen.selected
    )
    unique = {item.canonical_id: item for item in evidence}
    return tuple(unique[key] for key in sorted(unique))


def _source_coverage(
    discovery: RankingDiscovery,
    *,
    halt_snapshot: HaltSnapshot,
    observed_at: dt.datetime,
) -> tuple[SourceCoverage, ...]:
    coverage = [
        SourceCoverage(
            source_id=f"kis_{group.source.value}_{group.exchange.lower()}",
            observed_at=observed_at,
            record_count=len(group.stocks),
            complete=True,
        )
        for group in discovery.groups
    ]
    coverage.append(
        SourceCoverage(
            source_id="nyse_halts",
            observed_at=halt_snapshot.observed_at,
            record_count=len(halt_snapshot.symbols),
            complete=True,
        )
    )
    return tuple(sorted(coverage, key=lambda item: item.source_id))


def _opportunity_id(
    observed_at: dt.datetime,
    selected_keys: tuple[tuple[str, str], ...],
) -> str:
    coordinates = "|".join(f"{exchange}:{symbol}" for exchange, symbol in selected_keys)
    digest = hashlib.sha256(coordinates.encode("ascii")).hexdigest()[:12]
    utc_stamp = observed_at.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"us-opportunity-{utc_stamp}-{digest}"


def _finite_decimal(value: float) -> Decimal:
    if not math.isfinite(value):
        raise InvalidKisOpportunityProjectionError("무한대 또는 NaN 수치가 포함됐습니다")
    return Decimal(str(value))


def _decimal_text(value: Decimal | float) -> str:
    decimal_value = value if isinstance(value, Decimal) else _finite_decimal(value)
    return format(decimal_value.normalize(), "f")


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
