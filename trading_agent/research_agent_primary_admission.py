from __future__ import annotations

import csv
import datetime as dt
import hashlib
import math
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Final

from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.research_agent_source_common import canonical_payload_json
from trading_agent.signal_contract_models import FeatureValue, OpportunitySnapshot
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_PRIMARY_MAX_FEED_DELAY: Final[dt.timedelta] = dt.timedelta(minutes=3)
_MARKET_RISK_HEADER: Final[tuple[str, ...]] = (
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


class PrimaryAdmissionFailure(StrEnum):
    SESSION_CLOSED = "session_closed"
    PRIOR_DATE = "prior_date"
    STALE = "stale"
    MISSING_SPREAD = "missing_spread"
    COMPLETED_BAR_UNAVAILABLE = "completed_bar_unavailable"


@dataclass(frozen=True, slots=True)
class DaySourceAdmission:
    observed_at: dt.datetime
    canonical_payload: str
    provenance_sha256: tuple[str, ...]


type DaySourceAdmissionResult = DaySourceAdmission | PrimaryAdmissionFailure


@dataclass(frozen=True, slots=True)
class _DayDatabaseFacts:
    checkpoint_count: int
    event_count: int
    recommendation_count: int
    latest_checkpoint_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class _RiskRow:
    observed_at: dt.datetime
    spread_bps: float


def primary_session_failure(now: dt.datetime) -> PrimaryAdmissionFailure | None:
    current = now.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    if bounds is None or not bounds[0] <= current < bounds[1]:
        return PrimaryAdmissionFailure.SESSION_CLOSED
    return None


def opportunity_admission(
    snapshot: OpportunitySnapshot,
    now: dt.datetime,
) -> PrimaryAdmissionFailure | None:
    dated = _dated_failure(
        now,
        (
            snapshot.observed_at,
            *(reference.observed_at for reference in snapshot.evidence_refs),
            *(coverage.observed_at for coverage in snapshot.source_coverage),
        ),
    )
    if dated is not None:
        return dated
    if snapshot.observed_at > now or snapshot.valid_until < now:
        return PrimaryAdmissionFailure.STALE
    if not all(_spread_is_usable(candidate.features) for candidate in snapshot.candidates):
        return PrimaryAdmissionFailure.MISSING_SPREAD
    return None


def market_context_admission(
    snapshot: MarketContextSnapshot,
    now: dt.datetime,
) -> PrimaryAdmissionFailure | None:
    dated = _dated_failure(
        now,
        (snapshot.observed_at, *(coverage.observed_at for coverage in snapshot.coverage)),
    )
    if dated is not None:
        return dated
    if snapshot.observed_at > now or snapshot.valid_until < now:
        return PrimaryAdmissionFailure.STALE
    return None


def day_source_admission(
    database: Path,
    risk_screen: Path,
    now: dt.datetime,
) -> DaySourceAdmissionResult:
    database_facts = _read_day_database(database)
    if database_facts.latest_checkpoint_at is None:
        return PrimaryAdmissionFailure.COMPLETED_BAR_UNAVAILABLE
    risk_rows = _read_risk_rows(risk_screen)
    if not risk_rows:
        return PrimaryAdmissionFailure.MISSING_SPREAD
    latest_risk_at = max(row.observed_at for row in risk_rows)
    latest_risk_rows = tuple(row for row in risk_rows if row.observed_at == latest_risk_at)
    dated = _dated_failure(now, (database_facts.latest_checkpoint_at, latest_risk_at))
    if dated is not None:
        return dated
    if any(
        timestamp > now or now - timestamp > _PRIMARY_MAX_FEED_DELAY
        for timestamp in (database_facts.latest_checkpoint_at, latest_risk_at)
    ):
        return PrimaryAdmissionFailure.STALE
    if not all(math.isfinite(row.spread_bps) and row.spread_bps >= 0 for row in latest_risk_rows):
        return PrimaryAdmissionFailure.MISSING_SPREAD
    database_sha256 = _file_sha256(database)
    risk_sha256 = _file_sha256(risk_screen)
    observed_at = max(database_facts.latest_checkpoint_at, latest_risk_at)
    payload = canonical_payload_json(
        {
            "checkpoint_count": database_facts.checkpoint_count,
            "database_sha256": database_sha256,
            "event_count": database_facts.event_count,
            "latest_checkpoint_at": database_facts.latest_checkpoint_at.isoformat(),
            "latest_risk_at": latest_risk_at.isoformat(),
            "recommendation_count": database_facts.recommendation_count,
            "risk_sha256": risk_sha256,
            "session": database.parent.name,
        }
    )
    return DaySourceAdmission(
        observed_at=observed_at,
        canonical_payload=payload,
        provenance_sha256=tuple(sorted((database_sha256, risk_sha256))),
    )


def _dated_failure(
    now: dt.datetime,
    timestamps: tuple[dt.datetime, ...],
) -> PrimaryAdmissionFailure | None:
    current_date = now.astimezone(NEW_YORK).date()
    if any(timestamp.astimezone(NEW_YORK).date() != current_date for timestamp in timestamps):
        return PrimaryAdmissionFailure.PRIOR_DATE
    return None


def _spread_is_usable(features: tuple[FeatureValue, ...]) -> bool:
    values = tuple(feature.value for feature in features if feature.name == "spread_bps")
    if len(values) != 1:
        return False
    try:
        spread = Decimal(values[0])
    except InvalidOperation:
        return False
    return spread.is_finite() and spread >= 0


def _read_day_database(database: Path) -> _DayDatabaseFacts:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        _ = connection.execute("PRAGMA query_only=ON")
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        recommendations = connection.execute("SELECT COUNT(*) FROM recommendations").fetchone()
        checkpoints = connection.execute("SELECT COUNT(*),MAX(processed_at) FROM bar_checkpoints").fetchone()
        events = connection.execute("SELECT COUNT(*) FROM events").fetchone()
    if integrity != ("ok",) or recommendations is None or checkpoints is None or events is None:
        raise sqlite3.DatabaseError
    checkpoint_at = None if checkpoints[1] is None else dt.datetime.fromisoformat(str(checkpoints[1]))
    if checkpoint_at is not None and (checkpoint_at.tzinfo is None or checkpoint_at.utcoffset() is None):
        raise ValueError
    return _DayDatabaseFacts(
        checkpoint_count=int(checkpoints[0]),
        event_count=int(events[0]),
        recommendation_count=int(recommendations[0]),
        latest_checkpoint_at=checkpoint_at,
    )


def _read_risk_rows(path: Path) -> tuple[_RiskRow, ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if "spread_bps" not in fields:
            return ()
        if fields != _MARKET_RISK_HEADER:
            raise ValueError
        rows = tuple(reader)
    parsed: list[_RiskRow] = []
    for row in rows:
        observed_at = dt.datetime.fromisoformat(row["observed_at"])
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError
        raw_spread = row["spread_bps"]
        spread = float("nan") if raw_spread is None or not raw_spread.strip() else float(raw_spread)
        parsed.append(_RiskRow(observed_at=observed_at, spread_bps=spread))
    return tuple(parsed)


def _file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


__all__: Final = (
    "DaySourceAdmission",
    "DaySourceAdmissionResult",
    "PrimaryAdmissionFailure",
    "day_source_admission",
    "market_context_admission",
    "opportunity_admission",
    "primary_session_failure",
)
