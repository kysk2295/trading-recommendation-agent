from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionPlan,
    dynamic_subscription_request_bytes,
    roll_alpaca_sip_dynamic_subscription_plan,
)
from trading_agent.us_subscription_policy_state import SubscriptionPolicyRuntimeState

_SCHEMA = """
CREATE TABLE dynamic_plan(
    generation INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL UNIQUE,
    evaluated_at TEXT NOT NULL,
    market_date TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_json BLOB NOT NULL
);
CREATE TRIGGER dynamic_plan_no_update BEFORE UPDATE ON dynamic_plan
BEGIN SELECT RAISE(ABORT, 'dynamic plan is append-only'); END;
CREATE TRIGGER dynamic_plan_no_delete BEFORE DELETE ON dynamic_plan
BEGIN SELECT RAISE(ABORT, 'dynamic plan is append-only'); END;
"""
_OBJECTS = {
    "dynamic_plan",
    "dynamic_plan_no_delete",
    "dynamic_plan_no_update",
}
type _PlanRow = tuple[str, str, str, str, bytes]


class AlpacaSipDynamicPlanStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic plan store is invalid"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicPlanRollResult:
    plan: AlpacaSipDynamicSubscriptionPlan
    appended: bool


class _PlanArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    plan: AlpacaSipDynamicSubscriptionPlan

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        _ = dynamic_subscription_request_bytes(self.plan)
        return self


class AlpacaSipDynamicPlanStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def roll(self, state: SubscriptionPolicyRuntimeState) -> AlpacaSipDynamicPlanRollResult:
        try:
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                prior = self._latest(connection)
                plan = roll_alpaca_sip_dynamic_subscription_plan(prior, state)
                if prior == plan:
                    connection.commit()
                    return AlpacaSipDynamicPlanRollResult(plan, False)
                payload = _artifact_bytes(plan)
                connection.execute(
                    "INSERT INTO dynamic_plan(plan_id,evaluated_at,market_date,payload_sha256,payload_json) "
                    "VALUES(?,?,?,?,?)",
                    (
                        plan.plan_id,
                        plan.evaluated_at.isoformat(),
                        plan.market_date.isoformat(),
                        hashlib.sha256(payload).hexdigest(),
                        payload,
                    ),
                )
                connection.commit()
                return AlpacaSipDynamicPlanRollResult(plan, True)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise AlpacaSipDynamicPlanStoreError from None

    def latest(self) -> AlpacaSipDynamicSubscriptionPlan | None:
        if not self.path.is_file():
            return None
        try:
            with closing(self._connection(write=False)) as connection:
                return self._latest(connection)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise AlpacaSipDynamicPlanStoreError from None

    def _latest(self, connection: sqlite3.Connection) -> AlpacaSipDynamicSubscriptionPlan | None:
        rows: list[_PlanRow] = connection.execute(
            "SELECT plan_id,evaluated_at,market_date,payload_sha256,payload_json FROM dynamic_plan ORDER BY generation"
        ).fetchall()
        plans = tuple(_plan_from_row(row) for row in rows)
        if any(current.evaluated_at <= prior.evaluated_at for prior, current in pairwise(plans)):
            raise AlpacaSipDynamicPlanStoreError
        if not plans:
            return None
        return plans[-1]

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise AlpacaSipDynamicPlanStoreError
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existed = self.path.exists()
            if existed:
                _require_private_file(self.path)
            connection = sqlite3.connect(self.path)
            if not existed:
                os.chmod(self.path, 0o600)
            _require_private_file(self.path)
            if connection.execute("PRAGMA user_version").fetchone() == (0,):
                connection.executescript(_SCHEMA)
                connection.execute("PRAGMA user_version=1")
                connection.commit()
        else:
            _require_private_file(self.path)
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'"
            )
        }
        if connection.execute("PRAGMA user_version").fetchone() != (1,) or objects != _OBJECTS:
            connection.close()
            raise AlpacaSipDynamicPlanStoreError
        return connection


def _artifact_bytes(plan: AlpacaSipDynamicSubscriptionPlan) -> bytes:
    return _PlanArtifact(plan=plan).model_dump_json().encode("ascii")


def _plan_from_row(row: _PlanRow) -> AlpacaSipDynamicSubscriptionPlan:
    plan_id, evaluated_at, market_date, payload_sha256, payload = row
    if hashlib.sha256(payload).hexdigest() != payload_sha256:
        raise AlpacaSipDynamicPlanStoreError
    artifact = _PlanArtifact.model_validate_json(payload)
    plan = artifact.plan
    if (
        plan.plan_id != plan_id
        or plan.evaluated_at.isoformat() != evaluated_at
        or plan.market_date.isoformat() != market_date
        or _artifact_bytes(plan) != payload
    ):
        raise AlpacaSipDynamicPlanStoreError
    return plan


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AlpacaSipDynamicPlanStoreError


__all__ = (
    "AlpacaSipDynamicPlanRollResult",
    "AlpacaSipDynamicPlanStore",
    "AlpacaSipDynamicPlanStoreError",
)
