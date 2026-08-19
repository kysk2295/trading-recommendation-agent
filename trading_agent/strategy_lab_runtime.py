from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

from pydantic import BaseModel, ConfigDict, ValidationError

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)
from trading_agent.strategy_lab_kernel import StrategyLabFleet
from trading_agent.strategy_lab_models import (
    STRATEGY_LAB_IDS,
    LabEvidenceBatch,
    StrategyLabEvidenceBundle,
    StrategyLabId,
    strategy_lab_spec,
)


class StrategyLabRuntimeDepth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    lab_id: StrategyLabId
    depth: int


class StrategyLabRuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["completed", "waiting_evidence", "waiting_availability", "blocked"]
    current_cycle: int
    trace_depths: tuple[StrategyLabRuntimeDepth, ...]
    next_wake_at: dt.datetime | None
    observed_at: dt.datetime
    broker_mutation: Literal[0] = 0
    trading_mutation: Literal[0] = 0


@dataclass(frozen=True, slots=True)
class _InvalidBundle:
    pass


class StrategyLabRuntime:
    def __init__(
        self,
        evidence_bundle_path: Path,
        ledger: ExperimentLedgerStore,
    ) -> None:
        self._evidence_bundle_path = evidence_bundle_path
        self._ledger = ledger

    def tick(self, observed_at: dt.datetime) -> StrategyLabRuntimeStatus:
        if not self._migrate_existing_ledger():
            return self._status("blocked", (), observed_at, None)
        depths = self._depths()
        if depths is None or observed_at.tzinfo is None:
            return self._status("blocked", (), observed_at, None)
        bundle = self._bundle()
        match bundle:
            case None:
                return self._status("waiting_evidence", depths, observed_at, None)
            case _InvalidBundle():
                return self._status("blocked", depths, observed_at, None)
            case StrategyLabEvidenceBundle():
                pass
            case unreachable:
                assert_never(unreachable)
        if not _bundle_matches_specs(bundle):
            return self._status("blocked", depths, observed_at, None)
        cycle = _cycle_number(depths)
        next_batches = _next_batches(bundle, cycle)
        if next_batches is None:
            return self._status("waiting_evidence", depths, observed_at, None)
        next_wake_at = max(batch.available_at for batch in next_batches)
        if observed_at < next_wake_at:
            return self._status("waiting_availability", depths, observed_at, next_wake_at)
        try:
            _ = StrategyLabFleet(self._ledger).run_cycle(bundle, observed_at)
        except (
            ExperimentLedgerConflictError,
            ExperimentLedgerWriterLeaseUnavailableError,
            InvalidExperimentLedgerSourceError,
            UnsupportedExperimentLedgerSchemaError,
            sqlite3.DatabaseError,
            ValueError,
        ):
            return self._status("blocked", depths, observed_at, None)
        completed_depths = self._depths()
        if completed_depths is None:
            return self._status("blocked", (), observed_at, None)
        following_batches = _next_batches(bundle, _cycle_number(completed_depths))
        following_wake_at = (
            None
            if following_batches is None
            else max(batch.available_at for batch in following_batches)
        )
        return self._status("completed", completed_depths, observed_at, following_wake_at)

    def _migrate_existing_ledger(self) -> bool:
        if not self._ledger.path.exists():
            return True
        try:
            with self._ledger.writer():
                pass
            return True
        except (
            ExperimentLedgerWriterLeaseUnavailableError,
            InvalidExperimentLedgerSourceError,
            UnsupportedExperimentLedgerSchemaError,
            sqlite3.DatabaseError,
            OSError,
            ValueError,
        ):
            return False

    def _bundle(self) -> StrategyLabEvidenceBundle | _InvalidBundle | None:
        if not self._evidence_bundle_path.is_absolute() or not self._evidence_bundle_path.exists():
            return None
        try:
            payload = read_private_text_query_only(self._evidence_bundle_path)
            return StrategyLabEvidenceBundle.model_validate_json(payload)
        except (InvalidPrivateQueryFileError, ValidationError):
            return _InvalidBundle()

    def _depths(self) -> tuple[StrategyLabRuntimeDepth, ...] | None:
        try:
            depths = tuple(
                StrategyLabRuntimeDepth(lab_id=lab_id, depth=len(self._ledger.strategy_lab_trace(lab_id)))
                for lab_id in STRATEGY_LAB_IDS
            )
        except (InvalidExperimentLedgerSourceError, UnsupportedExperimentLedgerSchemaError, sqlite3.DatabaseError):
            return None
        return depths if len({item.depth for item in depths}) == 1 else None

    @staticmethod
    def _status(
        status: Literal["completed", "waiting_evidence", "waiting_availability", "blocked"],
        depths: tuple[StrategyLabRuntimeDepth, ...],
        observed_at: dt.datetime,
        next_wake_at: dt.datetime | None,
    ) -> StrategyLabRuntimeStatus:
        return StrategyLabRuntimeStatus(
            status=status,
            current_cycle=_cycle_number(depths),
            trace_depths=depths,
            next_wake_at=next_wake_at,
            observed_at=observed_at,
        )


def _bundle_matches_specs(bundle: StrategyLabEvidenceBundle) -> bool:
    return all(
        batch.feature_name == strategy_lab_spec(batch.lab_id).feature_name
        and batch.target_name == strategy_lab_spec(batch.lab_id).target_name
        for batch in bundle.batches
    )


def _cycle_number(depths: tuple[StrategyLabRuntimeDepth, ...]) -> int:
    return 0 if not depths else depths[0].depth


def _next_batches(
    bundle: StrategyLabEvidenceBundle,
    cycle_number: int,
) -> tuple[LabEvidenceBatch, ...] | None:
    batches = tuple(bundle.batches_for(lab_id) for lab_id in STRATEGY_LAB_IDS)
    if any(len(lab_batches) <= cycle_number for lab_batches in batches):
        return None
    return tuple(lab_batches[cycle_number] for lab_batches in batches)


__all__ = ("StrategyLabRuntime", "StrategyLabRuntimeDepth", "StrategyLabRuntimeStatus")
