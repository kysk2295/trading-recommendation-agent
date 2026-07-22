from __future__ import annotations

import datetime as dt
import subprocess
from dataclasses import dataclass
from pathlib import Path

from trading_agent.experiment_ledger_keys import strategy_authority_binding_key
from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    StoredStrategyVersionRegistration,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.hermes_arm_request import (
    HermesArmAuthority,
    HermesArmFailure,
    HermesArmScope,
    InvalidHermesArmRequestError,
)
from trading_agent.lane_contract_models import LaneAccountBinding, LaneManifest
from trading_agent.lane_policy_models import LaneOrderAuthority, LaneRiskEnforcement
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)
from trading_agent.research_identity_models import AgentOperatingMode


@dataclass(frozen=True, slots=True)
class LedgerHermesArmAuthorityConfig:
    repository: Path
    lane_registry: Path
    experiment_ledger: Path


class LedgerHermesArmAuthorityResolver:
    __slots__ = ("_config",)

    def __init__(self, config: LedgerHermesArmAuthorityConfig) -> None:
        self._config = config

    def resolve(self, scope: HermesArmScope) -> HermesArmAuthority:
        try:
            session_date = _session_date(scope.session_id)
            commit_sha = _clean_commit(self._config.repository)
            lane_reader = LaneRegistryReader(self._config.lane_registry)
            experiment_reader = ExperimentLedgerReader(self._config.experiment_ledger)
            manifest = _current_manifest(lane_reader, scope)
            account = _current_account(lane_reader, scope)
            champion = _current_champion(experiment_reader, scope, session_date)
            if champion.registration.strategy_id not in manifest.strategy_ids:
                raise InvalidHermesArmRequestError(HermesArmFailure.CHAMPION_MISMATCH)
            if champion.registration.code_version != commit_sha:
                raise InvalidHermesArmRequestError(HermesArmFailure.COMMIT_MISMATCH)
            binding = _paper_authority_binding(experiment_reader, champion)
            return HermesArmAuthority(
                scope=scope,
                strategy_version=champion.registration.strategy_version,
                account_fingerprint=account.account_fingerprint,
                risk_contract_hash=HermesArmAuthority.risk_hash(manifest.risk_contract),
                commit_sha=commit_sha,
                champion_binding_key=str(strategy_authority_binding_key(binding)),
            )
        except (
            InvalidExperimentLedgerSourceError,
            InvalidLaneRegistrySourceError,
            UnsupportedExperimentLedgerSchemaError,
            UnsupportedLaneRegistrySchemaError,
        ):
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE) from None


def _session_date(session_id: str) -> dt.date:
    try:
        session_date = dt.date.fromisoformat(session_id[-10:])
    except ValueError:
        raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_REQUEST) from None
    if session_id != f"XNYS-{session_date.isoformat()}":
        raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_REQUEST)
    return session_date


def _clean_commit(repository: Path) -> str:
    root = repository.resolve(strict=False)
    commit = _git(root, "rev-parse", "HEAD")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise InvalidHermesArmRequestError(HermesArmFailure.DIRTY_COMMIT)
    return commit


def _git(repository: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ("git", "-C", str(repository), *args),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise InvalidHermesArmRequestError(HermesArmFailure.DIRTY_COMMIT) from None
    return result.stdout.strip()


def _current_manifest(reader: LaneRegistryReader, scope: HermesArmScope) -> LaneManifest:
    manifests = tuple(stored.manifest for stored in reader.manifests() if stored.manifest.lane_id is scope.lane_id)
    if not manifests:
        raise InvalidHermesArmRequestError(HermesArmFailure.RISK_MISMATCH)
    manifest = max(manifests, key=lambda candidate: candidate.registered_at)
    if (
        manifest.execution_policy.order_authority is not LaneOrderAuthority.ALPACA_PAPER
        or manifest.risk_contract.enforcement is not LaneRiskEnforcement.BROKER_PAPER
    ):
        raise InvalidHermesArmRequestError(HermesArmFailure.RISK_MISMATCH)
    return manifest


def _current_account(reader: LaneRegistryReader, scope: HermesArmScope) -> LaneAccountBinding:
    bindings = tuple(
        stored.binding for stored in reader.account_bindings() if stored.binding.lane_id is scope.lane_id
    )
    if not bindings:
        raise InvalidHermesArmRequestError(HermesArmFailure.ACCOUNT_MISMATCH)
    return max(bindings, key=lambda candidate: candidate.bound_at)


def _current_champion(
    reader: ExperimentLedgerReader,
    scope: HermesArmScope,
    session_date: dt.date,
) -> StoredStrategyVersionRegistration:
    candidates: list[StoredStrategyVersionRegistration] = []
    for stored in reader.strategy_versions():
        if stored.registration.lane_id is not scope.lane_id:
            continue
        state = reader.lifecycle_state(stored.registration.strategy_version, session_date)
        if state is not None and state.event.to_state is StrategyLifecycleState.PAPER_CHAMPION:
            candidates.append(stored)
    if len(candidates) != 1:
        raise InvalidHermesArmRequestError(HermesArmFailure.CHAMPION_MISSING)
    return candidates[0]


def _paper_authority_binding(reader: ExperimentLedgerReader, champion: StoredStrategyVersionRegistration):
    bindings = tuple(
        stored.binding
        for stored in reader.strategy_authority_bindings()
        if stored.binding.strategy_version == champion.registration.strategy_version
        and stored.binding.legacy_lane_id is champion.registration.lane_id
        and stored.binding.operating_mode is AgentOperatingMode.ALPACA_PAPER
    )
    if len(bindings) != 1:
        raise InvalidHermesArmRequestError(HermesArmFailure.CHAMPION_MISSING)
    return bindings[0]
