from __future__ import annotations

import os
from pathlib import Path

import pytest

from trading_agent.dashboard_hermes_sessions import (
    HermesSessionBindingStore,
    InvalidHermesSessionBindingError,
)


def test_family_binding_persists_across_store_restart_and_is_isolated(tmp_path: Path) -> None:
    # Given: one private binding root and two canonical families
    root = tmp_path / "bindings"
    first = HermesSessionBindingStore(root)

    # When: each family captures its first Hermes session and the store restarts
    first.capture("opportunity_manager", "session-opportunity-001")
    first.capture("day_trading", "session-day-trading-001")
    restarted = HermesSessionBindingStore(root)

    # Then: exact family-local sessions persist without aliasing
    assert restarted.session_for("opportunity_manager") == "session-opportunity-001"
    assert restarted.session_for("day_trading") == "session-day-trading-001"
    assert (root / "opportunity_manager.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("unsafe_kind", ["mode", "symlink", "hardlink"])
def test_unsafe_binding_fails_closed_without_replacement(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    # Given: a valid binding replaced by an unsafe filesystem identity
    root = tmp_path / "bindings"
    store = HermesSessionBindingStore(root)
    store.capture("market_context", "session-market-context-001")
    binding = root / "market_context.json"
    original = binding.read_bytes()
    binding.unlink()
    target = tmp_path / "unsafe-binding"
    target.write_bytes(original)
    target.chmod(0o600)
    match unsafe_kind:
        case "mode":
            binding.write_bytes(original)
            binding.chmod(0o644)
        case "symlink":
            binding.symlink_to(target)
        case "hardlink":
            os.link(target, binding)
        case unexpected:
            raise AssertionError(unexpected)

    # When / Then: reading fails closed and does not mint a replacement session
    with pytest.raises(InvalidHermesSessionBindingError):
        store.session_for("market_context")
    assert binding.exists()


def test_local_reset_removes_only_the_selected_family(tmp_path: Path) -> None:
    # Given: two persisted family bindings
    store = HermesSessionBindingStore(tmp_path / "bindings")
    store.capture("swing_trading", "session-swing-001")
    store.capture("systematic_quant", "session-systematic-001")

    # When: the operator performs a local-only reset
    store.reset("swing_trading")

    # Then: only that family loses its resumable binding
    assert store.session_for("swing_trading") is None
    assert store.session_for("systematic_quant") == "session-systematic-001"
