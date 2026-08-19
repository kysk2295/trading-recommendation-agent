from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from trading_agent.day_research_attempt_binding import DayResearchAttemptBinding
from trading_agent.day_research_ledger import (
    InvalidDayResearchLedgerSourceError,
    StoredDayHypothesisFamily,
    StoredDayHypothesisVersion,
    _all_stored_bindings,
    _require_stored_family_graph,
    _require_stored_version_graph,
    _stored_attempt,
    _stored_family,
    _stored_version,
    _version_by_id,
    require_same_market,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_results import ResearchAttempt


@dataclass(frozen=True, slots=True)
class DayResearchAttemptForReview:
    binding: DayResearchAttemptBinding
    attempt: ResearchAttempt


def day_hypothesis_families(
    connection: sqlite3.Connection,
) -> tuple[StoredDayHypothesisFamily, ...]:
    rows: list[tuple[str, str, str | None, str, str]] = connection.execute(
        """SELECT family_key,family_id,parent_family_id,created_at,payload_json
        FROM day_hypothesis_families ORDER BY rowid"""
    ).fetchall()
    families = tuple(_stored_family(row) for row in rows)
    by_id = {stored.family.family_id: stored for stored in families}
    if len(by_id) != len(families):
        raise InvalidDayResearchLedgerSourceError("stored_day_family_identity_duplicate")
    _require_stored_family_graph(by_id)
    return families


def day_hypothesis_family(
    connection: sqlite3.Connection,
    family_id: str,
) -> StoredDayHypothesisFamily | None:
    return next(
        (stored for stored in day_hypothesis_families(connection) if stored.family.family_id == family_id),
        None,
    )


def day_hypothesis_versions(
    connection: sqlite3.Connection,
    family_id: str | None = None,
    market_id: MarketId | None = None,
) -> tuple[StoredDayHypothesisVersion, ...]:
    rows: list[tuple[str, str, str, str | None, str, str, str, str, str]] = connection.execute(
        """SELECT version_key,hypothesis_version_id,family_id,parent_version_id,
        market_id,created_at,registration_completed_bar_at,first_shadow_eligible_at,payload_json
        FROM day_hypothesis_versions ORDER BY rowid"""
    ).fetchall()
    versions = tuple(_stored_version(row) for row in rows)
    families = {stored.family.family_id: stored for stored in day_hypothesis_families(connection)}
    by_id = {stored.version.hypothesis_version_id: stored for stored in versions}
    if len(by_id) != len(versions):
        raise InvalidDayResearchLedgerSourceError("stored_day_version_identity_duplicate")
    _require_stored_version_graph(families, by_id)
    return tuple(
        stored
        for stored in versions
        if (family_id is None or stored.version.family_id == family_id)
        and (market_id is None or stored.version.market_id is market_id)
    )


def day_hypothesis_version(
    connection: sqlite3.Connection,
    version_id: str,
) -> StoredDayHypothesisVersion | None:
    return next(
        (
            stored
            for stored in day_hypothesis_versions(connection)
            if stored.version.hypothesis_version_id == version_id
        ),
        None,
    )


def read_day_attempts_for_review(
    connection: sqlite3.Connection,
    market_id: MarketId,
    hypothesis_version_id: str,
) -> tuple[DayResearchAttemptForReview, ...]:
    stored_version = _version_by_id(connection, hypothesis_version_id)
    if stored_version is None:
        raise InvalidDayResearchLedgerSourceError("day_research_attempt_binding_version_missing")
    version = stored_version.version
    require_same_market(version.market_id, market_id)
    bindings = tuple(
        stored.binding
        for stored in _all_stored_bindings(connection)
        if stored.binding.hypothesis_version_id == hypothesis_version_id
    )
    records = tuple(
        DayResearchAttemptForReview(binding=binding, attempt=_required_stored_attempt(connection, binding.attempt_id))
        for binding in bindings
    )
    if any(record.binding.market_id is not market_id for record in records):
        raise InvalidDayResearchLedgerSourceError("stored_day_attempt_binding_market_invalid")
    return tuple(
        sorted(
            records,
            key=lambda record: (record.binding.bound_at, record.attempt.branch_index, record.attempt.attempt_id),
        )
    )


def _required_stored_attempt(connection: sqlite3.Connection, attempt_id: str) -> ResearchAttempt:
    attempt = _stored_attempt(connection, attempt_id)
    if attempt is None:
        raise InvalidDayResearchLedgerSourceError("stored_day_attempt_binding_attempt_missing")
    return attempt
