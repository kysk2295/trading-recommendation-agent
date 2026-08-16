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
    subject_refs: tuple[str, ...]


type DaySourceAdmissionResult = DaySourceAdmission | PrimaryAdmissionFailure


@dataclass(frozen=True, slots=True)
class _DayDatabaseFacts:
    checkpoint_count: int
    event_count: int
    recommendation_count: int
    latest_checkpoint_at: dt.datetime | None
    checkpoints: tuple[dict[str, object], ...]
    recommendations: tuple[dict[str, object], ...]
    subject_refs: tuple[str, ...]


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
    prepared = _prepare_day_source(database, risk_screen)
    if isinstance(prepared, PrimaryAdmissionFailure):
        return prepared
    admission, timestamps = prepared
    dated = _dated_failure(now, timestamps)
    if dated is not None:
        return dated
    if any(timestamp > now or now - timestamp > _PRIMARY_MAX_FEED_DELAY for timestamp in timestamps):
        return PrimaryAdmissionFailure.STALE
    return admission


def day_research_admission(
    database: Path,
    risk_screen: Path,
    now: dt.datetime,
) -> DaySourceAdmissionResult:
    prepared = _prepare_day_source(database, risk_screen)
    if isinstance(prepared, PrimaryAdmissionFailure):
        return prepared
    admission, timestamps = prepared
    if any(timestamp > now for timestamp in timestamps):
        return PrimaryAdmissionFailure.STALE
    return admission


def _prepare_day_source(
    database: Path,
    risk_screen: Path,
) -> tuple[DaySourceAdmission, tuple[dt.datetime, dt.datetime]] | PrimaryAdmissionFailure:
    database_facts = _read_day_database(database)
    if database_facts.latest_checkpoint_at is None:
        return PrimaryAdmissionFailure.COMPLETED_BAR_UNAVAILABLE
    risk_rows = _read_risk_rows(risk_screen)
    if not risk_rows:
        return PrimaryAdmissionFailure.MISSING_SPREAD
    latest_risk_at = max(row.observed_at for row in risk_rows)
    latest_risk_rows = tuple(row for row in risk_rows if row.observed_at == latest_risk_at)
    if not all(math.isfinite(row.spread_bps) and row.spread_bps >= 0 for row in latest_risk_rows):
        return PrimaryAdmissionFailure.MISSING_SPREAD
    database_sha256 = _file_sha256(database)
    risk_sha256 = _file_sha256(risk_screen)
    observed_at = max(database_facts.latest_checkpoint_at, latest_risk_at)
    payload = canonical_payload_json(
        {
            "checkpoint_count": database_facts.checkpoint_count,
            "checkpoints": database_facts.checkpoints,
            "database_sha256": database_sha256,
            "event_count": database_facts.event_count,
            "latest_checkpoint_at": database_facts.latest_checkpoint_at.isoformat(),
            "latest_risk_at": latest_risk_at.isoformat(),
            "recommendation_count": database_facts.recommendation_count,
            "recommendations": database_facts.recommendations,
            "risk_sha256": risk_sha256,
            "session": database.parent.name,
        }
    )
    return (
        DaySourceAdmission(
            observed_at=observed_at,
            canonical_payload=payload,
            provenance_sha256=tuple(sorted((database_sha256, risk_sha256))),
            subject_refs=database_facts.subject_refs,
        ),
        (database_facts.latest_checkpoint_at, latest_risk_at),
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
        checkpoint_rows = connection.execute(
            "SELECT symbol,processed_at,last_close FROM bar_checkpoints "
            "ORDER BY processed_at DESC,symbol LIMIT 32"
        ).fetchall()
        recommendation_rows = connection.execute(
            "SELECT recommendation_id,symbol,strategy,created_at,entry,stop,"
            "target_1r,target_2r,state,rationale FROM recommendations "
            "ORDER BY created_at DESC,symbol LIMIT 32"
        ).fetchall()
        event_rows = connection.execute(
            "SELECT event_id,recommendation_id,occurred_at,state,price,note "
            "FROM events ORDER BY event_id DESC LIMIT 32"
        ).fetchall()
    if integrity != ("ok",) or recommendations is None or checkpoints is None or events is None:
        raise sqlite3.DatabaseError
    checkpoint_at = None if checkpoints[1] is None else dt.datetime.fromisoformat(str(checkpoints[1]))
    if checkpoint_at is not None and (checkpoint_at.tzinfo is None or checkpoint_at.utcoffset() is None):
        raise ValueError
    bounded_events: dict[str, list[dict[str, object]]] = {}
    event_subjects: list[str] = []
    for row in reversed(event_rows):
        recommendation_id = str(row[1])
        bounded_events.setdefault(recommendation_id, []).append(
            {
                "event_id": int(row[0]),
                "note": str(row[5]),
                "occurred_at": _aware_iso(str(row[2])),
                "price": None if row[4] is None else float(row[4]),
                "state": str(row[3]),
            }
        )
        event_subjects.append(_day_subject("event", f"{recommendation_id}:{int(row[0])}"))
    bounded_recommendations = tuple(
        {
            "created_at": _aware_iso(str(row[3])),
            "entry": float(row[4]),
            "events": bounded_events.get(str(row[0]), []),
            "rationale": str(row[9]),
            "recommendation_id": str(row[0]),
            "state": str(row[8]),
            "stop": float(row[5]),
            "strategy": str(row[2]),
            "symbol": str(row[1]),
            "target_1r": float(row[6]),
            "target_2r": float(row[7]),
        }
        for row in reversed(recommendation_rows)
    )
    bounded_checkpoints = tuple(
        {
            "last_close": float(row[2]),
            "processed_at": _aware_iso(str(row[1])),
            "symbol": str(row[0]),
        }
        for row in reversed(checkpoint_rows)
    )
    recommendation_subjects = [
        _day_subject("recommendation", str(row[0])) for row in reversed(recommendation_rows)
    ]
    return _DayDatabaseFacts(
        checkpoint_count=int(checkpoints[0]),
        event_count=int(events[0]),
        recommendation_count=int(recommendations[0]),
        latest_checkpoint_at=checkpoint_at,
        checkpoints=bounded_checkpoints,
        recommendations=bounded_recommendations,
        subject_refs=tuple((*recommendation_subjects, *event_subjects)[:31]),
    )


def _aware_iso(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.isoformat()


def _day_subject(kind: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return f"day_{kind}.{digest}"


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
    "day_research_admission",
    "day_source_admission",
    "market_context_admission",
    "opportunity_admission",
    "primary_session_failure",
)
