from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_autonomous_research import (
    AutonomousTriggerV1,
    TriggerAuthority,
    TriggerType,
)
from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    open_private_parent,
    require_private_directory,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_query_file import InvalidPrivateQueryFileError, read_private_text_query_only


class TriggerAuthorityResolver(Protocol):
    def blocker(self, trigger: AutonomousTriggerV1, now: dt.datetime) -> str | None: ...


class TriggerAuthorityRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    authority_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{8,160}$")
    trigger_type: TriggerType
    authority: TriggerAuthority
    agent_family_id: AgentFamilyId
    source_receipt_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: AwareDatetime
    authorized_at: AwareDatetime
    expires_at: AwareDatetime


class InvalidTriggerAuthorityStoreError(RuntimeError):
    pass


class TriggerAuthorityStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=False)
        try:
            descriptor = open_private_parent(self._root, create=True)
            try:
                require_private_directory(descriptor)
            finally:
                os.close(descriptor)
        except (InvalidPrivateDirectoryIdentityError, OSError) as error:
            raise InvalidTriggerAuthorityStoreError from error

    def append(self, record: TriggerAuthorityRecordV1) -> bool:
        path = self._root / f"{record.authority_id}.json"
        try:
            return publish_private_immutable_text(path, record.model_dump_json())
        except InvalidPrivateImmutableFileError as error:
            raise InvalidTriggerAuthorityStoreError from error

    def records(self) -> tuple[TriggerAuthorityRecordV1, ...]:
        try:
            return tuple(
                TriggerAuthorityRecordV1.model_validate_json(read_private_text_query_only(path))
                for path in sorted(self._root.glob("*.json"))
            )
        except (InvalidPrivateQueryFileError, ValidationError) as error:
            raise InvalidTriggerAuthorityStoreError from error


class PersistedTriggerAuthorityResolver:
    def __init__(self, store: TriggerAuthorityStore) -> None:
        self._store = store

    def blocker(self, trigger: AutonomousTriggerV1, now: dt.datetime) -> str | None:
        candidates = tuple(
            record
            for record in self._store.records()
            if record.trigger_type == trigger.trigger_type
            and record.agent_family_id == trigger.agent_family_id
            and record.source_receipt_ids == trigger.source_receipt_ids
        )
        if not candidates:
            return "source_authority_missing"
        exact = tuple(
            record
            for record in candidates
            if record.authority == trigger.authority
            and record.evidence_refs == trigger.evidence_refs
            and record.payload_sha256 == trigger.payload_sha256
            and record.observed_at == trigger.observed_at
        )
        if len(exact) != 1:
            return "source_authority_invalid"
        record = exact[0]
        if now < record.authorized_at or now > record.expires_at:
            return "source_authority_stale"
        if trigger.authorized_at != record.authorized_at or trigger.expires_at != record.expires_at:
            return "source_authority_time_mismatch"
        return None


def authority_record_for(trigger: AutonomousTriggerV1) -> TriggerAuthorityRecordV1:
    return TriggerAuthorityRecordV1(
        authority_id=trigger.trigger_id,
        trigger_type=trigger.trigger_type,
        authority=trigger.authority,
        agent_family_id=trigger.agent_family_id,
        source_receipt_ids=trigger.source_receipt_ids,
        evidence_refs=trigger.evidence_refs,
        payload_sha256=trigger.payload_sha256,
        observed_at=trigger.observed_at,
        authorized_at=trigger.authorized_at,
        expires_at=trigger.expires_at,
    )


__all__ = (
    "PersistedTriggerAuthorityResolver",
    "TriggerAuthorityRecordV1",
    "TriggerAuthorityResolver",
    "TriggerAuthorityStore",
    "authority_record_for",
)
