from __future__ import annotations

import datetime as dt
import hashlib
import os
import stat
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.hermes_delivery_models import (
    HERMES_DELIVERY_CONTRACT_VERSION,
    HermesDeliveryEvent,
    HermesDeliveryKind,
    build_hermes_delivery_event,
    hermes_delivery_id,
)
from trading_agent.hermes_delivery_store import HermesDeliveryWriter
from trading_agent.signal_contract_models import OpportunitySnapshot, SignalActionability, TradeSignalEnvelope
from trading_agent.trade_signal_outbox_reader import TradeSignalOutboxReaderError, read_trade_signal_publications
from trading_agent.trade_signal_publication import TradeSignalPublication


class InvalidHermesProjectionSourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "Hermes projection source is invalid"


class HermesProjectionSources(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    opportunity_outbox: Path
    signal_outbox: Path


class HermesProjectionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    source_event_id: str
    root_source_event_id: str | None
    kind: HermesDeliveryKind
    market_id: str
    agent_family: str
    lane_id: str | None
    strategy_version: str | None
    instrument_id: str | None
    occurred_at: dt.datetime
    status: str
    evidence_refs: tuple[str, ...]
    rendered_text: str
    payload_sha256: str

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        texts = (
            self.source_event_id,
            self.market_id,
            self.agent_family,
            self.status,
            self.rendered_text,
        )
        optional = (self.root_source_event_id, self.lane_id, self.strategy_version, self.instrument_id)
        if (
            any(not value or value != value.strip() for value in texts)
            or any(value is not None and (not value or value != value.strip()) for value in optional)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
            or len(self.payload_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.payload_sha256)
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
        ):
            raise InvalidHermesProjectionSourceError
        return self


class HermesProjectionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    examined: int
    inserted: int
    replayed: int


def project_contract_outboxes(
    sources: HermesProjectionSources,
    writer: HermesDeliveryWriter,
) -> HermesProjectionResult:
    opportunities = read_opportunity_snapshots(sources.opportunity_outbox)
    try:
        publications = read_trade_signal_publications(sources.signal_outbox)
    except TradeSignalOutboxReaderError:
        raise InvalidHermesProjectionSourceError from None
    records = tuple(
        record
        for opportunity in opportunities
        for record in opportunity_projection_records(opportunity)
    )
    root_sources = frozenset(record.source_event_id for record in records)
    records += tuple(
        signal_publication_projection_record(publication, root_sources)
        for publication in publications
    )
    return project_outcomes(records, writer)


def project_outcomes(
    records: tuple[HermesProjectionRecord, ...],
    writer: HermesDeliveryWriter,
) -> HermesProjectionResult:
    inserted = 0
    for untrusted in records:
        event = delivery_event_from_projection_record(untrusted)
        inserted += int(writer.append_event(event).inserted)
    return HermesProjectionResult(examined=len(records), inserted=inserted, replayed=len(records) - inserted)


def delivery_event_from_projection_record(
    untrusted: HermesProjectionRecord,
) -> HermesDeliveryEvent:
    record = HermesProjectionRecord.model_validate(untrusted.model_dump(mode="python"))
    root_delivery_id = (
        None
        if record.root_source_event_id is None
        else hermes_delivery_id(record.root_source_event_id, HERMES_DELIVERY_CONTRACT_VERSION)
    )
    return build_hermes_delivery_event(
        kind=record.kind,
        source_event_id=record.source_event_id,
        market_id=record.market_id,
        lane_id=record.lane_id,
        occurred_at=record.occurred_at,
        payload_sha256=record.payload_sha256,
        rendered_text=record.rendered_text,
        agent_family=record.agent_family,
        instrument_id=record.instrument_id,
        strategy_version=record.strategy_version,
        status=record.status,
        evidence_refs=record.evidence_refs,
        root_delivery_id=root_delivery_id,
    )


def project_opportunity_snapshots(
    snapshots: tuple[OpportunitySnapshot, ...],
    writer: HermesDeliveryWriter,
) -> HermesProjectionResult:
    validated = tuple(
        OpportunitySnapshot.model_validate(snapshot.model_dump(mode="python")) for snapshot in snapshots
    )
    records = tuple(
        record
        for snapshot in validated
        for record in opportunity_projection_records(snapshot)
    )
    return project_outcomes(records, writer)


def project_trade_signals(
    signals: tuple[TradeSignalEnvelope, ...],
    writer: HermesDeliveryWriter,
    root_source_event_ids: frozenset[str],
) -> HermesProjectionResult:
    validated = tuple(
        TradeSignalEnvelope.model_validate(signal.model_dump(mode="python")) for signal in signals
    )
    records = tuple(
        _trade_signal_record(
            signal,
            root_source_event_ids,
            hashlib.sha256(signal.model_dump_json().encode()).hexdigest(),
        )
        for signal in validated
    )
    return project_outcomes(records, writer)


def read_opportunity_snapshots(path: Path) -> tuple[OpportunitySnapshot, ...]:
    source = path.expanduser().absolute()
    if source.is_symlink():
        raise InvalidHermesProjectionSourceError
    if not source.exists():
        return ()
    try:
        metadata = source.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            raise InvalidHermesProjectionSourceError
        lines = source.read_bytes().splitlines()
        snapshots = tuple(OpportunitySnapshot.model_validate_json(line) for line in lines)
        identities = tuple(item.opportunity_id for item in snapshots)
        if not lines or len(identities) != len(set(identities)):
            raise InvalidHermesProjectionSourceError
        return snapshots
    except (OSError, TypeError, ValidationError, ValueError):
        raise InvalidHermesProjectionSourceError from None


def opportunity_projection_records(
    snapshot: OpportunitySnapshot,
) -> tuple[HermesProjectionRecord, ...]:
    incomplete = any(not coverage.complete for coverage in snapshot.source_coverage)
    kind = HermesDeliveryKind.INCIDENT if incomplete else HermesDeliveryKind.WATCH
    status = "blocked_source_incomplete" if incomplete else "watch"
    evidence = tuple(sorted(reference.canonical_id for reference in snapshot.evidence_refs))
    payload_sha256 = hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()
    return tuple(
        HermesProjectionRecord(
            source_event_id=f"{snapshot.opportunity_id}:{candidate.symbol}",
            root_source_event_id=None,
            kind=kind,
            market_id=snapshot.strategy_lane.market_id.value,
            agent_family=snapshot.strategy_lane.agent_family.value,
            lane_id=snapshot.strategy_lane.canonical_id,
            strategy_version=snapshot.producer_strategy_version,
            instrument_id=candidate.symbol,
            occurred_at=snapshot.observed_at,
            status=status,
            evidence_refs=evidence,
            rendered_text=f"Opportunity Manager: {candidate.symbol} rank {candidate.rank}, score {candidate.score}.",
            payload_sha256=payload_sha256,
        )
        for candidate in snapshot.candidates
    )


def signal_publication_projection_record(
    publication: TradeSignalPublication,
    root_sources: frozenset[str],
) -> HermesProjectionRecord:
    return _trade_signal_record(
        publication.signal,
        root_sources,
        hashlib.sha256(publication.model_dump_json().encode()).hexdigest(),
    )


def _trade_signal_record(
    signal: TradeSignalEnvelope,
    root_sources: frozenset[str],
    payload_sha256: str,
) -> HermesProjectionRecord:
    root_source = None if signal.opportunity_id is None else f"{signal.opportunity_id}:{signal.symbol}"
    kind = (
        HermesDeliveryKind.ACTIONABLE
        if signal.actionability is SignalActionability.CURRENT_QUOTE_VALIDATED
        else HermesDeliveryKind.WATCH
    )
    targets = ", ".join(f"{target.label} {target.price}" for target in signal.targets)
    return HermesProjectionRecord(
        source_event_id=signal.signal_id,
        root_source_event_id=root_source if root_source in root_sources else None,
        kind=kind,
        market_id=signal.strategy_lane.market_id.value,
        agent_family=signal.strategy_lane.agent_family.value,
        lane_id=signal.strategy_lane.canonical_id,
        strategy_version=signal.producer_strategy_version,
        instrument_id=signal.symbol,
        occurred_at=signal.observed_at,
        status=signal.actionability.value,
        evidence_refs=tuple(sorted(reference.canonical_id for reference in signal.evidence_refs)),
        rendered_text=(
            f"{signal.strategy_lane.agent_family.value}: {signal.symbol}, entry {signal.entry_price}, "
            f"stop {signal.stop_price}, targets {targets}. {signal.rationale}"
        ),
        payload_sha256=payload_sha256,
    )
