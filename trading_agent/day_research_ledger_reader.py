from __future__ import annotations

import sqlite3

from trading_agent.day_research_ledger import (
    InvalidDayResearchLedgerSourceError,
    StoredDayHypothesisFamily,
    StoredDayHypothesisVersion,
    _require_stored_family_parent,
    _require_stored_version_lineage,
    _stored_family,
    _stored_version,
)
from trading_agent.research_identity_models import MarketId


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
    for stored in families:
        _require_stored_family_parent(stored, by_id)
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
    for stored in versions:
        _require_stored_version_lineage(stored, families, by_id)
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
