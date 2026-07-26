from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.dashboard_agent_admission import (
    AdmissionDecision,
    AutonomousPolicy,
    ReplayState,
    policy_blocker,
)
from trading_agent.dashboard_agent_receipts import build_receipt, task_id_for
from trading_agent.dashboard_autonomous_research import (
    AutonomousTaskReceiptV1,
    AutonomousTriggerV1,
    TaskState,
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


def _replay_state(state: TaskState) -> ReplayState:
    match state:
        case "completed" | "failed" | "uncertain" | "blocked":
            return state
        case "claimed" | "running" | "duplicate":
            return "uncertain"
        case _:
            assert_never(state)


class InvalidAutonomousTaskStoreError(RuntimeError):
    pass


class AutonomousTaskStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._claims = root / "claims"
        self._receipts = root / "receipts"
        self._admission_lock = root / "admission.lock"
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
        with self._locked():
            return self._claim_unlocked(trigger)

    def admit(
        self,
        trigger: AutonomousTriggerV1,
        now: dt.datetime,
        policy: AutonomousPolicy,
    ) -> AdmissionDecision:
        task_id = task_id_for(trigger)
        with self._locked():
            receipts = self._receipts_unlocked()
            prior = tuple(item for item in receipts if item.public_task_id == task_id)
            if prior:
                latest = max(prior, key=lambda item: item.sequence)
                replay_state: ReplayState = _replay_state(latest.state)
                return AdmissionDecision("duplicate", task_id, latest.reason or "duplicate_claim", None, replay_state)
            blocker = policy_blocker(trigger, now, policy, receipts)
            if blocker is not None:
                receipt = build_receipt(
                    trigger,
                    task_id,
                    0,
                    "blocker",
                    "blocked",
                    trigger.authorized_at,
                    reason=blocker,
                )
                _ = self._append_unlocked(receipt)
                return AdmissionDecision("blocked", task_id, blocker, receipt, "blocked")
            _, created = self._claim_unlocked(trigger)
            if not created:
                return AdmissionDecision("duplicate", task_id, "duplicate_claim", None, "uncertain")
            receipt = build_receipt(
                trigger,
                task_id,
                0,
                "claim",
                "claimed",
                now,
                consumed_tokens=trigger.budget_envelope.max_tokens,
                consumed_cost=trigger.budget_envelope.max_cost_microusd,
            )
            _ = self._append_unlocked(receipt)
            return AdmissionDecision("admitted", task_id, None, receipt, None)

    def reject(
        self,
        trigger: AutonomousTriggerV1,
        reason: str,
    ) -> AdmissionDecision:
        task_id = task_id_for(trigger)
        with self._locked():
            prior = tuple(item for item in self._receipts_unlocked() if item.public_task_id == task_id)
            if prior:
                latest = max(prior, key=lambda item: item.sequence)
                replay_state: ReplayState = _replay_state(latest.state)
                return AdmissionDecision("duplicate", task_id, reason, None, replay_state)
            receipt = build_receipt(
                trigger,
                task_id,
                0,
                "blocker",
                "blocked",
                trigger.authorized_at,
                reason=reason,
            )
            _ = self._append_unlocked(receipt)
            return AdmissionDecision("blocked", task_id, reason, receipt, "blocked")

    def append(self, receipt: AutonomousTaskReceiptV1) -> bool:
        with self._locked():
            return self._append_unlocked(receipt)

    def _append_unlocked(self, receipt: AutonomousTaskReceiptV1) -> bool:
        path = self._receipts / (
            f"{receipt.public_task_id}.{receipt.sequence:04d}.{receipt.kind}.{receipt.event_id}.json"
        )
        try:
            created = publish_private_immutable_text(path, receipt.model_dump_json())
        except InvalidPrivateImmutableFileError as error:
            raise InvalidAutonomousTaskStoreError from error
        return created

    def receipts(self) -> tuple[AutonomousTaskReceiptV1, ...]:
        with self._locked():
            return self._receipts_unlocked()

    def _receipts_unlocked(self) -> tuple[AutonomousTaskReceiptV1, ...]:
        try:
            return tuple(
                AutonomousTaskReceiptV1.model_validate_json(read_private_text_query_only(path))
                for path in sorted(self._receipts.glob("*.json"))
            )
        except (InvalidPrivateQueryFileError, ValidationError) as error:
            raise InvalidAutonomousTaskStoreError from error

    def _claim_unlocked(self, trigger: AutonomousTriggerV1) -> tuple[str, bool]:
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

    @contextmanager
    def _locked(self) -> Iterator[None]:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self._admission_lock, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = ("AutonomousTaskStore", "InvalidAutonomousTaskStoreError")
