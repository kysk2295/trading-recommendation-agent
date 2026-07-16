from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import override

from trading_agent.daily_research_contract import (
    EVALUATOR_VERSION,
    strategy_contract,
    strategy_version_identity,
)
from trading_agent.daily_research_models import DailyResearchRecord
from trading_agent.daily_research_record_source import load_daily_research_record_source
from trading_agent.execution_store_reader import ExecutionStoreReader
from trading_agent.lane_contract_keys import (
    ExperimentScopeKey,
    LaneManifestKey,
    experiment_scope_key,
    lane_manifest_key,
)
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import (
    INTRADAY_MANIFEST,
    current_intraday_experiment_scope,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryReader, LaneRegistryStore
from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_order_gate_models import CompletePaperPortfolio
from trading_agent.paper_runtime import MAX_RUNTIME_RECEIPT_AGE, PaperRuntimeReadiness
from trading_agent.strategy_factory import StrategyMode
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

ORB_SCOPE = current_intraday_experiment_scope("H-MOM-ORB-001")
ORB_SCOPE_KEY = experiment_scope_key(ORB_SCOPE)
INTRADAY_MANIFEST_KEY = lane_manifest_key(INTRADAY_MANIFEST)
DATA_QUALITY_INCOMPLETE = "data_quality_incomplete"


class InvalidIntradayLaneFinalizationError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "intraday lane 일일 확정 근거가 승인된 장종료·평탄·계보 조건을 충족하지 않습니다"


@dataclass(frozen=True, slots=True)
class IntradayLaneSnapshotPreflight:
    manifest_key: LaneManifestKey
    experiment_scope_key: ExperimentScopeKey
    source_ledger_generation: int
    source_ledger_sha256: str = field(repr=False)
    account_fingerprint: AccountFingerprint = field(repr=False)
    daily_record: DailyResearchRecord = field(repr=False)
    daily_record_raw_sha256: str = field(repr=False)
    existing_snapshot: LaneDailySnapshot | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class IntradayLaneSnapshotResult:
    created: bool
    snapshot: LaneDailySnapshot


def preflight_intraday_lane_day(
    registry: LaneRegistryReader,
    execution: ExecutionStoreReader,
    session: Path,
    session_date: dt.date,
    *,
    evaluated_at: dt.datetime,
) -> IntradayLaneSnapshotPreflight:
    try:
        return _preflight_intraday_lane_day(
            registry,
            execution,
            session,
            session_date,
            evaluated_at=evaluated_at,
        )
    except InvalidIntradayLaneFinalizationError:
        raise
    except (OSError, RuntimeError, UnicodeError, ValueError, sqlite3.Error):
        raise InvalidIntradayLaneFinalizationError from None


def finalize_intraday_lane_day(
    registry: LaneRegistryStore,
    execution: ExecutionStoreReader,
    session: Path,
    session_date: dt.date,
    readiness: PaperRuntimeReadiness,
    *,
    evaluated_at: dt.datetime,
) -> IntradayLaneSnapshotResult:
    before_readiness = preflight_intraday_lane_day(
        registry,
        execution,
        session,
        session_date,
        evaluated_at=evaluated_at,
    )
    close_at = _session_close(session_date)
    _require_flat_post_close_readiness(
        readiness,
        before_readiness.account_fingerprint,
        close_at,
        evaluated_at,
    )
    after_readiness = preflight_intraday_lane_day(
        registry,
        execution,
        session,
        session_date,
        evaluated_at=evaluated_at,
    )
    if before_readiness != after_readiness:
        raise InvalidIntradayLaneFinalizationError

    record = after_readiness.daily_record
    data_quality_complete = record.session_quality.forward_day_eligible
    incidents = set(record.incidents)
    if not data_quality_complete:
        incidents.add(DATA_QUALITY_INCOMPLETE)
    existing = after_readiness.existing_snapshot
    snapshot = LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=session_date,
        finalized_at=(evaluated_at if existing is None else existing.finalized_at),
        manifest_key=after_readiness.manifest_key,
        experiment_scope_keys=(after_readiness.experiment_scope_key,),
        source_ledger_generation=after_readiness.source_ledger_generation,
        source_ledger_sha256=after_readiness.source_ledger_sha256,
        champion_strategy_versions=(),
        data_quality_complete=data_quality_complete,
        allocation_eligible=False,
        incidents=tuple(sorted(incidents)),
        conservative_equity=min(
            readiness.broker_state.account.equity,
            readiness.broker_state.account.last_equity,
        ),
        realized_pnl=(readiness.broker_state.account.equity - readiness.broker_state.account.last_equity),
        unrealized_pnl=Decimal(0),
        planned_open_risk=Decimal(0),
        open_order_count=0,
        open_position_count=0,
    )
    with registry.writer() as writer:
        created = writer.append_daily_snapshot(snapshot)
    return IntradayLaneSnapshotResult(created, snapshot)


