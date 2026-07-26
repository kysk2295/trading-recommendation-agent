from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pydantic import ValidationError

from trading_agent.dashboard_autonomous_research import (
    AutonomousTaskReceiptV1,
    AutonomousTriggerV1,
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
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)


class InvalidAutonomousTaskStoreError(RuntimeError):
    pass


class AutonomousTaskStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._claims = root / "claims"
        self._receipts = root / "receipts"
        try:
            for path in (root, self._claims, self._receipts):
                descriptor = open_private_parent(path, create=True)
                try:
                    require_private_directory(descriptor)
                finally:
                    os.close(descriptor)
        except (InvalidPrivateDirectoryIdentityError, OSError) as error:
            raise InvalidAutonomousTaskStoreError from error

    @property
    def receipts_root(self) -> Path:
        return self._receipts

    def claim(self, trigger: AutonomousTriggerV1) -> tuple[str, bool]:
        claim_key = f"{trigger.agent_family_id}:{trigger.policy_version}:{trigger.dedupe_key}"
        digest = hashlib.sha256(claim_key.encode()).hexdigest()
        task_id = digest[:32]
        payload = json.dumps(
            {
                "agent_family_id": trigger.agent_family_id,
                "claim_sha256": digest,
                "policy_version": trigger.policy_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            created = publish_private_immutable_text(self._claims / f"{digest}.json", payload)
        except InvalidPrivateImmutableFileError as error:
            raise InvalidAutonomousTaskStoreError from error
        return task_id, created

    def append(self, receipt: AutonomousTaskReceiptV1) -> bool:
        path = self._receipts / (
            f"{receipt.public_task_id}.{receipt.sequence:04d}.{receipt.kind}.{receipt.event_id}.json"
        )
        try:
            created = publish_private_immutable_text(path, receipt.model_dump_json())
        except InvalidPrivateImmutableFileError as error:
            raise InvalidAutonomousTaskStoreError from error
        return created

    def receipts(self) -> tuple[AutonomousTaskReceiptV1, ...]:
        try:
            return tuple(
                AutonomousTaskReceiptV1.model_validate_json(read_private_text_query_only(path))
                for path in sorted(self._receipts.glob("*.json"))
            )
        except (InvalidPrivateQueryFileError, ValidationError) as error:
            raise InvalidAutonomousTaskStoreError from error


__all__ = ("AutonomousTaskStore", "InvalidAutonomousTaskStoreError")
