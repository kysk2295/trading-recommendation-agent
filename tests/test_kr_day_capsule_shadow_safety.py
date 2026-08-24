from __future__ import annotations

import ast
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from tests.test_kr_day_capsule_shadow import _entry_evaluation, _rebuild
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowStatus
from trading_agent.kr_day_capsule_shadow_service import run_kr_day_capsule_shadow_tick
from trading_agent.kr_day_capsule_shadow_store import (
    InvalidKrDayCapsuleShadowStoreError,
    KrDayCapsuleShadowStore,
)


def test_forged_capsule_lineage_blocks_without_cursor_advance(tmp_path: Path) -> None:
    valid = _entry_evaluation()
    forged = _rebuild(valid, capsule_id="f" * 64)
    store = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")

    result = run_kr_day_capsule_shadow_tick(store, (forged,)).results[0].event

    assert forged.setup_input.producer_strategy_version != forged.capsule_id
    assert result.status is KrDayCapsuleShadowStatus.BLOCKED
    assert result.accepted_bar_cursor is None


def test_existing_unsafe_parent_is_rejected_without_mutation(tmp_path: Path) -> None:
    parent = tmp_path / "unsafe"
    parent.mkdir(mode=0o755)
    os.chmod(parent, 0o755)
    store = KrDayCapsuleShadowStore(parent / "shadow.sqlite3")
    before = stat.S_IMODE(parent.stat().st_mode)

    with pytest.raises(InvalidKrDayCapsuleShadowStoreError):
        _ = run_kr_day_capsule_shadow_tick(store, (_entry_evaluation(),))

    assert before == 0o755
    assert stat.S_IMODE(parent.stat().st_mode) == before
    assert not store.path.exists()


def test_missing_parent_is_created_private(tmp_path: Path) -> None:
    parent = tmp_path / "missing"
    store = KrDayCapsuleShadowStore(parent / "shadow.sqlite3")

    result = run_kr_day_capsule_shadow_tick(store, (_entry_evaluation(),)).results[0]

    assert result.created is True
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert store.path.exists()


def test_store_rejects_unsafe_metadata_and_is_structurally_append_only(tmp_path: Path) -> None:
    store = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    _ = run_kr_day_capsule_shadow_tick(store, (_entry_evaluation(),))
    os.chmod(store.path, 0o644)

    with pytest.raises(InvalidKrDayCapsuleShadowStoreError):
        _ = store.events()
    os.chmod(store.path, 0o600)
    with sqlite3.connect(store.path) as connection, pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute("UPDATE kr_day_capsule_shadow_events SET capsule_id='unsafe'")


def test_import_closure_has_no_execution_or_provider_mutation_authority() -> None:
    project = Path(__file__).resolve().parents[1]
    modules = tuple(project.glob("trading_agent/kr_day_capsule_shadow_*.py"))

    imports = {
        alias.name
        for module in modules
        for node in ast.walk(ast.parse(module.read_text()))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert len(modules) == 4
    assert not any(
        token in name.lower()
        for name in imports
        for token in ("alpaca", "paper", "broker", "order", "account", "balance", "position")
    )
