from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from trading_agent.kis_provider import KisRankedStock
from trading_agent.private_report import PRIVATE_REPORT_MODE, open_private_append

RANKING_FIELDS: Final = (
    "observed_at",
    "ranking_source",
    "exchange",
    "source_rank",
    "symbol",
    "name",
    "price",
    "change_pct",
    "bid",
    "ask",
    "spread_bps",
    "volume",
    "dollar_volume",
    "average_daily_volume",
    "selected",
    "selection_input",
)
RANKING_COVERAGE_FIELDS: Final = (
    "observed_at",
    "ranking_source",
    "exchange",
    "status",
    "row_count",
    "reason",
)


class RankingSource(StrEnum):
    UPDOWN = "updown"
    VOLUME = "volume"


@dataclass(frozen=True, slots=True)
class RankingGroup:
    source: RankingSource
    exchange: str
    stocks: tuple[KisRankedStock, ...]


@dataclass(frozen=True, slots=True)
class RankingFailure:
    source: RankingSource
    exchange: str
    reason: str


@dataclass(frozen=True, slots=True)
class RankingDiscovery:
    groups: tuple[RankingGroup, ...]
    failures: tuple[RankingFailure, ...]


@dataclass(frozen=True, slots=True)
class RankingSnapshot:
    observed_at: dt.datetime
    groups: tuple[RankingGroup, ...]
    selected: tuple[KisRankedStock, ...]


def append_ranking_snapshot(path: Path, snapshot: RankingSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_file(path)
    has_header = path.is_file() and path.stat().st_size > 0
    selected_keys = {(stock.exchange, stock.symbol) for stock in snapshot.selected}
    with open_private_append(path) as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(RANKING_FIELDS)
        for group in snapshot.groups:
            writer.writerows(
                (
                    snapshot.observed_at.isoformat(),
                    group.source.value,
                    stock.exchange,
                    stock.rank,
                    stock.symbol,
                    stock.name,
                    stock.price,
                    stock.change_pct,
                    stock.bid,
                    stock.ask,
                    stock.spread_bps,
                    stock.volume,
                    stock.dollar_volume,
                    stock.average_daily_volume,
                    (stock.exchange, stock.symbol) in selected_keys,
                    any(stock is chosen for chosen in snapshot.selected),
                )
                for stock in group.stocks
            )


def append_ranking_coverage(
    path: Path,
    observed_at: dt.datetime,
    discovery: RankingDiscovery,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    has_header = path.is_file() and path.stat().st_size > 0
    with open_private_append(path) as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(RANKING_COVERAGE_FIELDS)
        writer.writerows(
            (
                observed_at.isoformat(),
                group.source.value,
                group.exchange,
                "ok",
                len(group.stocks),
                "",
            )
            for group in discovery.groups
        )
        writer.writerows(
            (
                observed_at.isoformat(),
                failure.source.value,
                failure.exchange,
                "failed",
                "",
                failure.reason,
            )
            for failure in discovery.failures
        )


def _migrate_legacy_file(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if "selection_input" in fields:
            return
        rows = tuple(reader)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(*fields, "selection_input"),
        )
        writer.writeheader()
        writer.writerows({**row, "selection_input": ""} for row in rows)
    temporary.chmod(PRIVATE_REPORT_MODE)
    temporary.replace(path)
