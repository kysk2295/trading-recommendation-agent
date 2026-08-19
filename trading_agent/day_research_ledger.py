from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import assert_never

from trading_agent.day_hypothesis_models import HypothesisFamily, HypothesisVersion
from trading_agent.day_research_attempt_binding import (
    DayResearchAttemptBinding,
    preregistered_attempted_artifact_ref,
)
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_keys import (
    DayHypothesisFamilyKey,
    DayHypothesisVersionKey,
    canonical_experiment_ledger_json,
    day_hypothesis_family_key,
    day_hypothesis_version_key,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_models import PreregistrationManifest, SealedHoldoutRef
from trading_agent.strategy_research_results import ResearchAttempt
from trading_agent.strategy_research_types import AttemptStatus


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


@dataclass(frozen=True, slots=True)
class StoredDayResearchAttemptBinding:
    binding: DayResearchAttemptBinding


@dataclass(frozen=True, slots=True)
class StoredDayStrategyCapsule:
    capsule: StrategyCapsule


@dataclass(frozen=True, slots=True)
class StoredDayResearchVersionGraph:
    families: dict[str, StoredDayHypothesisFamily]
    versions: dict[str, StoredDayHypothesisVersion]


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


def register_day_research_attempt_binding(
    connection: sqlite3.Connection,
    binding: DayResearchAttemptBinding,
) -> bool:
    graph = audit_day_research_attempt_bindings(connection)
    return _register_day_research_attempt_binding_after_audit(connection, binding, graph)


def register_day_strategy_capsule(
    connection: sqlite3.Connection,
    capsule: StrategyCapsule,
) -> bool:
    capsule_id = _safe_capsule_identity(capsule)
    audit = _stored_binding_audit(connection)
    stored_bindings = {item.binding.binding_id: item for item in audit.bindings}
    existing = _capsule_by_id(connection, capsule_id)
    if existing is not None:
        checked = _validated_capsule_or_conflict(capsule)
        _require_capsule_parent_coherence(connection, checked, stored_bindings, audit.graph)
        if existing.capsule == checked:
            return False
        raise DayResearchLedgerConflictError
    checked = _validated_capsule(capsule)
    _require_capsule_parent_coherence(connection, checked, stored_bindings, audit.graph)
    try:
        _ = connection.execute(
            "INSERT INTO day_strategy_capsules VALUES (?,?,?,?,?)",
            (
                checked.capsule_id,
                checked.hypothesis_version_id,
                checked.market_id.value,
                checked.published_at.isoformat(),
                canonical_experiment_ledger_json(checked),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise DayResearchLedgerConflictError from error
    return True


def _register_day_research_attempt_binding_after_audit(
    connection: sqlite3.Connection,
    binding: DayResearchAttemptBinding,
    graph: StoredDayResearchVersionGraph,
) -> bool:
    binding_id = _safe_binding_identity(binding)
    existing = _binding_by_id(connection, binding_id)
    if existing is not None:
        checked = _validated_binding_or_conflict(binding)
        _require_binding_parent_coherence(connection, checked, graph)
        if existing.binding == checked:
            return False
        raise DayResearchLedgerConflictError
    checked = _validated_binding(binding)
    existing_attempt = _binding_by_attempt_id(connection, checked.attempt_id)
    if existing_attempt is not None:
        _require_binding_parent_coherence(connection, existing_attempt.binding, graph)
        raise DayResearchLedgerConflictError
    version, _ = _require_binding_parent_coherence(connection, checked, graph)
    _require_available_search_budget(connection, checked, version)
    try:
        _ = connection.execute(
            "INSERT INTO day_research_attempt_bindings VALUES (?,?,?,?,?,?,?,?,?)",
            (
                checked.binding_id,
                checked.attempt_id,
                checked.hypothesis_version_id,
                checked.market_id.value,
                checked.artifact_ref,
                checked.multiple_testing_family,
                checked.search_budget_debit,
                checked.bound_at.isoformat(),
                canonical_experiment_ledger_json(checked),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise DayResearchLedgerConflictError from error
    return True


def audit_day_research_attempt_bindings(connection: sqlite3.Connection) -> StoredDayResearchVersionGraph:
    return _stored_binding_audit(connection).graph


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


def _validated_binding(binding: DayResearchAttemptBinding) -> DayResearchAttemptBinding:
    try:
        return DayResearchAttemptBinding.model_validate(binding.model_dump(mode="python"))
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("invalid_day_research_attempt_binding") from None


def _safe_binding_identity(binding: DayResearchAttemptBinding) -> str:
    match binding.__dict__.get("binding_id"):
        case str() as binding_id if len(binding_id) == 64 and all(
            character in "0123456789abcdef" for character in binding_id
        ):
            return binding_id
        case _:
            raise InvalidDayResearchLedgerSourceError("invalid_day_research_attempt_binding")


def _validated_binding_or_conflict(binding: DayResearchAttemptBinding) -> DayResearchAttemptBinding:
    try:
        return _validated_binding(binding)
    except InvalidDayResearchLedgerSourceError:
        raise DayResearchLedgerConflictError from None


def _validated_capsule(capsule: StrategyCapsule) -> StrategyCapsule:
    try:
        return StrategyCapsule.model_validate(capsule.model_dump(mode="python"))
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("invalid_day_strategy_capsule") from None


def _safe_capsule_identity(capsule: StrategyCapsule) -> str:
    match capsule.__dict__.get("capsule_id"):
        case str() as capsule_id if len(capsule_id) == 64 and all(
            character in "0123456789abcdef" for character in capsule_id
        ):
            return capsule_id
        case _:
            raise InvalidDayResearchLedgerSourceError("invalid_day_strategy_capsule")


def _validated_capsule_or_conflict(capsule: StrategyCapsule) -> StrategyCapsule:
    try:
        return _validated_capsule(capsule)
    except InvalidDayResearchLedgerSourceError:
        raise DayResearchLedgerConflictError from None


def _family_by_id(
    connection: sqlite3.Connection,
    family_id: str,
) -> StoredDayHypothesisFamily | None:
    rows: list[tuple[str, str, str | None, str, str]] = connection.execute(
        """SELECT family_key,family_id,parent_family_id,created_at,payload_json
        FROM day_hypothesis_families WHERE family_id=?""",
        (family_id,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise InvalidDayResearchLedgerSourceError("stored_day_family_identity_duplicate")
    return _stored_family(rows[0])


def _version_by_id(
    connection: sqlite3.Connection,
    version_id: str,
) -> StoredDayHypothesisVersion | None:
    rows: list[tuple[str, str, str, str | None, str, str, str, str, str]] = connection.execute(
        """SELECT version_key,hypothesis_version_id,family_id,parent_version_id,
        market_id,created_at,registration_completed_bar_at,first_shadow_eligible_at,payload_json
        FROM day_hypothesis_versions WHERE hypothesis_version_id=?""",
        (version_id,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise InvalidDayResearchLedgerSourceError("stored_day_version_identity_duplicate")
    return _stored_version(rows[0])


def _binding_by_id(
    connection: sqlite3.Connection,
    binding_id: str,
) -> StoredDayResearchAttemptBinding | None:
    rows: list[tuple[str, str, str, str, str, str, int, str, str]] = connection.execute(
        "SELECT binding_id,attempt_id,hypothesis_version_id,market_id,artifact_ref, "
        "multiple_testing_family,search_budget_debit,bound_at,payload_json "
        "FROM day_research_attempt_bindings WHERE binding_id=?",
        (binding_id,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise InvalidDayResearchLedgerSourceError("stored_day_attempt_binding_identity_duplicate")
    return _stored_binding(rows[0])


def _binding_by_attempt_id(
    connection: sqlite3.Connection,
    attempt_id: str,
) -> StoredDayResearchAttemptBinding | None:
    rows: list[tuple[str, str, str, str, str, str, int, str, str]] = connection.execute(
        "SELECT binding_id,attempt_id,hypothesis_version_id,market_id,artifact_ref, "
        "multiple_testing_family,search_budget_debit,bound_at,payload_json "
        "FROM day_research_attempt_bindings WHERE attempt_id=?",
        (attempt_id,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise InvalidDayResearchLedgerSourceError("stored_day_attempt_binding_attempt_duplicate")
    return _stored_binding(rows[0])


def _capsule_by_id(
    connection: sqlite3.Connection,
    capsule_id: str,
) -> StoredDayStrategyCapsule | None:
    rows: list[tuple[str, str, str, str, str]] = connection.execute(
        "SELECT capsule_id,hypothesis_version_id,market_id,created_at,payload_json "
        "FROM day_strategy_capsules WHERE capsule_id=?",
        (capsule_id,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise InvalidDayResearchLedgerSourceError("stored_day_capsule_identity_duplicate")
    return _stored_capsule(rows[0])


def _stored_family(row: tuple[str, str, str | None, str, str]) -> StoredDayHypothesisFamily:
    key, family_id, parent_id, created_at, payload = row
    try:
        family = HypothesisFamily.model_validate_json(payload)
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("stored_day_family_payload_invalid") from None
    if payload != canonical_experiment_ledger_json(family):
        raise InvalidDayResearchLedgerSourceError("stored_day_family_payload_invalid")
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
    if payload != canonical_experiment_ledger_json(version):
        raise InvalidDayResearchLedgerSourceError("stored_day_version_payload_invalid")
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


def _stored_binding(
    row: tuple[str, str, str, str, str, str, int, str, str],
) -> StoredDayResearchAttemptBinding:
    binding_id, attempt_id, version_id, market_id, artifact_ref, family, debit, bound_at, payload = row
    try:
        binding = DayResearchAttemptBinding.model_validate_json(payload)
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("stored_day_attempt_binding_payload_invalid") from None
    if payload != canonical_experiment_ledger_json(binding):
        raise InvalidDayResearchLedgerSourceError("stored_day_attempt_binding_payload_invalid")
    if (
        binding_id != binding.binding_id
        or attempt_id != binding.attempt_id
        or version_id != binding.hypothesis_version_id
        or market_id != binding.market_id.value
        or artifact_ref != binding.artifact_ref
        or family != binding.multiple_testing_family
        or debit != binding.search_budget_debit
        or bound_at != binding.bound_at.isoformat()
    ):
        raise InvalidDayResearchLedgerSourceError("stored_day_attempt_binding_index_invalid")
    return StoredDayResearchAttemptBinding(binding)


def _stored_capsule(row: tuple[str, str, str, str, str]) -> StoredDayStrategyCapsule:
    capsule_id, version_id, market_id, created_at, payload = row
    try:
        capsule = StrategyCapsule.model_validate_json(payload)
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("stored_day_capsule_payload_invalid") from None
    if payload != canonical_experiment_ledger_json(capsule):
        raise InvalidDayResearchLedgerSourceError("stored_day_capsule_payload_invalid")
    if (
        capsule_id != capsule.capsule_id
        or version_id != capsule.hypothesis_version_id
        or market_id != capsule.market_id.value
        or created_at != capsule.published_at.isoformat()
    ):
        raise InvalidDayResearchLedgerSourceError("stored_day_capsule_index_invalid")
    return StoredDayStrategyCapsule(capsule)


def _stored_attempt(connection: sqlite3.Connection, attempt_id: str) -> ResearchAttempt | None:
    rows: list[tuple[str, str, str, int, str, str]] = connection.execute(
        "SELECT attempt_key,attempt_id,hypothesis_id,branch_index,status,payload_json "
        "FROM strategy_research_attempts WHERE attempt_id=?",
        (attempt_id,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise InvalidDayResearchLedgerSourceError("stored_research_attempt_duplicate")
    row = rows[0]
    key, stored_id, hypothesis_id, branch_index, status, payload = row
    try:
        attempt = ResearchAttempt.model_validate_json(payload)
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("stored_research_attempt_payload_invalid") from None
    if (
        key != attempt.content_sha256
        or stored_id != attempt.attempt_id
        or hypothesis_id != attempt.hypothesis_id
        or branch_index != attempt.branch_index
        or status != attempt.status.value
        or payload != attempt.model_dump_json()
    ):
        raise InvalidDayResearchLedgerSourceError("stored_research_attempt_index_invalid")
    return attempt


def _require_attempt_preregistration(
    connection: sqlite3.Connection,
    attempt: ResearchAttempt,
) -> PreregistrationManifest:
    rows: list[tuple[str, str, str, str, str, str, str]] = connection.execute(
        "SELECT registration_key,hypothesis_id,parent_hypothesis_id,search_family_id, "
        "agent_id,protocol_version,payload_json FROM strategy_research_preregistrations WHERE hypothesis_id=?",
        (attempt.hypothesis_id,),
    ).fetchall()
    if not rows:
        raise InvalidDayResearchLedgerSourceError("stored_research_attempt_preregistration_missing")
    if len(rows) != 1:
        raise InvalidDayResearchLedgerSourceError("stored_research_attempt_preregistration_duplicate")
    row = rows[0]
    key, hypothesis_id, parent_id, family, agent, protocol, payload = row
    try:
        manifest = PreregistrationManifest.model_validate_json(payload)
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("stored_preregistration_payload_invalid") from None
    hypothesis = manifest.hypothesis
    if (
        key != manifest.content_sha256
        or hypothesis_id != hypothesis.hypothesis_id
        or parent_id != hypothesis.parent_hypothesis_id
        or family != hypothesis.search_family_id
        or agent != hypothesis.agent_id.value
        or protocol != hypothesis.protocol_version
        or payload != manifest.model_dump_json()
        or attempt.code_sha256 != hypothesis.code_sha256
        or attempt.data_manifest_sha256 != hypothesis.data_manifest_sha256
    ):
        raise InvalidDayResearchLedgerSourceError("stored_research_attempt_preregistration_invalid")
    _require_attempt_holdout_seal(connection, manifest)
    return manifest


def _require_attempt_holdout_seal(
    connection: sqlite3.Connection,
    manifest: PreregistrationManifest,
) -> None:
    expected = manifest.hypothesis.holdout_period_sealed_ref
    rows: list[tuple[str, str, str, str]] = connection.execute(
        "SELECT seal_id,hypothesis_id,commitment_sha256,payload_json FROM strategy_research_holdout_seals "
        "WHERE hypothesis_id=?",
        (manifest.hypothesis.hypothesis_id,),
    ).fetchall()
    if not rows:
        raise InvalidDayResearchLedgerSourceError("stored_research_attempt_holdout_seal_missing")
    if len(rows) != 1:
        raise InvalidDayResearchLedgerSourceError("stored_research_attempt_holdout_seal_duplicate")
    row = rows[0]
    seal_id, hypothesis_id, commitment, payload = row
    try:
        seal = SealedHoldoutRef.model_validate_json(payload)
    except ValueError:
        raise InvalidDayResearchLedgerSourceError("stored_research_attempt_holdout_seal_payload_invalid") from None
    if (
        seal_id != expected.seal_id
        or hypothesis_id != manifest.hypothesis.hypothesis_id
        or commitment != expected.commitment_sha256
        or seal != expected
        or payload != expected.model_dump_json()
    ):
        raise InvalidDayResearchLedgerSourceError("stored_research_attempt_holdout_seal_invalid")


def _require_binding_parent_coherence(
    connection: sqlite3.Connection,
    binding: DayResearchAttemptBinding,
    graph: StoredDayResearchVersionGraph | None = None,
) -> tuple[HypothesisVersion, ResearchAttempt]:
    stored_version = _version_by_id(connection, binding.hypothesis_version_id) if graph is None else graph.versions.get(
        binding.hypothesis_version_id
    )
    attempt = _stored_attempt(connection, binding.attempt_id)
    if stored_version is None or attempt is None:
        raise InvalidDayResearchLedgerSourceError("day_research_attempt_binding_parent_missing")
    version = stored_version.version
    if graph is None:
        _require_target_version_lineage(connection, version)
    manifest = _require_attempt_preregistration(connection, attempt)
    require_same_market(version.market_id, binding.market_id)
    if (
        binding.multiple_testing_family != version.multiple_testing_family
        or version.multiple_testing_family != manifest.hypothesis.multiple_testing_family
        or attempt.code_sha256 != version.code_sha256
        or attempt.data_manifest_sha256 != version.data_manifest_sha256
        or attempt.finished_at is None
        or binding.bound_at <= attempt.finished_at
        or binding.bound_at <= version.created_at
        or binding.bound_at <= version.registration_completed_bar_at
    ):
        raise InvalidDayResearchLedgerSourceError("day_research_attempt_binding_protocol_invalid")
    match attempt.status:
        case AttemptStatus.SUCCEEDED:
            if binding.artifact_ref not in attempt.artifact_refs:
                raise InvalidDayResearchLedgerSourceError("day_research_attempt_binding_artifact_invalid")
        case (
            AttemptStatus.FAILED
            | AttemptStatus.ABORTED
            | AttemptStatus.TIMED_OUT
            | AttemptStatus.CANCELLED
            | AttemptStatus.CENSORED
        ):
            if binding.artifact_ref != preregistered_attempted_artifact_ref(attempt.code_sha256):
                raise InvalidDayResearchLedgerSourceError("day_research_attempt_binding_artifact_invalid")
        case AttemptStatus.STARTED:
            raise InvalidDayResearchLedgerSourceError("day_research_attempt_binding_not_terminal")
        case unreachable:
            assert_never(unreachable)
    return version, attempt


def _require_capsule_parent_coherence(
    connection: sqlite3.Connection,
    capsule: StrategyCapsule,
    stored_bindings: Mapping[str, StoredDayResearchAttemptBinding] | None = None,
    graph: StoredDayResearchVersionGraph | None = None,
) -> None:
    version = (
        _version_by_id(connection, capsule.hypothesis_version_id)
        if graph is None
        else graph.versions.get(capsule.hypothesis_version_id)
    )
    bindings = (
        {item.binding.binding_id: item for item in _all_stored_bindings(connection)}
        if stored_bindings is None
        else stored_bindings
    )
    stored_binding = bindings.get(capsule.attempt_binding_id)
    binding = None if stored_binding is None else stored_binding.binding
    if version is None or binding is None:
        raise InvalidDayResearchLedgerSourceError("day_strategy_capsule_parent_missing")
    _, attempt = _require_binding_parent_coherence(connection, binding, graph)
    require_same_market(version.version.market_id, capsule.market_id)
    if (
        attempt.status is not AttemptStatus.SUCCEEDED
        or binding.hypothesis_version_id != capsule.hypothesis_version_id
        or binding.market_id is not capsule.market_id
        or binding.artifact_ref != capsule.artifact_ref
        or version.version.code_sha256 != capsule.artifact_sha256
        or version.version.evaluation_cadence != capsule.evaluation_cadence
        or version.version.entry_rule != capsule.entry_rule
        or version.version.exit_rule != capsule.exit_rule
        or version.version.stop_rule != capsule.stop_rule
        or version.version.cost_model != capsule.cost_model
        or version.version.protocol_sha256 != capsule.protocol_sha256
        or capsule.published_at <= binding.bound_at
    ):
        raise InvalidDayResearchLedgerSourceError("day_strategy_capsule_protocol_invalid")


def _all_stored_bindings(connection: sqlite3.Connection) -> tuple[StoredDayResearchAttemptBinding, ...]:
    return _stored_binding_audit(connection).bindings


@dataclass(frozen=True, slots=True)
class StoredDayResearchBindingAudit:
    bindings: tuple[StoredDayResearchAttemptBinding, ...]
    graph: StoredDayResearchVersionGraph


def _stored_binding_audit(connection: sqlite3.Connection) -> StoredDayResearchBindingAudit:
    rows: list[tuple[str, str, str, str, str, str, int, str, str]] = connection.execute(
        "SELECT binding_id,attempt_id,hypothesis_version_id,market_id,artifact_ref, "
        "multiple_testing_family,search_budget_debit,bound_at,payload_json "
        "FROM day_research_attempt_bindings ORDER BY rowid"
    ).fetchall()
    bindings = tuple(_stored_binding(row) for row in rows)
    if len({item.binding.binding_id for item in bindings}) != len(bindings):
        raise InvalidDayResearchLedgerSourceError("stored_day_attempt_binding_identity_duplicate")
    if len({item.binding.attempt_id for item in bindings}) != len(bindings):
        raise InvalidDayResearchLedgerSourceError("stored_day_attempt_binding_attempt_duplicate")
    graph = _stored_day_research_version_graph(connection)
    for stored in bindings:
        _require_binding_parent_coherence(connection, stored.binding, graph)
    _require_stored_binding_budgets(bindings, graph)
    return StoredDayResearchBindingAudit(bindings, graph)


def _require_available_search_budget(
    connection: sqlite3.Connection,
    binding: DayResearchAttemptBinding,
    version: HypothesisVersion,
) -> None:
    row: tuple[int | None] = connection.execute(
        "SELECT SUM(search_budget_debit) FROM day_research_attempt_bindings WHERE hypothesis_version_id=?",
        (version.hypothesis_version_id,),
    ).fetchone()
    cumulative_debit = binding.search_budget_debit + (0 if row[0] is None else row[0])
    if cumulative_debit > version.search_budget.max_attempts:
        raise InvalidDayResearchLedgerSourceError("day_research_attempt_binding_budget_exhausted")


def _require_stored_binding_budgets(
    bindings: tuple[StoredDayResearchAttemptBinding, ...],
    graph: StoredDayResearchVersionGraph,
) -> None:
    debit_by_version: dict[str, int] = {}
    for stored in bindings:
        binding = stored.binding
        debit_by_version[binding.hypothesis_version_id] = (
            debit_by_version.get(binding.hypothesis_version_id, 0) + binding.search_budget_debit
        )
    for version_id, debit in debit_by_version.items():
        stored_version = graph.versions.get(version_id)
        if stored_version is None or debit > stored_version.version.search_budget.max_attempts:
            raise InvalidDayResearchLedgerSourceError("stored_day_attempt_binding_budget_invalid")


def _stored_day_research_version_graph(connection: sqlite3.Connection) -> StoredDayResearchVersionGraph:
    family_rows: list[tuple[str, str, str | None, str, str]] = connection.execute(
        "SELECT family_key,family_id,parent_family_id,created_at,payload_json FROM day_hypothesis_families"
    ).fetchall()
    families = tuple(_stored_family(row) for row in family_rows)
    by_family = {stored.family.family_id: stored for stored in families}
    if len(by_family) != len(families):
        raise InvalidDayResearchLedgerSourceError("stored_day_family_identity_duplicate")
    _require_stored_family_graph(by_family)
    version_rows: list[tuple[str, str, str, str | None, str, str, str, str, str]] = connection.execute(
        "SELECT version_key,hypothesis_version_id,family_id,parent_version_id,market_id,created_at, "
        "registration_completed_bar_at,first_shadow_eligible_at,payload_json FROM day_hypothesis_versions"
    ).fetchall()
    versions = tuple(_stored_version(row) for row in version_rows)
    by_version = {stored.version.hypothesis_version_id: stored for stored in versions}
    if len(by_version) != len(versions):
        raise InvalidDayResearchLedgerSourceError("stored_day_version_identity_duplicate")
    _require_stored_version_graph(by_family, by_version)
    return StoredDayResearchVersionGraph(by_family, by_version)


def _require_target_version_lineage(connection: sqlite3.Connection, version: HypothesisVersion) -> None:
    family = _family_by_id(connection, version.family_id)
    if family is None or family.family.created_at > version.created_at:
        raise InvalidDayResearchLedgerSourceError("stored_day_version_family_invalid")
    seen = {version.hypothesis_version_id}
    child = version
    while child.parent_version_id is not None:
        parent = _version_by_id(connection, child.parent_version_id)
        if parent is None or parent.version.hypothesis_version_id in seen:
            raise InvalidDayResearchLedgerSourceError("stored_day_version_lineage_invalid")
        _require_parent_version(parent.version, child)
        seen.add(parent.version.hypothesis_version_id)
        child = parent.version


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


def _require_stored_family_graph(families: Mapping[str, StoredDayHypothesisFamily]) -> None:
    parent_ids: dict[str, str | None] = {}
    for family_id, stored in families.items():
        family = stored.family
        parent = families.get(family.parent_family_id) if family.parent_family_id is not None else None
        if (
            family.parent_family_id is not None
            and (parent is None or parent.family.created_at >= family.created_at)
        ):
            raise InvalidDayResearchLedgerSourceError("stored_day_family_lineage_invalid")
        parent_ids[family_id] = family.parent_family_id
    _require_acyclic_parent_graph(parent_ids, "stored_day_family_lineage_invalid")


def _require_stored_version_graph(
    families: Mapping[str, StoredDayHypothesisFamily],
    versions: Mapping[str, StoredDayHypothesisVersion],
) -> None:
    parent_ids: dict[str, str | None] = {}
    for version_id, stored in versions.items():
        version = stored.version
        family = families.get(version.family_id)
        if family is None or family.family.created_at > version.created_at:
            raise InvalidDayResearchLedgerSourceError("stored_day_version_family_invalid")
        parent = versions.get(version.parent_version_id) if version.parent_version_id is not None else None
        if version.parent_version_id is not None:
            if parent is None:
                raise InvalidDayResearchLedgerSourceError("stored_day_version_lineage_invalid")
            _require_parent_version(parent.version, version)
        parent_ids[version_id] = version.parent_version_id
    _require_acyclic_parent_graph(parent_ids, "stored_day_version_lineage_invalid")


def _require_acyclic_parent_graph(
    parent_ids: Mapping[str, str | None],
    reason: str,
) -> None:
    completed: set[str] = set()
    for start_id in parent_ids:
        if start_id in completed:
            continue
        path: set[str] = set()
        current_id = start_id
        while current_id not in completed:
            if current_id in path:
                raise InvalidDayResearchLedgerSourceError(reason)
            path.add(current_id)
            parent_id = parent_ids.get(current_id)
            if parent_id is None:
                break
            current_id = parent_id
        completed.update(path)
