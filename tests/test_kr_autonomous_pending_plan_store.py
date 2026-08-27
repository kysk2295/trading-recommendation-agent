from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from tests.test_kr_autonomous_trade_planner import _request
from trading_agent.kr_autonomous_pending_plan_models import KrAutonomousPendingPlan, pending_plan_id
from trading_agent.kr_autonomous_pending_plan_store import (
    InvalidKrAutonomousPendingPlanStoreError,
    KrAutonomousPendingPlanStore,
)
from trading_agent.kr_autonomous_trade_proposal import propose_kr_autonomous_trade


def _plan() -> KrAutonomousPendingPlan:
    request = _request()
    proposal, failure = propose_kr_autonomous_trade(request)
    assert proposal is not None and failure is None
    draft = KrAutonomousPendingPlan.model_construct(plan_id="", request=request, proposal=proposal)
    return KrAutonomousPendingPlan.model_validate(
        draft.model_copy(update={"plan_id": pending_plan_id(draft)}).model_dump(mode="python")
    )


def test_pending_store_appends_replays_and_preserves_private_modes(tmp_path: Path) -> None:
    plan = _plan()
    store = KrAutonomousPendingPlanStore(tmp_path / "private" / "pending.sqlite3")
    assert store.append(plan)
    assert not store.append(plan)
    assert store.plan(plan.plan_id) == plan
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_pending_store_rejects_divergent_proposal_and_plan_identity(tmp_path: Path) -> None:
    plan = _plan()
    store = KrAutonomousPendingPlanStore(tmp_path / "pending.sqlite3")
    assert store.append(plan)
    proposal = plan.proposal.model_copy(update={"quantity": plan.proposal.quantity - 1})
    forged = plan.model_copy(update={"proposal": proposal})
    with pytest.raises(InvalidKrAutonomousPendingPlanStoreError):
        _ = store.append(forged)
    with pytest.raises(InvalidKrAutonomousPendingPlanStoreError):
        _ = store.append(plan.model_copy(update={"plan_id": "f" * 64}))


def test_pending_store_rejects_schema_and_payload_tamper(tmp_path: Path) -> None:
    store = KrAutonomousPendingPlanStore(tmp_path / "pending.sqlite3")
    plan = _plan()
    assert store.append(plan)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER kr_autonomous_pending_plans_no_update")
        connection.execute("UPDATE kr_autonomous_pending_plans SET payload_json='{}'")
    with pytest.raises(InvalidKrAutonomousPendingPlanStoreError):
        _ = store.plan(plan.plan_id)


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "wrong_mode", "wrong_parent_mode"))
def test_pending_store_rejects_unsafe_paths(tmp_path: Path, kind: str) -> None:
    plan = _plan()
    path = tmp_path / "private" / "pending.sqlite3"
    store = KrAutonomousPendingPlanStore(path)
    if kind == "wrong_parent_mode":
        path.parent.mkdir()
        path.parent.chmod(0o755)
    elif kind == "symlink":
        path.parent.mkdir()
        target = tmp_path / "target.sqlite3"
        target.touch(mode=0o600)
        path.symlink_to(target)
    else:
        assert store.append(plan)
        if kind == "hardlink":
            os.link(path, tmp_path / "other.sqlite3")
        else:
            path.chmod(0o644)
    with pytest.raises(InvalidKrAutonomousPendingPlanStoreError):
        _ = store.append(plan)


def test_pending_store_detects_replacement_between_descriptor_check_and_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = KrAutonomousPendingPlanStore(tmp_path / "pending.sqlite3")
    replacement = tmp_path / "replacement.sqlite3"
    replacement.touch(mode=0o600)
    original_connect = sqlite3.connect

    def replace_then_connect(
        database: str | bytes | os.PathLike[str] | os.PathLike[bytes], *, uri: bool, timeout: float
    ) -> sqlite3.Connection:
        os.replace(replacement, store.path)
        return original_connect(database, uri=uri, timeout=timeout)

    monkeypatch.setattr("trading_agent.kr_autonomous_pending_plan_store.sqlite3.connect", replace_then_connect)
    with pytest.raises(InvalidKrAutonomousPendingPlanStoreError):
        _ = store.append(_plan())
