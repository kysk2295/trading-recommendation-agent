from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.hermes_delivery_models import HermesDeliveryEvent, HermesDeliveryKind
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionSources,
    delivery_event_from_projection_record,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.research_identity_models import AgentFamily
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_session_delivery_projection import (
    us_session_projection_records,
    us_session_projection_sha256,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RECOVERY_WINDOW = dt.timedelta(hours=24)


class InvalidUsSessionDeliveryTerminalError(ValueError):
    @override
    def __str__(self) -> str:
        return "US session delivery terminal is invalid"


class UsSessionDeliveryTerminalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    sources: HermesProjectionSources
    session_date: dt.date
    finalized_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if self.finalized_at.tzinfo is None or self.finalized_at.utcoffset() is None:
            raise InvalidUsSessionDeliveryTerminalError
        return self


class UsSessionDeliveryTerminalArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    policy_version: Literal["us-session-hermes-terminal-v1"] = (
        "us-session-hermes-terminal-v1"
    )
    session_date: dt.date
    finalized_at: dt.datetime
    source_projection_sha256: str
    watch_count: int
    signal_count: int
    signal_symbols: tuple[str, ...]
    event: HermesDeliveryEvent

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        signal_terminal = self.signal_count > 0
        expected_kind = (
            HermesDeliveryKind.DAILY_SUMMARY
            if signal_terminal
            else HermesDeliveryKind.NO_RECOMMENDATION
        )
        expected_status = "session_summary" if signal_terminal else "censored_no_setup"
        if (
            self.finalized_at.tzinfo is None
            or self.finalized_at.utcoffset() is None
            or _HEX64.fullmatch(self.source_projection_sha256) is None
            or self.watch_count < 1
            or self.signal_count < 0
            or self.signal_symbols != tuple(sorted(set(self.signal_symbols)))
            or (not signal_terminal and self.signal_symbols)
            or len(self.signal_symbols) > self.signal_count
            or self.event.kind is not expected_kind
            or self.event.status != expected_status
            or self.event.occurred_at != self.finalized_at
            or self.event.root_delivery_id != self.event.delivery_id
            or self.event.market_id != "us_equities"
            or self.event.agent_family != AgentFamily.DAY_TRADING.value
            or self.event.instrument_id is not None
            or self.event.payload_sha256 not in self.event.source_event_id
        ):
            raise InvalidUsSessionDeliveryTerminalError
        return self


class UsSessionDeliveryTerminalResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    artifact: UsSessionDeliveryTerminalArtifact
    inserted: int
    replayed: int


def build_us_session_delivery_terminal(
    request: UsSessionDeliveryTerminalRequest,
) -> UsSessionDeliveryTerminalArtifact:
    request = UsSessionDeliveryTerminalRequest.model_validate(
        request.model_dump(mode="python")
    )
    bounds = regular_session_bounds(request.session_date)
    if bounds is None:
        raise InvalidUsSessionDeliveryTerminalError
    close = bounds[1]
    if not close <= request.finalized_at <= close + _RECOVERY_WINDOW:
        raise InvalidUsSessionDeliveryTerminalError
    records = us_session_projection_records(request.sources, request.session_date)
    if not records or any(record.occurred_at > close for record in records):
        raise InvalidUsSessionDeliveryTerminalError
    watch_records = tuple(
        record
        for record in records
        if record.agent_family == AgentFamily.OPPORTUNITY_MANAGER.value
    )
    signal_records = tuple(
        record
        for record in records
        if record.agent_family == AgentFamily.DAY_TRADING.value
    )
    if len(watch_records) + len(signal_records) != len(records) or not watch_records:
        raise InvalidUsSessionDeliveryTerminalError
    source_digest = us_session_projection_sha256(records)
    signal_symbols = tuple(
        sorted(
            {
                record.instrument_id
                for record in signal_records
                if record.instrument_id is not None
            }
        )
    )
    material = json.dumps(
        (
            request.session_date.isoformat(),
            source_digest,
            len(watch_records),
            len(signal_records),
            signal_symbols,
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode()).hexdigest()
    kind = (
        HermesDeliveryKind.DAILY_SUMMARY
        if signal_records
        else HermesDeliveryKind.NO_RECOMMENDATION
    )
    status = "session_summary" if signal_records else "censored_no_setup"
    record = HermesProjectionRecord(
        source_event_id=f"us-session-terminal-{digest}",
        root_source_event_id=None,
        kind=kind,
        market_id="us_equities",
        agent_family=AgentFamily.DAY_TRADING.value,
        lane_id=None,
        strategy_version=_single_strategy_version(signal_records),
        instrument_id=None,
        occurred_at=request.finalized_at,
        status=status,
        evidence_refs=tuple(sorted(item.source_event_id for item in records)),
        rendered_text=_render_terminal(
            request.session_date,
            len(watch_records),
            len(signal_records),
            signal_symbols,
        ),
        payload_sha256=digest,
    )
    return UsSessionDeliveryTerminalArtifact(
        session_date=request.session_date,
        finalized_at=request.finalized_at,
        source_projection_sha256=source_digest,
        watch_count=len(watch_records),
        signal_count=len(signal_records),
        signal_symbols=signal_symbols,
        event=delivery_event_from_projection_record(record),
    )


def project_us_session_delivery_terminal(
    request: UsSessionDeliveryTerminalRequest,
    store: HermesDeliveryStore,
) -> UsSessionDeliveryTerminalResult:
    artifact = build_us_session_delivery_terminal(request)
    record = _artifact_record(artifact)
    with store.writer() as writer:
        projection = project_outcomes((record,), writer)
    return UsSessionDeliveryTerminalResult(
        artifact=artifact,
        inserted=projection.inserted,
        replayed=projection.replayed,
    )


def write_us_session_delivery_terminal(
    destination: Path,
    artifact: UsSessionDeliveryTerminalArtifact,
) -> None:
    validated = UsSessionDeliveryTerminalArtifact.model_validate(
        artifact.model_dump(mode="python")
    )
    write_private_stable_report(
        destination,
        validated.model_dump_json(indent=2) + "\n",
    )


def read_us_session_delivery_terminal(
    source: Path,
) -> UsSessionDeliveryTerminalArtifact:
    try:
        return UsSessionDeliveryTerminalArtifact.model_validate_json(
            read_private_text_query_only(source)
        )
    except (InvalidPrivateQueryFileError, ValidationError, ValueError):
        raise InvalidUsSessionDeliveryTerminalError from None


def _artifact_record(
    artifact: UsSessionDeliveryTerminalArtifact,
) -> HermesProjectionRecord:
    event = artifact.event
    return HermesProjectionRecord(
        source_event_id=event.source_event_id,
        root_source_event_id=None,
        kind=event.kind,
        market_id=event.market_id,
        agent_family=event.agent_family or AgentFamily.DAY_TRADING.value,
        lane_id=event.lane_id,
        strategy_version=event.strategy_version,
        instrument_id=event.instrument_id,
        occurred_at=event.occurred_at,
        status=event.status,
        evidence_refs=event.evidence_refs,
        rendered_text=event.rendered_text,
        payload_sha256=event.payload_sha256,
    )


def _single_strategy_version(
    records: tuple[HermesProjectionRecord, ...],
) -> str | None:
    versions = {record.strategy_version for record in records}
    return next(iter(versions)) if len(versions) == 1 else None


def _render_terminal(
    session_date: dt.date,
    watches: int,
    signals: int,
    symbols: tuple[str, ...],
) -> str:
    if not signals:
        return f"US Day {session_date.isoformat()}: no eligible setup from {watches} watched symbols."
    joined = ", ".join(symbols)
    return (
        f"US Day {session_date.isoformat()}: {signals} published signals ({joined}) from "
        f"{watches} watched symbols. Signal-count summary, not a performance result."
    )
