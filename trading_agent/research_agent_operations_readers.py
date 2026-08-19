from __future__ import annotations

import datetime as dt
import os
import sqlite3
import stat
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from run_autonomous_research_cycle import (
    InvalidAutonomousCycleCliResultError,
    load_autonomous_cycle_cli_result,
)
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.dashboard_autonomous_research import AutonomousTaskReceiptV1
from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    absolute_private_path,
    open_private_parent,
    require_private_directory_query_only,
)
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)
from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleState,
    ResearchAgentResultStatus,
)
from trading_agent.research_agent_cycle_schema import RESEARCH_AGENT_CYCLE_SCHEMA_VERSION
from trading_agent.research_agent_cycle_store_codec import (
    cycle_from_payload,
    open_work_from_payload,
    result_from_payload,
    stored_evidence,
)
from trading_agent.research_agent_cycle_store_support import InvalidResearchAgentCycleStoreError
from trading_agent.research_agent_operations_models import (
    CycleOperationsFacts,
    CycleOperationsHistory,
    InvalidResearchAgentOperationsSourceError,
    OperationsAlertReason,
)
from trading_agent.research_agent_operations_sqlite import (
    cycle_database_storage_bytes,
    open_cycle_database_query_only,
)

StoreKind = Literal["cycle", "receipt", "runs"]
_TABLES = frozenset(
    {"evidence", "cycles", "cycle_events", "results", "cursors", "day_cursors", "open_work"}
)
_MAX_ENTRIES = 10_000


def require_operations_store(path: Path, kind: StoreKind) -> Path:
    target = absolute_private_path(path)
    try:
        metadata = target.lstat()
    except OSError:
        raise InvalidResearchAgentOperationsSourceError(_reason(kind, "missing")) from None
    if stat.S_ISLNK(metadata.st_mode):
        raise InvalidResearchAgentOperationsSourceError(_reason(kind, "symlink"))
    regular = stat.S_ISREG(metadata.st_mode) if kind == "cycle" else stat.S_ISDIR(metadata.st_mode)
    expected_mode = 0o600 if kind == "cycle" else 0o700
    if not regular or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise InvalidResearchAgentOperationsSourceError(_reason(kind, "nonprivate"))
    if kind == "cycle" and metadata.st_nlink != 1:
        raise InvalidResearchAgentOperationsSourceError(_reason(kind, "hardlink"))
    try:
        parent = open_private_parent(target.parent if kind == "cycle" else target, create=False)
        try:
            require_private_directory_query_only(parent)
        finally:
            os.close(parent)
    except (InvalidPrivateDirectoryIdentityError, OSError):
        raise InvalidResearchAgentOperationsSourceError(_reason(kind, "nonprivate")) from None
    return target


def read_cycle_operations_facts(path: Path, now: dt.datetime) -> tuple[CycleOperationsFacts, ...]:
    try:
        with open_cycle_database_query_only(path) as connection:
            if connection.execute("PRAGMA user_version").fetchone() != (RESEARCH_AGENT_CYCLE_SCHEMA_VERSION,):
                raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_WRONG_SCHEMA)
            tables = frozenset(
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                if row[0] != "sqlite_sequence"
            )
            if tables != _TABLES:
                raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_WRONG_SCHEMA)
            evidence = tuple(
                stored_evidence(row).evidence
                for row in connection.execute(
                    "SELECT sequence,evidence_id,agent_family_id,payload_json FROM evidence ORDER BY sequence"
                )
            )
            cycles = tuple(
                cycle_from_payload(row[0])
                for row in connection.execute("SELECT payload_json FROM cycles ORDER BY evidence_sequence")
            )
            results = tuple(
                result_from_payload(row[0]) for row in connection.execute("SELECT payload_json FROM results")
            )
            _ = tuple(
                open_work_from_payload(row[0]) for row in connection.execute("SELECT payload_json FROM open_work")
            )
    except InvalidResearchAgentOperationsSourceError:
        raise
    except (InvalidResearchAgentCycleStoreError, OSError, sqlite3.Error, TypeError, ValueError):
        raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_MALFORMED) from None
    history = CycleOperationsHistory(evidence=evidence, cycles=cycles, results=results, as_of=now)
    return tuple(_family_facts(family, history) for family in PRIMARY_AGENT_FAMILIES)


def read_task_receipts(root: Path) -> tuple[AutonomousTaskReceiptV1, ...]:
    files = private_store_files(root, "receipt")
    try:
        return tuple(AutonomousTaskReceiptV1.model_validate_json(read_private_text_query_only(path)) for path in files)
    except (InvalidPrivateQueryFileError, ValidationError):
        raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.RECEIPT_STORE_MALFORMED) from None


def private_store_files(root: Path, kind: StoreKind) -> tuple[Path, ...]:
    files: list[Path] = []
    entry_count = 0
    try:
        for entry in root.rglob("*"):
            entry_count += 1
            if entry_count > _MAX_ENTRIES:
                raise InvalidResearchAgentOperationsSourceError(_reason(kind, "malformed"))
            metadata = entry.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise InvalidResearchAgentOperationsSourceError(_reason(kind, "symlink"))
            if stat.S_ISDIR(metadata.st_mode):
                if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
                    raise InvalidResearchAgentOperationsSourceError(_reason(kind, "nonprivate"))
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise InvalidResearchAgentOperationsSourceError(_reason(kind, "nonprivate"))
            if metadata.st_nlink != 1:
                raise InvalidResearchAgentOperationsSourceError(_reason(kind, "hardlink"))
            files.append(entry)
    except InvalidResearchAgentOperationsSourceError:
        raise
    except OSError:
        raise InvalidResearchAgentOperationsSourceError(_reason(kind, "malformed")) from None
    return tuple(sorted(files))


def heavy_experiment_completions(files: tuple[Path, ...]) -> int:
    reports = tuple(path for path in files if path.name == "autonomous_research_cycle_ko.md")
    try:
        return sum(load_autonomous_cycle_cli_result(path.parent).status == "complete" for path in reports)
    except InvalidAutonomousCycleCliResultError:
        raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.RUNS_STORE_MALFORMED) from None


def _family_facts(family: AgentFamilyId, history: CycleOperationsHistory) -> CycleOperationsFacts:
    family_cycles = tuple(
        item for item in history.cycles if item.agent_family_id == family and item.terminal_at is not None
    )
    ordered = tuple(sorted(family_cycles, key=lambda item: item.terminal_at or item.started_at, reverse=True))
    failures = 0
    for cycle in ordered:
        if cycle.state is ResearchAgentCycleState.COMPLETED:
            break
        failures += 1
    successes = tuple(
        item
        for item in history.results
        if item.agent_family_id == family
        and item.status in {ResearchAgentResultStatus.COMPLETED, ResearchAgentResultStatus.NO_ACTION}
    )
    available = tuple(
        item.available_at
        for item in history.evidence
        if item.agent_family_id == family and item.available_at <= history.as_of
    )
    return CycleOperationsFacts(
        family=family,
        last_terminal_at=None if not ordered else ordered[0].terminal_at,
        last_success_at=None if not successes else max(item.occurred_at for item in successes),
        consecutive_failures=failures,
        last_evidence_at=None if not available else max(available),
    )


def _reason(kind: StoreKind, suffix: str) -> OperationsAlertReason:
    return OperationsAlertReason(f"{kind}_store_{suffix}")


__all__ = (
    "cycle_database_storage_bytes",
    "heavy_experiment_completions",
    "private_store_files",
    "read_cycle_operations_facts",
    "read_task_receipts",
    "require_operations_store",
)
