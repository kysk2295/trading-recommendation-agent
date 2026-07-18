from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Protocol

from trading_agent.intraday_feature_kernel import build_intraday_feature_snapshot
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_market_data_runtime_models import (
    MarketDataRuntimeBatch,
    MarketDataRuntimeCheckpoint,
    MarketDataRuntimeError,
    MarketDataRuntimeIncident,
    MarketDataRuntimeIncidentKind,
    MarketDataRuntimeReceipt,
    MarketDataRuntimeResult,
    MarketDataRuntimeStatus,
    RuntimeFeatureRequest,
    validate_runtime_request,
)
from trading_agent.us_market_data_runtime_store import MarketDataRuntimeStore
from trading_agent.us_subscription_models import (
    DesiredMarketDataSubscription,
    SubscriptionPolicyDecision,
    SubscriptionPolicyStatus,
)


class ReadOnlyUsMarketDataAdapter(Protocol):
    source_id: str

    def read_batch(
        self,
        desired: tuple[DesiredMarketDataSubscription, ...],
        after_sequence: int | None,
    ) -> MarketDataRuntimeBatch: ...


class UsMarketDataSupervisor:
    __slots__ = ("_adapter", "_clock", "_store")

    def __init__(
        self,
        adapter: ReadOnlyUsMarketDataAdapter,
        store: MarketDataRuntimeStore,
        *,
        clock: Callable[[], dt.datetime],
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._clock = clock

    def run_cycle(
        self,
        decision: SubscriptionPolicyDecision,
        requests: tuple[RuntimeFeatureRequest, ...],
    ) -> MarketDataRuntimeResult:
        source_id = self._validate_inputs(decision, requests)
        if decision.status is not SubscriptionPolicyStatus.READY or not decision.desired:
            return MarketDataRuntimeResult(
                MarketDataRuntimeStatus.BLOCKED_SUBSCRIPTION_POLICY,
                source_id,
                None,
                None,
                0,
                0,
                (),
                (),
            )

        with self._store.writer() as writer:
            prior = writer.latest_checkpoint(source_id)
            after_sequence = None if prior is None else prior.last_sequence
            batch = self._adapter.read_batch(decision.desired, after_sequence)
            self._validate_batch(batch, decision)
            now = self._valid_now()
            incidents: list[MarketDataRuntimeIncident] = []
            reconnect = prior is not None and batch.connection_epoch != prior.connection_epoch
            if prior is not None and reconnect:
                incident = MarketDataRuntimeIncident(
                    MarketDataRuntimeIncidentKind.RECONNECT,
                    source_id,
                    prior.connection_epoch,
                    batch.connection_epoch,
                    None,
                    None,
                    now,
                )
                _ = writer.append_incident(incident)
                incidents.append(incident)

            last_sequence = 0 if prior is None or reconnect else prior.last_sequence
            gap_blocked = False if prior is None or reconnect else prior.gap_blocked
            expected = last_sequence + 1
            inserted_count = 0
            duplicate_count = 0
            latest_received_at: dt.datetime | None = None
            for receipt in batch.receipts:
                inserted = writer.append_receipt(receipt)
                if not inserted:
                    duplicate_count += 1
                    continue
                inserted_count += 1
                latest_received_at = receipt.received_at
                if receipt.sequence != expected and not gap_blocked:
                    incident = MarketDataRuntimeIncident(
                        MarketDataRuntimeIncidentKind.SEQUENCE_GAP,
                        source_id,
                        None,
                        batch.connection_epoch,
                        expected,
                        receipt.sequence,
                        now,
                    )
                    _ = writer.append_incident(incident)
                    incidents.append(incident)
                    gap_blocked = True
                last_sequence = max(last_sequence, receipt.sequence)
                expected = last_sequence + 1

            if inserted_count == 0:
                return MarketDataRuntimeResult(
                    MarketDataRuntimeStatus.NO_NEW_DATA,
                    source_id,
                    batch.connection_epoch,
                    None if prior is None else prior.last_sequence,
                    0,
                    duplicate_count,
                    (),
                    tuple(incidents),
                )

            writer.append_checkpoint(
                MarketDataRuntimeCheckpoint(source_id, batch.connection_epoch, last_sequence, gap_blocked, now)
            )
            if gap_blocked:
                return MarketDataRuntimeResult(
                    MarketDataRuntimeStatus.BLOCKED_SEQUENCE_GAP,
                    source_id,
                    batch.connection_epoch,
                    last_sequence,
                    inserted_count,
                    duplicate_count,
                    (),
                    tuple(incidents),
                )

            if latest_received_at is None:
                raise MarketDataRuntimeError
            snapshots = tuple(
                build_intraday_feature_snapshot(
                    batch.identity,
                    request.instrument_id,
                    latest_received_at,
                    writer.completed_bars(source_id, batch.connection_epoch, request.instrument_id),
                    request.expected_cumulative_volume,
                )
                for request in requests
            )
            return MarketDataRuntimeResult(
                MarketDataRuntimeStatus.READY,
                source_id,
                batch.connection_epoch,
                last_sequence,
                inserted_count,
                duplicate_count,
                snapshots,
                tuple(incidents),
            )

    def _validate_inputs(
        self,
        decision: SubscriptionPolicyDecision,
        requests: tuple[RuntimeFeatureRequest, ...],
    ) -> str:
        source_id = self._adapter.source_id
        if type(decision) is not SubscriptionPolicyDecision:
            raise MarketDataRuntimeError
        if type(source_id) is not str or not source_id:
            raise MarketDataRuntimeError
        if type(requests) is not tuple:
            raise MarketDataRuntimeError
        for request in requests:
            validate_runtime_request(request)
        return source_id

    def _validate_batch(
        self,
        batch: MarketDataRuntimeBatch,
        decision: SubscriptionPolicyDecision,
    ) -> None:
        if type(batch) is not MarketDataRuntimeBatch:
            raise MarketDataRuntimeError
        if batch.source_id != self._adapter.source_id or not batch.connection_epoch:
            raise MarketDataRuntimeError
        if type(batch.identity) is not ResearchInputIdentity or type(batch.receipts) is not tuple:
            raise MarketDataRuntimeError
        desired = {(item.instrument_id, item.symbol) for item in decision.desired}
        previous = 0
        for receipt in batch.receipts:
            if (
                type(receipt) is not MarketDataRuntimeReceipt
                or receipt.source_id != batch.source_id
                or receipt.connection_epoch != batch.connection_epoch
                or (receipt.instrument_id, receipt.symbol) not in desired
                or receipt.sequence < previous
            ):
                raise MarketDataRuntimeError
            previous = receipt.sequence

    def _valid_now(self) -> dt.datetime:
        now = self._clock()
        if type(now) is not dt.datetime or now.tzinfo is None or now.utcoffset() is None:
            raise MarketDataRuntimeError
        return now


__all__ = ("ReadOnlyUsMarketDataAdapter", "UsMarketDataSupervisor")
