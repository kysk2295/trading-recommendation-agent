from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3

from trading_agent.sec_edgar_models import SecFilingEvent
from trading_agent.sec_edgar_store_types import InvalidSecEdgarStoreError


def require_all_version_chains(connection: sqlite3.Connection) -> None:
    accessions = connection.execute(
        "SELECT cik,accession_number,MIN(version_id) FROM sec_filing_versions "
        "GROUP BY cik,accession_number"
    ).fetchall()
    for cik, accession_number, target_version_id in accessions:
        require_version_chain(connection, target_version_id, cik, accession_number)


def require_version_chain(
    connection: sqlite3.Connection,
    target_version_id: str,
    cik: str,
    accession_number: str,
) -> None:
    rows = connection.execute(
        "SELECT v.version_id,v.event_id,v.previous_version_id,v.payload_sha256,v.payload_json,"
        "MIN(o.observed_at),MAX(o.observed_at) FROM sec_filing_versions v "
        "LEFT JOIN sec_filing_observations o ON o.version_id=v.version_id "
        "WHERE v.cik=? AND v.accession_number=? GROUP BY v.version_id",
        (cik, accession_number),
    ).fetchall()
    if not rows:
        raise InvalidSecEdgarStoreError
    nodes = {row[0]: row for row in rows}
    children: dict[str, list[str]] = {}
    roots: list[str] = []
    observation_bounds: dict[str, tuple[dt.datetime, dt.datetime]] = {}
    for row in rows:
        version_id, event_id, previous_id, payload_sha, payload_json, first_seen, last_seen = row
        event = SecFilingEvent.model_validate_json(payload_json)
        if (
            event.event_id != event_id
            or event.cik != cik
            or event.accession_number != accession_number
            or hashlib.sha256(payload_json.encode()).hexdigest() != payload_sha
            or _version_identity(previous_id, event_id) != version_id
            or first_seen is None
            or last_seen is None
        ):
            raise InvalidSecEdgarStoreError
        first_observed = dt.datetime.fromisoformat(first_seen)
        last_observed = dt.datetime.fromisoformat(last_seen)
        if event.accepted_at > first_observed or last_observed < first_observed:
            raise InvalidSecEdgarStoreError
        observation_bounds[version_id] = (first_observed, last_observed)
        if previous_id is None:
            roots.append(version_id)
        else:
            children.setdefault(previous_id, []).append(version_id)
    if len(roots) != 1 or target_version_id not in nodes:
        raise InvalidSecEdgarStoreError
    visited: set[str] = set()
    current = roots[0]
    previous_last: dt.datetime | None = None
    while True:
        if current in visited or current not in nodes:
            raise InvalidSecEdgarStoreError
        first_observed, last_observed = observation_bounds[current]
        if previous_last is not None and first_observed < previous_last:
            raise InvalidSecEdgarStoreError
        visited.add(current)
        descendants = children.get(current, [])
        if not descendants:
            break
        if len(descendants) != 1:
            raise InvalidSecEdgarStoreError
        previous_last = last_observed
        current = descendants[0]
    if visited != set(nodes):
        raise InvalidSecEdgarStoreError


def _version_identity(previous_id: str | None, event_id: str) -> str:
    return hashlib.sha256(f"sec-filing-version|{previous_id or 'root'}|{event_id}".encode()).hexdigest()
