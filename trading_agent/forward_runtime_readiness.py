from __future__ import annotations

import datetime as dt
import os
import sqlite3
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.daily_research_contract import strategy_version_identity
from trading_agent.execution_store import ExecutionStore
from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.lane_contract_keys import experiment_scope_key, lane_manifest_key
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    INTRADAY_MANIFEST,
)
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)
from trading_agent.research_identity_models import AgentOperatingMode
from trading_agent.strategy_factory import StrategyMode

STRICT_CYCLES = 390
STRICT_INTERVAL_SECONDS = 60
STRICT_KIS_SERVER_ATTEMPTS = 4
STRICT_EOD_LAST_BAR_SEMANTIC_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class ForwardRuntimeReadiness:
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers


def evaluate_forward_runtime_readiness(
    *,
    runtime_dir: Path,
    expected_head: str,
    required_commits: tuple[str, ...],
    session_date: dt.date,
    experiment_ledger: Path,
    lane_registry: Path,
    execution_database: Path,
    cycles: int,
    interval_seconds: int,
    kis_server_attempts: int,
    eod_last_bar_semantic_attempts: int,
) -> ForwardRuntimeReadiness:
    blockers: list[str] = []
    if not _private_frozen_runtime(runtime_dir, expected_head):
        blockers.append("runtime_not_frozen")
    else:
        if any(not _is_ancestor(runtime_dir, commit, expected_head) for commit in required_commits):
            blockers.append("required_commit_missing")
    if not _current_lane_registry(lane_registry):
        blockers.append("lane_registry_not_current")
    if not _active_runtime_version(experiment_ledger, expected_head, session_date):
        blockers.append("runtime_version_not_active")
    if not _execution_database_initialized(execution_database):
        blockers.append("execution_database_not_initialized")
    if (
        cycles != STRICT_CYCLES
        or interval_seconds != STRICT_INTERVAL_SECONDS
        or kis_server_attempts != STRICT_KIS_SERVER_ATTEMPTS
        or eod_last_bar_semantic_attempts != STRICT_EOD_LAST_BAR_SEMANTIC_ATTEMPTS
    ):
        blockers.append("runtime_config_mismatch")
    return ForwardRuntimeReadiness(tuple(sorted(set(blockers))))


def _execution_database_initialized(path: Path) -> bool:
    try:
        return ExecutionStore(path).is_initialized()
    except (sqlite3.Error, OSError):
        return False


def _private_frozen_runtime(runtime_dir: Path, expected_head: str) -> bool:
    try:
        if (
            not runtime_dir.is_absolute()
            or runtime_dir.is_symlink()
            or not runtime_dir.is_dir()
        ):
            return False
        metadata = runtime_dir.stat()
        if (
            metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or _git(runtime_dir, "rev-parse", "HEAD") != expected_head
        ):
            return False
        return _git(runtime_dir, "status", "--porcelain", "--untracked-files=all") == ""
    except (OSError, subprocess.SubprocessError):
        return False


def _is_ancestor(runtime_dir: Path, commit: str, head: str) -> bool:
    try:
        result = subprocess.run(
            ("git", "-C", str(runtime_dir), "merge-base", "--is-ancestor", commit, head),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _git(runtime_dir: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(runtime_dir), *arguments),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _current_lane_registry(path: Path) -> bool:
    reader = LaneRegistryReader(path)
    try:
        if not reader.is_initialized():
            return False
        manifests = tuple(
            stored
            for stored in reader.manifests()
            if stored.manifest.lane_id is INTRADAY_MANIFEST.lane_id
        )
        if (
            len(manifests) != 1
            or manifests[0].manifest != INTRADAY_MANIFEST
            or manifests[0].manifest_key != lane_manifest_key(INTRADAY_MANIFEST)
        ):
            return False
        scopes = reader.experiment_scopes()
        return all(
            sum(
                stored.scope == expected
                and stored.scope_key == experiment_scope_key(expected)
                for stored in scopes
            )
            == 1
            for expected in CURRENT_INTRADAY_EXPERIMENT_SCOPES
        )
    except (
        InvalidLaneRegistrySourceError,
        UnsupportedLaneRegistrySchemaError,
        ValidationError,
        sqlite3.Error,
        OSError,
        ValueError,
    ):
        return False


def _active_runtime_version(path: Path, head: str, session_date: dt.date) -> bool:
    reader = ExperimentLedgerReader(path)
    expected = {strategy_version_identity(mode, head) for mode in StrategyMode}
    try:
        if not reader.is_initialized():
            return False
        versions = tuple(
            stored for stored in reader.strategy_versions() if stored.registration.strategy_version in expected
        )
        authorities = tuple(
            stored
            for stored in reader.strategy_authority_bindings()
            if stored.binding.strategy_version in expected
        )
        if (
            len(versions) != len(expected)
            or {stored.registration.strategy_version for stored in versions} != expected
            or {stored.registration.code_version for stored in versions} != {head}
            or len(authorities) != len(expected)
            or {stored.binding.strategy_version for stored in authorities} != expected
            or any(
                stored.binding.operating_mode is not AgentOperatingMode.ALPACA_PAPER
                for stored in authorities
            )
        ):
            return False
        return all(
            (state := reader.lifecycle_state(version, session_date)) is not None
            and state.event.to_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
            for version in expected
        )
    except (
        InvalidExperimentLedgerSourceError,
        UnsupportedExperimentLedgerSchemaError,
        ValidationError,
        sqlite3.Error,
        OSError,
        ValueError,
    ):
        return False
