from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading_agent.execution_errors import (
    ExecutionSchemaIntegrityError,
    UnsupportedExecutionSchemaError,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_arm_authority import (
    LedgerHermesArmAuthorityConfig,
    LedgerHermesArmAuthorityResolver,
)
from trading_agent.hermes_arm_request import (
    HermesArmScope,
    InvalidHermesArmRequestError,
)
from trading_agent.us_equity_calendar import regular_session_bounds


@dataclass(frozen=True, slots=True)
class PaperSmokeEligibilityConfig:
    repository: Path
    lane_registry: Path
    experiment_ledger: Path
    execution_database: Path


@dataclass(frozen=True, slots=True)
class PaperSmokeEligibility:
    ready_to_request_arm: bool
    blockers: tuple[str, ...]


def audit_paper_smoke_eligibility(
    config: PaperSmokeEligibilityConfig,
    scope: HermesArmScope,
) -> PaperSmokeEligibility:
    try:
        session_date = dt.date.fromisoformat(scope.session_id[-10:])
    except ValueError:
        return PaperSmokeEligibility(False, ("invalid_request",))
    if scope.session_id != f"XNYS-{session_date.isoformat()}":
        return PaperSmokeEligibility(False, ("invalid_request",))
    if regular_session_bounds(session_date) is None:
        return PaperSmokeEligibility(False, ("non_regular_session",))
    resolver = LedgerHermesArmAuthorityResolver(
        LedgerHermesArmAuthorityConfig(
            repository=config.repository,
            lane_registry=config.lane_registry,
            experiment_ledger=config.experiment_ledger,
        )
    )
    try:
        authority = resolver.resolve(scope)
    except InvalidHermesArmRequestError as error:
        return PaperSmokeEligibility(False, (error.reason.value,))
    store = ExecutionStore(config.execution_database)
    try:
        if not store.is_initialized():
            return PaperSmokeEligibility(False, ("uninitialized_execution_store",))
        ledger = store.reconciliation_ledger()
    except (
        ExecutionSchemaIntegrityError,
        UnsupportedExecutionSchemaError,
        sqlite3.Error,
        OSError,
    ):
        return PaperSmokeEligibility(False, ("invalid_execution_store",))
    if ledger.account_fingerprint != authority.account_fingerprint:
        return PaperSmokeEligibility(False, ("account_mismatch",))
    if ledger.unresolved_intent_ids:
        return PaperSmokeEligibility(False, ("unresolved_execution_intents",))
    if ledger.pending_trade_update_receipt_keys:
        return PaperSmokeEligibility(False, ("pending_trade_update_receipts",))
    if ledger.unrecovered_trade_update_quarantine_keys:
        return PaperSmokeEligibility(
            False,
            ("unrecovered_trade_update_quarantine",),
        )
    return PaperSmokeEligibility(True, ())
