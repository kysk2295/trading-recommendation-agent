from __future__ import annotations

import csv
import datetime as dt
import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from io import StringIO
from pathlib import Path
from typing import Final, final, override

import httpx2

from trading_agent.kis_provider import KisRankedStock, select_ranked_stocks
from trading_agent.private_report import PRIVATE_REPORT_MODE, open_private_append

NYSE_CURRENT_HALTS_URL: Final = "https://www.nyse.com/api/trade-halts/current/download"
PORTFOLIO_LIMIT_REASON: Final = "포트폴리오 한도"
MARKET_RISK_HEADER: Final = (
    "observed_at",
    "exchange",
    "symbol",
    "selected",
    "reason",
    "change_pct",
    "price",
    "bid",
    "ask",
    "spread_bps",
    "estimated_round_trip_cost_bps",
    "dollar_volume",
    "volume",
    "average_daily_volume",
    "volume_to_adv",
)
HALT_HEADER: Final = (
    "Halt Date",
    "Halt Time",
    "Symbol",
    "Name",
    "Exchange",
    "Reason",
    "Resume Date",
    "NYSE Resume Time",
)


class RiskRejectReason(StrEnum):
    ACTIVE_HALT = "공식 현재 거래정지"
    MISSING_QUOTE = "유효 호가 없음"
    CROSSED_QUOTE = "역전 호가"
    WIDE_SPREAD = "스프레드 초과"
    ESTIMATED_COST = "스프레드+슬리피지 한도 초과"


@dataclass(frozen=True, slots=True)
class HaltFeedFormatError(ValueError):
    actual_header: tuple[str, ...]

    @override
    def __str__(self) -> str:
        return f"NYSE 현재 거래정지 CSV 형식이 변경됐습니다: {self.actual_header!r}"


class MarketRiskFileFormatError(ValueError):
    def __init__(self, path: Path, actual_header: tuple[str, ...]) -> None:
        super().__init__(path, actual_header)
        self.path = path
        self.actual_header = actual_header

    @override
    def __str__(self) -> str:
        return f"시장위험 CSV 형식이 변경됐습니다 ({self.path}): {self.actual_header!r}"


@dataclass(frozen=True, slots=True)
class HaltSnapshot:
    observed_at: dt.datetime
    symbols: frozenset[str]


@dataclass(frozen=True, slots=True)
class MarketRiskConfig:
    max_spread_bps: float = 100.0
    slippage_per_side_bps: float = 20.0
    max_round_trip_cost_bps: float = 140.0


@dataclass(frozen=True, slots=True)
class MarketRiskRejection:
    stock: KisRankedStock
    reason: RiskRejectReason
    estimated_round_trip_cost_bps: float


@dataclass(frozen=True, slots=True)
class MarketRiskScreen:
    observed_at: dt.datetime
    config: MarketRiskConfig
    selected: tuple[KisRankedStock, ...]
    not_selected: tuple[KisRankedStock, ...]
    rejected: tuple[MarketRiskRejection, ...]


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def fetch_active_halts(
    client: httpx2.Client,
    clock: Callable[[], dt.datetime] = _utc_now,
) -> HaltSnapshot:
    response = client.get(NYSE_CURRENT_HALTS_URL)
    _ = response.raise_for_status()
    reader = csv.reader(StringIO(response.text.lstrip("\ufeff")))
    header = tuple(next(reader, ()))
    if header != HALT_HEADER:
        raise HaltFeedFormatError(actual_header=header)
    symbols: set[str] = set()
    for row in reader:
        values = tuple(row)
        if len(values) != len(HALT_HEADER):
            raise HaltFeedFormatError(actual_header=values)
        symbol = values[2].strip().upper()
        if symbol:
            symbols.add(symbol)
    return HaltSnapshot(clock(), frozenset(symbols))


