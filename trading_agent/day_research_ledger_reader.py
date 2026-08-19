from __future__ import annotations

import sqlite3

from trading_agent.day_research_ledger import (
    StoredDayHypothesisFamily,
    StoredDayHypothesisVersion,
    read_day_hypothesis_families,
    read_day_hypothesis_versions,
)
from trading_agent.research_identity_models import MarketId


def day_hypothesis_families(
    connection: sqlite3.Connection,
) -> tuple[StoredDayHypothesisFamily, ...]:
    return read_day_hypothesis_families(connection)


def day_hypothesis_family(
    connection: sqlite3.Connection,
    family_id: str,
) -> StoredDayHypothesisFamily | None:
    return next(
        (stored for stored in read_day_hypothesis_families(connection) if stored.family.family_id == family_id),
        None,
    )


def day_hypothesis_versions(
    connection: sqlite3.Connection,
    family_id: str | None = None,
    market_id: MarketId | None = None,
) -> tuple[StoredDayHypothesisVersion, ...]:
    return tuple(
        stored
        for stored in read_day_hypothesis_versions(connection)
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
            for stored in read_day_hypothesis_versions(connection)
            if stored.version.hypothesis_version_id == version_id
        ),
        None,
    )
