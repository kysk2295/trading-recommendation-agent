from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never, override

from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.research_agent_soak_models import (
    SoakCheckpoint,
    SoakCheckpointKind,
    SoakCheckpointPayload,
    SoakEvidenceMode,
    SoakObservation,
    canonical_payload_json,
)
from trading_agent.research_agent_soak_runtime import capture_soak_observation
from trading_agent.research_agent_soak_sqlite import (
    create_soak_database,
    initialize_schema,
    open_soak_database,
    require_schema,
)

_ZERO_HASH: Final = "0" * 64
_SELECT_CHAIN: Final = "SELECT sequence,payload_json,checkpoint_sha256 FROM checkpoints ORDER BY sequence"


@dataclass(frozen=True, slots=True)
class InvalidResearchAgentSoakStoreError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return "research-agent soak store is invalid"


@dataclass(frozen=True, slots=True)
class _CheckpointRequest:
    kind: SoakCheckpointKind
    mode: SoakEvidenceMode
    observation: SoakObservation


@dataclass(frozen=True, slots=True)
class _AppendRequest:
    kind: SoakCheckpointKind
    observation: SoakObservation
    required_mode: SoakEvidenceMode | None


class ResearchAgentSoakStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def prepare_actual(self) -> SoakCheckpoint:
        return self._prepare(SoakEvidenceMode.ACTUAL, capture_soak_observation())

    def prepare_controlled(self, observation: SoakObservation) -> SoakCheckpoint:
        return self._prepare(SoakEvidenceMode.CONTROLLED_FIXTURE, observation)

    def append_current(self, kind: SoakCheckpointKind) -> SoakCheckpoint:
        return self._append(_AppendRequest(kind=kind, observation=capture_soak_observation(), required_mode=None))

    def append_controlled(self, kind: SoakCheckpointKind, observation: SoakObservation) -> SoakCheckpoint:
        return self._append(
            _AppendRequest(
                kind=kind,
                observation=observation,
                required_mode=SoakEvidenceMode.CONTROLLED_FIXTURE,
            )
        )

    def records(self) -> tuple[SoakCheckpoint, ...]:
        try:
            with open_soak_database(self.path, write=False) as connection:
                require_schema(connection)
                rows: list[tuple[int, str, str]] = connection.execute(_SELECT_CHAIN).fetchall()
                records = _parse_chain(rows)
            return records
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidResearchAgentSoakStoreError(reason="read_failed") from None

    def _prepare(self, mode: SoakEvidenceMode, observation: SoakObservation) -> SoakCheckpoint:
        request = _CheckpointRequest(kind=SoakCheckpointKind.PREPARED, mode=mode, observation=observation)
        checkpoint = _build_checkpoint(request, ())
        try:
            with create_soak_database(self.path) as connection:
                initialize_schema(connection)
                _insert_checkpoint(connection, checkpoint)
            return checkpoint
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidResearchAgentSoakStoreError(reason="prepare_failed") from None

    def _append(self, append: _AppendRequest) -> SoakCheckpoint:
        if append.kind is SoakCheckpointKind.PREPARED:
            raise InvalidResearchAgentSoakStoreError(reason="prepared_append_forbidden")
        try:
            with open_soak_database(self.path, write=True) as connection:
                connection.execute("BEGIN IMMEDIATE")
                require_schema(connection)
                rows: list[tuple[int, str, str]] = connection.execute(_SELECT_CHAIN).fetchall()
                records = _parse_chain(rows)
                mode = records[0].payload.evidence_mode
                if append.required_mode is not None and mode is not append.required_mode:
                    raise InvalidResearchAgentSoakStoreError(reason="controlled_append_to_actual_forbidden")
                request = _CheckpointRequest(kind=append.kind, mode=mode, observation=append.observation)
                checkpoint = _build_checkpoint(request, records)
                _require_event_order(checkpoint, records)
                _insert_checkpoint(connection, checkpoint)
            return checkpoint
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidResearchAgentSoakStoreError(reason="append_failed") from None