@final
class MarketRiskGate:
    def __init__(self, halts: HaltSnapshot, config: MarketRiskConfig) -> None:
        self.halts = halts
        self.config = config

    def screen(
        self,
        groups: tuple[tuple[KisRankedStock, ...], ...],
        limit: int,
    ) -> MarketRiskScreen:
        ranked = select_ranked_stocks(groups, sum(len(group) for group in groups))
        selected: list[KisRankedStock] = []
        not_selected: list[KisRankedStock] = []
        rejected: list[MarketRiskRejection] = []
        for stock in ranked:
            reason = self._reject_reason(stock)
            if reason is None:
                target = selected if len(selected) < limit else not_selected
                target.append(stock)
                continue
            rejected.append(
                MarketRiskRejection(
                    stock,
                    reason,
                    self.estimated_round_trip_cost_bps(stock),
                )
            )
        return MarketRiskScreen(
            self.halts.observed_at,
            self.config,
            tuple(selected),
            tuple(not_selected),
            tuple(rejected),
        )

    def estimated_round_trip_cost_bps(self, stock: KisRankedStock) -> float:
        return stock.spread_bps + self.config.slippage_per_side_bps * 2.0

    def _reject_reason(self, stock: KisRankedStock) -> RiskRejectReason | None:
        if stock.symbol.upper() in self.halts.symbols:
            return RiskRejectReason.ACTIVE_HALT
        if stock.bid <= 0.0 or stock.ask <= 0.0:
            return RiskRejectReason.MISSING_QUOTE
        if stock.ask < stock.bid:
            return RiskRejectReason.CROSSED_QUOTE
        spread = stock.spread_bps
        if not math.isfinite(spread):
            return RiskRejectReason.MISSING_QUOTE
        if spread > self.config.max_spread_bps:
            return RiskRejectReason.WIDE_SPREAD
        if self.estimated_round_trip_cost_bps(stock) > self.config.max_round_trip_cost_bps:
            return RiskRejectReason.ESTIMATED_COST
        return None


def write_market_risk_screen(path: Path, screen: MarketRiskScreen) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_file(path)
    has_header = path.is_file() and path.stat().st_size > 0
    with open_private_append(path) as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(MARKET_RISK_HEADER)
        for stock in screen.selected:
            writer.writerow(
                (
                    screen.observed_at.isoformat(),
                    stock.exchange,
                    stock.symbol,
                    True,
                    "",
                    stock.change_pct,
                    stock.price,
                    stock.bid,
                    stock.ask,
                    stock.spread_bps,
                    stock.spread_bps + screen.config.slippage_per_side_bps * 2.0,
                    stock.dollar_volume,
                    *_volume_features(stock),
                )
            )
        for stock in screen.not_selected:
            writer.writerow(
                (
                    screen.observed_at.isoformat(),
                    stock.exchange,
                    stock.symbol,
                    False,
                    PORTFOLIO_LIMIT_REASON,
                    stock.change_pct,
                    stock.price,
                    stock.bid,
                    stock.ask,
                    stock.spread_bps,
                    stock.spread_bps + screen.config.slippage_per_side_bps * 2.0,
                    stock.dollar_volume,
                    *_volume_features(stock),
                )
            )
        for rejection in screen.rejected:
            stock = rejection.stock
            writer.writerow(
                (
                    screen.observed_at.isoformat(),
                    stock.exchange,
                    stock.symbol,
                    False,
                    rejection.reason.value,
                    stock.change_pct,
                    stock.price,
                    stock.bid,
                    stock.ask,
                    stock.spread_bps,
                    rejection.estimated_round_trip_cost_bps,
                    stock.dollar_volume,
                    *_volume_features(stock),
                )
            )


def _volume_features(stock: KisRankedStock) -> tuple[int, int, float | None]:
    ratio = None if stock.average_daily_volume <= 0 else stock.volume / stock.average_daily_volume
    return stock.volume, stock.average_daily_volume, ratio


def _migrate_legacy_file(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader, ()))
        if header == MARKET_RISK_HEADER:
            return
        if header != MARKET_RISK_HEADER[:-3]:
            raise MarketRiskFileFormatError(path, header)
        rows = tuple(tuple(row) for row in reader)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MARKET_RISK_HEADER)
        writer.writerows((*row, "", "", "") for row in rows)
    temporary.chmod(PRIVATE_REPORT_MODE)
    temporary.replace(path)
