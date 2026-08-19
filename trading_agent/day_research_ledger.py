from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from trading_agent.day_hypothesis_models import HypothesisFamily, HypothesisVersion
from trading_agent.experiment_ledger_keys import (
    DayHypothesisFamilyKey,
    DayHypothesisVersionKey,
    canonical_experiment_ledger_json,
    day_hypothesis_family_key,
    day_hypothesis_version_key,
)
from trading_agent.research_identity_models import MarketId


@dataclass(frozen=True, slots=True)
class DayResearchLedgerConflictError(RuntimeError):
    reason: str = "day_research_immutable_identity_conflict"

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class InvalidDayResearchLedgerSourceError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class StoredDayHypothesisFamily:
    family_key: DayHypothesisFamilyKey
    family: HypothesisFamily


@dataclass(frozen=True, slots=True)
class StoredDayHypothesisVersion:
    version_key: DayHypothesisVersionKey
    version: HypothesisVersion


def require_same_market(parent_market: MarketId, child_market: MarketId) -> None:
    if parent_market is not child_market:
        raise InvalidDayResearchLedgerSourceError("day_research_cross_market_reference")


def register_day_hypothesis_family(
    connection: sqlite3.Connection,
    family: HypothesisFamily,
) -> bool:
    family_id = _safe_family_identity(family)
    existing = _family_by_id(connection, family_id)
    if existing is not None:
        checked = _validated_family_or_conflict(family)
        key = day_hypothesis_family_key(checked)
        if existing.family_key == key and existing.family == checked:
            return False
        raise DayResearchLedgerConflictError
    checked = _validated_family(family)
    _require_family_parent(connection, checked)
    key = day_hypothesis_family_key(checked)
    try:
        _ = connection.execute(
            "INSERT INTO day_hypothesis_families VALUES (?,?,?,?,?)",
            (
                key,
                checked.family_id,
                checked.parent_family_id,
                checked.created_at.isoformat(),
                canonical_experiment_ledger_json(checked),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise DayResearchLedgerConflictError from error
    return True


def register_day_hypothesis_version(
    connection: sqlite3.Connection,
    version: HypothesisVersion,
) -> bool:
    version_id = _safe_version_identity(version)
    existing = _version_by_id(connection, version_id)
    if existing is not None:
        checked = _validated_version_or_conflict(version)
        key = day_hypothesis_version_key(checked)
        if existing.version_key == key and existing.version == checked:
            return False
        raise DayResearchLedgerConflictError
    checked = _validated_version(version)
    _require_version_lineage(connection, checked)
    key = day_hypothesis_version_key(checked)
    try:
        _ = connection.execute(
            "INSERT INTO day_hypothesis_versions VALUES (?,?,?,?,?,?,?,?,?)",
            (
                key,
                checked.hypothesis_version_id,
                checked.family_id,
                checked.parent_version_id,
                checked.market_id.value,
                checked.created_at.isoformat(),
                checked.registration_completed_bar_at.isoformat(),
                checked.first_shadow_eligible_at.isoformat(),
                canonical_experiment_ledger_json(checked),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise DayResearchLedgerConflictError from error
    return True


def _validated_family(family: HypothesisFamily) -> HypothesisFamily:
    try:
        return HypothesisFamily.model_validate(family.model_dump(mode="python"))
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("invalid_day_hypothesis_family") from None


def _safe_family_identity(family: HypothesisFamily) -> str:
    match family.__dict__.get("family_id"):
        case str() as family_id if len(family_id) == 64 and all(
            character in "0123456789abcdef" for character in family_id
        ):
            return family_id
        case _:
            raise InvalidDayResearchLedgerSourceError("invalid_day_hypothesis_family")


def _validated_family_or_conflict(family: HypothesisFamily) -> HypothesisFamily:
    try:
        return _validated_family(family)
    except InvalidDayResearchLedgerSourceError:
        raise DayResearchLedgerConflictError from None


def _validated_version(version: HypothesisVersion) -> HypothesisVersion:
    try:
        return HypothesisVersion.model_validate(version.model_dump(mode="python"))
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("invalid_day_hypothesis_version") from None


def _safe_version_identity(version: HypothesisVersion) -> str:
    match version.__dict__.get("hypothesis_version_id"):
        case str() as version_id if len(version_id) == 64 and all(
            character in "0123456789abcdef" for character in version_id
        ):
            return version_id
        case _:
            raise InvalidDayResearchLedgerSourceError("invalid_day_hypothesis_version")


def _validated_version_or_conflict(version: HypothesisVersion) -> HypothesisVersion:
    try:
        return _validated_version(version)
    except InvalidDayResearchLedgerSourceError:
        raise DayResearchLedgerConflictError from None


def _family_by_id(
    connection: sqlite3.Connection,
    family_id: str,
) -> StoredDayHypothesisFamily | None:
    row: tuple[str, str, str | None, str, str] | None = connection.execute(
        """SELECT family_key,family_id,parent_family_id,created_at,payload_json
        FROM day_hypothesis_families WHERE family_id=?""",
        (family_id,),
    ).fetchone()
    return None if row is None else _stored_family(row)


def _version_by_id(
    connection: sqlite3.Connection,
    version_id: str,
) -> StoredDayHypothesisVersion | None:
    row: tuple[str, str, str, str | None, str, str, str, str, str] | None = connection.execute(
        """SELECT version_key,hypothesis_version_id,family_id,parent_version_id,
        market_id,created_at,registration_completed_bar_at,first_shadow_eligible_at,payload_json
        FROM day_hypothesis_versions WHERE hypothesis_version_id=?""",
        (version_id,),
    ).fetchone()
    return None if row is None else _stored_version(row)


def _stored_family(row: tuple[str, str, str | None, str, str]) -> StoredDayHypothesisFamily:
    key, family_id, parent_id, created_at, payload = row
    try:
        family = HypothesisFamily.model_validate_json(payload)
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("stored_day_family_payload_invalid") from None
    typed_key = DayHypothesisFamilyKey(key)
    if (
        typed_key != day_hypothesis_family_key(family)
        or family_id != family.family_id
        or parent_id != family.parent_family_id
        or created_at != family.created_at.isoformat()
    ):
        raise InvalidDayResearchLedgerSourceError("stored_day_family_index_invalid")
    return StoredDayHypothesisFamily(typed_key, family)


def _stored_version(
    row: tuple[str, str, str, str | None, str, str, str, str, str],
) -> StoredDayHypothesisVersion:
    key, version_id, family_id, parent_id, market_id, created_at, completed_at, eligible_at, payload = row
    try:
        version = HypothesisVersion.model_validate_json(payload)
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("stored_day_version_payload_invalid") from None
    typed_key = DayHypothesisVersionKey(key)
    if (
        typed_key != day_hypothesis_version_key(version)
        or version_id != version.hypothesis_version_id
        or family_id != version.family_id
        or parent_id != version.parent_version_id
        or market_id != version.market_id.value
        or created_at != version.created_at.isoformat()
        or completed_at != version.registration_completed_bar_at.isoformat()
        or eligible_at != version.first_shadow_eligible_at.isoformat()
    ):
        raise InvalidDayResearchLedgerSourceError("stored_day_version_index_invalid")
    return StoredDayHypothesisVersion(typed_key, version)


def _require_family_parent(connection: sqlite3.Connection, family: HypothesisFamily) -> None:
    if family.parent_family_id is None:
        return
    parent = _family_by_id(connection, family.parent_family_id)
    if parent is None or parent.family.created_at >= family.created_at:
        raise InvalidDayResearchLedgerSourceError("day_research_family_lineage_invalid")


def _require_version_lineage(connection: sqlite3.Connection, version: HypothesisVersion) -> None:
    family = _family_by_id(connection, version.family_id)
    if family is None or family.family.created_at > version.created_at:
        raise InvalidDayResearchLedgerSourceError("day_research_version_family_invalid")
    if version.parent_version_id is None:
        return
    parent = _version_by_id(connection, version.parent_version_id)
    if parent is None:
        raise InvalidDayResearchLedgerSourceError("day_research_parent_version_missing")
    _require_parent_version(parent.version, version)


def _require_parent_version(parent: HypothesisVersion, child: HypothesisVersion) -> None:
    require_same_market(parent.market_id, child.market_id)
    if parent.family_id != child.family_id or not (
        parent.created_at
        < parent.registration_completed_bar_at
        < parent.first_shadow_eligible_at
        <= child.created_at
        < child.registration_completed_bar_at
        < child.first_shadow_eligible_at
    ):
        raise InvalidDayResearchLedgerSourceError("day_research_version_lineage_invalid")


def _require_stored_family_parent(
    stored: StoredDayHypothesisFamily,
    families: dict[str, StoredDayHypothesisFamily],
) -> None:
    seen = {stored.family.family_id}
    child = stored.family
    while child.parent_family_id is not None:
        parent = families.get(child.parent_family_id)
        if parent is None or parent.family.family_id in seen or parent.family.created_at >= child.created_at:
            raise InvalidDayResearchLedgerSourceError("stored_day_family_lineage_invalid")
        seen.add(parent.family.family_id)
        child = parent.family


def _require_stored_version_lineage(
    stored: StoredDayHypothesisVersion,
    families: dict[str, StoredDayHypothesisFamily],
    versions: dict[str, StoredDayHypothesisVersion],
) -> None:
    version = stored.version
    family = families.get(version.family_id)
    if family is None or family.family.created_at > version.created_at:
        raise InvalidDayResearchLedgerSourceError("stored_day_version_family_invalid")
    seen = {version.hypothesis_version_id}
    child = version
    while child.parent_version_id is not None:
        parent = versions.get(child.parent_version_id)
        if parent is None or parent.version.hypothesis_version_id in seen:
            raise InvalidDayResearchLedgerSourceError("stored_day_version_lineage_invalid")
        _require_parent_version(parent.version, child)
        seen.add(parent.version.hypothesis_version_id)
        child = parent.version