def _build_checkpoint(request: _CheckpointRequest, records: tuple[SoakCheckpoint, ...]) -> SoakCheckpoint:
    previous = _ZERO_HASH if not records else records[-1].checkpoint_sha256
    payload = SoakCheckpointPayload(
        sequence=len(records) + 1,
        recorded_at=request.observation.recorded_at,
        monotonic_ns=request.observation.monotonic_ns,
        boot_sha256=request.observation.boot_sha256,
        invocation_sha256=request.observation.invocation_sha256,
        evidence_mode=request.mode,
        kind=request.kind,
        previous_sha256=previous,
    )
    canonical = canonical_payload_json(payload)
    return SoakCheckpoint(payload=payload, checkpoint_sha256=hashlib.sha256(canonical.encode()).hexdigest())


def _insert_checkpoint(connection: sqlite3.Connection, checkpoint: SoakCheckpoint) -> None:
    _ = connection.execute(
        "INSERT INTO checkpoints(sequence,payload_json,checkpoint_sha256) VALUES (?,?,?)",
        (checkpoint.payload.sequence, canonical_payload_json(checkpoint.payload), checkpoint.checkpoint_sha256),
    )
    connection.commit()


def _require_event_order(checkpoint: SoakCheckpoint, records: tuple[SoakCheckpoint, ...]) -> None:
    first = records[0].payload
    match checkpoint.payload.kind:
        case SoakCheckpointKind.REBOOT_RECOVERED:
            if checkpoint.payload.boot_sha256 == first.boot_sha256:
                raise InvalidResearchAgentSoakStoreError(reason="reboot_not_observed")
        case SoakCheckpointKind.PROVIDER_OUTAGE_RECOVERED:
            observed = any(item.payload.kind is SoakCheckpointKind.PROVIDER_OUTAGE_OBSERVED for item in records)
            if not observed:
                raise InvalidResearchAgentSoakStoreError(reason="provider_outage_not_observed")
        case (
            SoakCheckpointKind.PREPARED
            | SoakCheckpointKind.HEARTBEAT
            | SoakCheckpointKind.PROCESS_RESTART
            | SoakCheckpointKind.PROVIDER_OUTAGE_OBSERVED
        ):
            return
        case unreachable:
            assert_never(unreachable)


def _parse_chain(rows: list[tuple[int, str, str]]) -> tuple[SoakCheckpoint, ...]:
    records: list[SoakCheckpoint] = []
    previous = _ZERO_HASH
    for expected, row in enumerate(rows, start=1):
        sequence, raw_payload, stored_hash = row
        payload = SoakCheckpointPayload.model_validate_json(raw_payload, strict=True)
        canonical = canonical_payload_json(payload)
        calculated = hashlib.sha256(canonical.encode()).hexdigest()
        if sequence != expected or payload.sequence != expected or raw_payload != canonical:
            raise InvalidResearchAgentSoakStoreError(reason="sequence_invalid")
        if payload.previous_sha256 != previous or stored_hash != calculated:
            raise InvalidResearchAgentSoakStoreError(reason="chain_invalid")
        _require_chain_record(payload, records, expected)
        records.append(SoakCheckpoint(payload=payload, checkpoint_sha256=stored_hash))
        previous = stored_hash
    if not records:
        raise InvalidResearchAgentSoakStoreError(reason="chain_empty")
    return tuple(records)


def _require_chain_record(payload: SoakCheckpointPayload, records: list[SoakCheckpoint], expected: int) -> None:
    if expected == 1 and payload.kind is not SoakCheckpointKind.PREPARED:
        raise InvalidResearchAgentSoakStoreError(reason="genesis_invalid")
    if expected > 1 and payload.evidence_mode is not records[0].payload.evidence_mode:
        raise InvalidResearchAgentSoakStoreError(reason="mode_changed")
    if records and payload.recorded_at < records[-1].payload.recorded_at:
        raise InvalidResearchAgentSoakStoreError(reason="time_reordered")
    if (
        records
        and payload.boot_sha256 == records[-1].payload.boot_sha256
        and payload.monotonic_ns < records[-1].payload.monotonic_ns
    ):
        raise InvalidResearchAgentSoakStoreError(reason="monotonic_reordered")


__all__ = ("InvalidResearchAgentSoakStoreError", "ResearchAgentSoakStore")
