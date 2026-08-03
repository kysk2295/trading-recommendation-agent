from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import os
import re
import stat
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Final, override

from trading_agent.market_context_breadth_producer import (
    BreadthMemberObservation,
    MarketContextBreadthProducerError,
    produce_market_context_from_breadth,
)
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.market_risk import MARKET_RISK_HEADER
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.research_agent_source_common import canonical_model_json
from trading_agent.research_agent_sources import ResearchAgentSourcePaths
from trading_agent.research_identity_models import MarketId
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_MAX_AGE: Final = dt.timedelta(minutes=3)
_MAX_BYTES: Final = 8 * 1024 * 1024
_MAX_ROWS: Final = 5_000
_SYMBOL: Final = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,14}$")


@dataclass(frozen=True, slots=True)
class MarketContextSupplyUnavailableError(RuntimeError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class InvalidMarketContextSupplyError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class MarketContextSupplyResult:
    created: bool
    snapshot: MarketContextSnapshot
    artifact_sha256: str
    source_row_count: int


@dataclass(frozen=True, slots=True)
class MarketContextSupplyCandidate:
    snapshot: MarketContextSnapshot
    canonical_payload: str
    source_row_count: int


@dataclass(frozen=True, slots=True)
class _RiskMember:
    observed_at: dt.datetime
    symbol: str
    change_pct: Decimal
    spread_bps: Decimal
    volume_to_adv: Decimal


def materialize_current_market_context(
    paths: ResearchAgentSourcePaths,
    now: dt.datetime,
) -> MarketContextSupplyResult:
    candidate = prepare_current_market_context(paths, now)
    artifact = paths.market_context_root / f"{candidate.snapshot.context_id}.market-context.json"
    try:
        created = publish_private_immutable_text(artifact, candidate.canonical_payload)
    except (InvalidPrivateImmutableFileError, OSError, ValueError):
        raise InvalidMarketContextSupplyError("market_context_publication_invalid") from None
    return MarketContextSupplyResult(
        created=created,
        snapshot=candidate.snapshot,
        artifact_sha256=hashlib.sha256(candidate.canonical_payload.encode()).hexdigest(),
        source_row_count=candidate.source_row_count,
    )


def prepare_current_market_context(
    paths: ResearchAgentSourcePaths,
    now: dt.datetime,
) -> MarketContextSupplyCandidate:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidMarketContextSupplyError("collection_time_invalid")
    current = now.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    if bounds is None or not bounds[0] <= current < bounds[1]:
        raise MarketContextSupplyUnavailableError("session_closed")
    session = paths.day_session_root / current.strftime("%Y%m%d")
    risk_screen = session / "market_risk_screen.csv"
    if not risk_screen.exists():
        raise MarketContextSupplyUnavailableError("current_risk_screen_unavailable")
    rows = _read_risk_screen(risk_screen, paths.day_session_root)
    latest_at = max(row.observed_at for row in rows)
    latest = tuple(row for row in rows if row.observed_at == latest_at)
    if latest_at.astimezone(NEW_YORK).date() != current.date():
        raise MarketContextSupplyUnavailableError("current_risk_screen_prior_date")
    if latest_at > now or now - latest_at > _MAX_AGE:
        raise MarketContextSupplyUnavailableError("current_risk_screen_stale")
    if len({row.symbol for row in latest}) != len(latest):
        raise InvalidMarketContextSupplyError("risk_screen_duplicate_symbol")
    members = tuple(
        BreadthMemberObservation(
            symbol=row.symbol,
            session_return_bps=_scaled_integer(row.change_pct, Decimal(100)),
            relative_volume_bps=_scaled_integer(row.volume_to_adv, Decimal(10_000)),
        )
        for row in latest
    )
    try:
        snapshot = produce_market_context_from_breadth(
            members,
            market_id=MarketId.US_EQUITIES,
            observed_at=latest_at,
            valid_until=latest_at + _MAX_AGE,
            source_record_count=len(latest),
        )
        payload = canonical_model_json(snapshot) + "\n"
    except (MarketContextBreadthProducerError, ValueError):
        raise InvalidMarketContextSupplyError("market_context_candidate_invalid") from None
    return MarketContextSupplyCandidate(
        snapshot=snapshot,
        canonical_payload=payload,
        source_row_count=len(latest),
    )


def _read_risk_screen(path: Path, root: Path) -> tuple[_RiskMember, ...]:
    if path != path.resolve() or root != root.resolve() or not path.is_relative_to(root):
        raise InvalidMarketContextSupplyError("risk_screen_boundary_invalid")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise InvalidMarketContextSupplyError("risk_screen_read_invalid") from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 0 < before.st_size <= _MAX_BYTES
        ):
            raise InvalidMarketContextSupplyError("risk_screen_private_file_invalid")
        payload = os.read(descriptor, _MAX_BYTES + 1)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise InvalidMarketContextSupplyError("risk_screen_changed_during_read")
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8"), newline=""))
        if tuple(reader.fieldnames or ()) != MARKET_RISK_HEADER:
            raise InvalidMarketContextSupplyError("risk_screen_header_invalid")
        raw_rows = tuple(reader)
    except (OSError, UnicodeError, csv.Error):
        raise InvalidMarketContextSupplyError("risk_screen_read_invalid") from None
    finally:
        os.close(descriptor)
    if not raw_rows or len(raw_rows) > _MAX_ROWS:
        raise InvalidMarketContextSupplyError("risk_screen_row_count_invalid")
    try:
        rows = tuple(_parse_row(row) for row in raw_rows)
    except (InvalidOperation, KeyError, TypeError, ValueError):
        raise InvalidMarketContextSupplyError("risk_screen_row_invalid") from None
    return rows


def _parse_row(row: dict[str, str | None]) -> _RiskMember:
    observed = dt.datetime.fromisoformat(row["observed_at"] or "")
    symbol = row["symbol"] or ""
    change = Decimal(row["change_pct"] or "")
    spread = Decimal(row["spread_bps"] or "")
    relative_volume = Decimal(row["volume_to_adv"] or "")
    if (
        observed.tzinfo is None
        or observed.utcoffset() is None
        or _SYMBOL.fullmatch(symbol) is None
        or not all(value.is_finite() for value in (change, spread, relative_volume))
        or spread < 0
        or relative_volume < 0
    ):
        raise InvalidMarketContextSupplyError("risk_screen_row_invalid")
    return _RiskMember(observed, symbol, change, spread, relative_volume)


def _scaled_integer(value: Decimal, scale: Decimal) -> int:
    return int((value * scale).to_integral_value(rounding=ROUND_HALF_EVEN))


__all__ = (
    "InvalidMarketContextSupplyError",
    "MarketContextSupplyCandidate",
    "MarketContextSupplyResult",
    "MarketContextSupplyUnavailableError",
    "materialize_current_market_context",
    "prepare_current_market_context",
)