def _preflight_intraday_lane_day(
    registry: LaneRegistryReader,
    execution: ExecutionStoreReader,
    session: Path,
    session_date: dt.date,
    *,
    evaluated_at: dt.datetime,
) -> IntradayLaneSnapshotPreflight:
    close_at = _session_close(session_date)
    if (
        not _aware(evaluated_at)
        or evaluated_at.astimezone(NEW_YORK).date() != session_date
        or evaluated_at.astimezone(dt.UTC) < close_at
    ):
        raise InvalidIntradayLaneFinalizationError
    if not registry.is_initialized() or not execution.is_initialized():
        raise InvalidIntradayLaneFinalizationError

    manifests = tuple(stored for stored in registry.manifests() if stored.manifest_key == INTRADAY_MANIFEST_KEY)
    if len(manifests) != 1 or manifests[0].manifest != INTRADAY_MANIFEST:
        raise InvalidIntradayLaneFinalizationError
    scopes = tuple(stored for stored in registry.experiment_scopes() if stored.scope_key == ORB_SCOPE_KEY)
    if len(scopes) != 1 or scopes[0].scope != ORB_SCOPE:
        raise InvalidIntradayLaneFinalizationError

    bindings = tuple(
        stored.binding for stored in registry.account_bindings() if stored.binding.lane_id is LaneId.INTRADAY_MOMENTUM
    )
    execution_binding = execution.account_binding()
    if len(bindings) != 1 or execution_binding is None:
        raise InvalidIntradayLaneFinalizationError
    binding = bindings[0]
    try:
        execution_bound_at = dt.datetime.fromisoformat(execution_binding.bound_at)
    except ValueError:
        raise InvalidIntradayLaneFinalizationError from None
    expected_ledger_fingerprint = hashlib.sha256(str(execution.path.resolve(strict=False)).encode()).hexdigest()
    if (
        binding.account_fingerprint != execution_binding.account_fingerprint
        or binding.execution_ledger_fingerprint != expected_ledger_fingerprint
        or not _aware(execution_bound_at)
        or execution_bound_at != binding.bound_at
        or execution_bound_at > evaluated_at
    ):
        raise InvalidIntradayLaneFinalizationError

    source = load_daily_research_record_source(
        session,
        session_date,
        StrategyMode.ORB,
        ORB_SCOPE_KEY,
    )
    contract = strategy_contract(StrategyMode.ORB)
    record = source.record
    if (
        record.experiment_scope != ORB_SCOPE
        or record.experiment_scope_key != ORB_SCOPE_KEY
        or record.hypothesis_id != contract.hypothesis_id
        or record.strategy != StrategyMode.ORB.value
        or record.strategy_version != strategy_version_identity(
            StrategyMode.ORB,
            record.code_version,
        )
        or record.evaluator_version != EVALUATOR_VERSION
        or not _aware(record.recorded_at)
        or record.recorded_at > evaluated_at
    ):
        raise InvalidIntradayLaneFinalizationError

    identity = execution.ledger_snapshot_identity()
    existing = registry.daily_snapshot(LaneId.INTRADAY_MOMENTUM, session_date)
    return IntradayLaneSnapshotPreflight(
        manifest_key=INTRADAY_MANIFEST_KEY,
        experiment_scope_key=ORB_SCOPE_KEY,
        source_ledger_generation=identity.generation,
        source_ledger_sha256=identity.sha256,
        account_fingerprint=execution_binding.account_fingerprint,
        daily_record=record,
        daily_record_raw_sha256=source.raw_sha256,
        existing_snapshot=None if existing is None else existing.snapshot,
    )


def _require_flat_post_close_readiness(
    readiness: PaperRuntimeReadiness,
    expected_account_fingerprint: AccountFingerprint,
    close_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> None:
    state = readiness.broker_state
    account = state.account
    portfolio = readiness.portfolio
    heartbeat = readiness.stream_heartbeat
    clock = readiness.market_clock
    if (
        not readiness.ready
        or readiness.reasons
        or clock.is_open
        or account.status != "ACTIVE"
        or account.trading_blocked
        or account.account_fingerprint != expected_account_fingerprint
        or state.open_orders
        or state.protective_ocos
        or any(position.quantity != 0 or position.market_value != 0 for position in state.positions)
        or not isinstance(portfolio, CompletePaperPortfolio)
        or portfolio.exposures
        or portfolio.account_status != account.status
        or portfolio.trading_blocked != account.trading_blocked
        or portfolio.equity != account.equity
        or portfolio.last_equity != account.last_equity
        or portfolio.buying_power != account.buying_power
        or not all(
            value.is_finite()
            for value in (
                account.equity,
                account.last_equity,
                account.buying_power,
            )
        )
        or account.equity < 0
        or account.last_equity < 0
        or not _ordered_heartbeat(readiness)
        or not all(
            _current_post_close_receipt(value, close_at, evaluated_at)
            for value in (
                account.observed_at,
                clock.observed_at,
                clock.market_timestamp,
                heartbeat.pong_at,
                portfolio.observed_at,
            )
        )
    ):
        raise InvalidIntradayLaneFinalizationError


def _ordered_heartbeat(readiness: PaperRuntimeReadiness) -> bool:
    heartbeat = readiness.stream_heartbeat
    values = (
        heartbeat.authorized_at,
        heartbeat.subscribed_at,
        heartbeat.pong_at,
    )
    return all(_aware(value) for value in values) and values[0] <= values[1] <= values[2]


def _current_post_close_receipt(
    value: dt.datetime,
    close_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> bool:
    if not _aware(value):
        return False
    observed = value.astimezone(dt.UTC)
    evaluated = evaluated_at.astimezone(dt.UTC)
    return close_at <= observed <= evaluated and evaluated - observed <= MAX_RUNTIME_RECEIPT_AGE


def _session_close(session_date: dt.date) -> dt.datetime:
    bounds = regular_session_bounds(session_date)
    if bounds is None:
        raise InvalidIntradayLaneFinalizationError
    return bounds[1].astimezone(dt.UTC)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
