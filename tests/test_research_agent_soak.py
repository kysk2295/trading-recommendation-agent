from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

import trading_agent.research_agent_soak_sqlite as soak_sqlite
from trading_agent.research_agent_soak_models import (
    SoakCheckpoint,
    SoakCheckpointKind,
    SoakCheckpointPayload,
    SoakEvidenceMode,
    SoakObservation,
    SoakState,
    canonical_payload_json,
)
from trading_agent.research_agent_soak_status import build_research_agent_soak_status
from trading_agent.research_agent_soak_store import (
    InvalidResearchAgentSoakStoreError,
    ResearchAgentSoakStore,
)

_START = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)


def test_controlled_fixture_cannot_complete_actual_soak_after_24_hours(tmp_path: Path) -> None:
    # Given: controlled evidence with every mechanical checkpoint across 25 hours.
    store = _prepared_store(tmp_path)
    _append_required_mechanics(store, evidence_boot="b" * 64)

    # When: status is projected after the nominal duration.
    status = build_research_agent_soak_status(store.records(), _START + dt.timedelta(hours=25))

    # Then: fixture mechanics remain explicitly ineligible for actual completion.
    assert status.status is SoakState.COLLECTING
    assert status.actual_restart_observed is False
    assert status.actual_reboot_observed is False
    assert status.actual_provider_outage_observed is False
    assert "controlled_fixture_ineligible_for_actual_completion" in status.blockers


def test_incomplete_soak_expires_after_72_hours(tmp_path: Path) -> None:
    # Given: a controlled soak containing only its genesis checkpoint.
    store = _prepared_store(tmp_path)

    # When: status is projected beyond the bounded collection window.
    status = build_research_agent_soak_status(store.records(), _START + dt.timedelta(hours=73))

    # Then: it expires and continues to expose every missing actual requirement.
    assert status.status is SoakState.EXPIRED
    assert status.blockers == (
        "actual_collection_window_exceeded",
        "controlled_fixture_ineligible_for_actual_completion",
        "actual_24_hours_missing",
        "actual_process_restart_missing",
        "actual_system_reboot_missing",
        "actual_provider_outage_missing",
    )


def test_actual_mode_with_all_mechanics_still_expires_beyond_72_hours(tmp_path: Path) -> None:
    # Given: an actual-mode unit fixture containing every ordered mechanical requirement.
    store = _prepared_store(tmp_path)
    _append_required_mechanics(store, evidence_boot="b" * 64)
    records = _as_actual_mode(store.records())

    # When: status is projected beyond the maximum collection window.
    status = build_research_agent_soak_status(records, _START + dt.timedelta(hours=73))

    # Then: the window boundary dominates otherwise complete mechanics.
    assert status.status is SoakState.EXPIRED
    assert status.blockers == ("actual_collection_window_exceeded",)


@pytest.mark.parametrize(
    ("hours", "expected"),
    [(23, SoakState.COLLECTING), (25, SoakState.COMPLETE)],
)
def test_actual_mode_completion_respects_24_hour_boundary(tmp_path: Path, hours: int, expected: SoakState) -> None:
    # Given: actual-mode unit mechanics completed within the 72-hour window.
    store = _prepared_store(tmp_path)
    _append_required_mechanics(store, evidence_boot="b" * 64)
    records = _as_actual_mode(store.records())

    # When: status is projected on one side of the 24-hour boundary.
    status = build_research_agent_soak_status(records, _START + dt.timedelta(hours=hours))

    # Then: completion occurs only at or after 24 hours.
    assert status.status is expected


def test_exact_schema_rejects_replaced_append_only_trigger(tmp_path: Path) -> None:
    # Given: a valid store whose update trigger is replaced by a same-named no-op trigger.
    store = _prepared_store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.executescript(
            "DROP TRIGGER checkpoints_no_update;"
            "CREATE TRIGGER checkpoints_no_update BEFORE UPDATE ON checkpoints BEGIN SELECT 1; END;"
        )

    # When: the store is read through its verified surface.
    action = store.records

    # Then: matching object names do not disguise the wrong schema.
    with pytest.raises(InvalidResearchAgentSoakStoreError):
        action()


def test_hash_chain_rejects_tampered_payload_without_partial_records(tmp_path: Path) -> None:
    # Given: a valid two-record chain with triggers maliciously removed before payload tampering.
    store = _prepared_store(tmp_path)
    _ = store.append_controlled(SoakCheckpointKind.HEARTBEAT, _observation(60, "a" * 64, "2" * 64))
    with sqlite3.connect(store.path) as connection:
        connection.executescript(
            "DROP TRIGGER checkpoints_no_update;"
            "UPDATE checkpoints SET payload_json='{}' WHERE sequence=2;"
            "CREATE TRIGGER checkpoints_no_update BEFORE UPDATE ON checkpoints "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
        )

    # When: the full chain is requested.
    action = store.records

    # Then: parsing fails closed rather than returning the valid prefix.
    with pytest.raises(InvalidResearchAgentSoakStoreError):
        action()


def test_hardlinked_store_is_rejected(tmp_path: Path) -> None:
    # Given: a valid store with a second hard link.
    store = _prepared_store(tmp_path)
    os.link(store.path, store.path.with_suffix(".linked"))

    # When: the store is read.
    action = store.records

    # Then: link-count ambiguity fails closed.
    with pytest.raises(InvalidResearchAgentSoakStoreError):
        action()


def test_controlled_observation_cannot_be_appended_to_actual_store(tmp_path: Path) -> None:
    # Given: an actual store whose genesis identity was gathered internally.
    store = ResearchAgentSoakStore(tmp_path / "private" / "soak.sqlite3")
    _ = store.prepare_actual()

    # When: caller-controlled time and identity are offered to that store.
    def action() -> None:
        _ = store.append_controlled(SoakCheckpointKind.HEARTBEAT, _observation(60, "a" * 64, "2" * 64))

    # Then: the controlled seam cannot contribute to actual evidence.
    with pytest.raises(InvalidResearchAgentSoakStoreError):
        action()


def test_canonical_chain_rejects_chronologically_reordered_checkpoint(tmp_path: Path) -> None:
    # Given: a valid checkpoint whose canonical payload and hash are rewritten to predate genesis.
    store = _prepared_store(tmp_path)
    _ = store.append_controlled(SoakCheckpointKind.HEARTBEAT, _observation(60, "a" * 64, "2" * 64))
    with sqlite3.connect(store.path) as connection:
        raw: tuple[str] | None = connection.execute("SELECT payload_json FROM checkpoints WHERE sequence=2").fetchone()
        assert raw is not None
        payload = SoakCheckpointPayload.model_validate_json(raw[0], strict=True).model_copy(
            update={"recorded_at": _START - dt.timedelta(seconds=1)}
        )
        canonical = canonical_payload_json(payload)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        connection.executescript("DROP TRIGGER checkpoints_no_update;")
        _ = connection.execute(
            "UPDATE checkpoints SET payload_json=?,checkpoint_sha256=? WHERE sequence=2", (canonical, digest)
        )
        connection.executescript(
            "CREATE TRIGGER checkpoints_no_update BEFORE UPDATE ON checkpoints "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
        )

    # When: the cryptographically self-consistent but reordered chain is read.
    action = store.records

    # Then: chronological ordering is also part of chain integrity.
    with pytest.raises(InvalidResearchAgentSoakStoreError):
        action()


def test_provider_outage_recovery_requires_prior_outage_observation(tmp_path: Path) -> None:
    # Given: a controlled store without an outage-observed checkpoint.
    store = _prepared_store(tmp_path)

    # When: a lone recovery claim is appended.
    def action() -> None:
        _ = store.append_controlled(SoakCheckpointKind.PROVIDER_OUTAGE_RECOVERED, _observation(60, "a" * 64, "2" * 64))

    # Then: the claim is rejected and the genesis remains the complete chain.
    with pytest.raises(InvalidResearchAgentSoakStoreError):
        action()
    assert len(store.records()) == 1


def test_prepare_does_not_unlink_attacker_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a connector swap that replaces the newly created pathname after its inode is held.
    store = ResearchAgentSoakStore(tmp_path / "private" / "soak.sqlite3")
    original_connect = soak_sqlite.sqlite3.connect
    attacker = b"attacker replacement"

    def replacing_connect(database: Path, timeout: float) -> sqlite3.Connection:
        os.replace(store.path, store.path.with_suffix(".held"))
        store.path.write_bytes(attacker)
        store.path.chmod(0o600)
        return original_connect(database, timeout=timeout)

    monkeypatch.setattr(soak_sqlite.sqlite3, "connect", replacing_connect)

    # When: actual preparation detects the identity change.
    action = store.prepare_actual

    # Then: preparation fails closed and cleanup preserves the different attacker inode.
    with pytest.raises(InvalidResearchAgentSoakStoreError):
        action()
    assert store.path.read_bytes() == attacker


def test_read_rejects_path_replacement_during_connect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a valid store whose pathname is replaced inside the SQLite connect boundary.
    store = _prepared_store(tmp_path)
    original_connect = soak_sqlite.sqlite3.connect

    def replacing_connect(database: Path, timeout: float) -> sqlite3.Connection:
        os.replace(store.path, store.path.with_suffix(".held"))
        store.path.write_bytes(b"replacement")
        store.path.chmod(0o600)
        return original_connect(database, timeout=timeout)

    monkeypatch.setattr(soak_sqlite.sqlite3, "connect", replacing_connect)

    # When: records are requested through the replaced path.
    action = store.records

    # Then: the held descriptor mismatch rejects the read without returning a prefix.
    with pytest.raises(InvalidResearchAgentSoakStoreError):
        action()


def test_append_rejects_path_replacement_during_connect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a valid store whose pathname is replaced at the write-connect boundary.
    store = _prepared_store(tmp_path)
    original_connect = soak_sqlite.sqlite3.connect

    def replacing_connect(database: Path, timeout: float) -> sqlite3.Connection:
        os.replace(store.path, store.path.with_suffix(".held"))
        store.path.write_bytes(b"replacement")
        store.path.chmod(0o600)
        return original_connect(database, timeout=timeout)

    monkeypatch.setattr(soak_sqlite.sqlite3, "connect", replacing_connect)

    # When: a controlled checkpoint is appended through the replaced path.
    def action() -> None:
        _ = store.append_controlled(SoakCheckpointKind.HEARTBEAT, _observation(60, "a" * 64, "2" * 64))

    # Then: append fails before SQL can target the replacement.
    with pytest.raises(InvalidResearchAgentSoakStoreError):
        action()


def _prepared_store(tmp_path: Path) -> ResearchAgentSoakStore:
    store = ResearchAgentSoakStore(tmp_path / "private" / "soak.sqlite3")
    _ = store.prepare_controlled(_observation(0, "a" * 64, "1" * 64))
    return store


def _append_required_mechanics(store: ResearchAgentSoakStore, evidence_boot: str) -> None:
    _ = store.append_controlled(SoakCheckpointKind.PROCESS_RESTART, _observation(60, "a" * 64, "2" * 64))
    _ = store.append_controlled(SoakCheckpointKind.REBOOT_RECOVERED, _observation(120, evidence_boot, "3" * 64))
    _ = store.append_controlled(SoakCheckpointKind.PROVIDER_OUTAGE_OBSERVED, _observation(180, evidence_boot, "4" * 64))
    _ = store.append_controlled(
        SoakCheckpointKind.PROVIDER_OUTAGE_RECOVERED, _observation(240, evidence_boot, "5" * 64)
    )


def _observation(offset_seconds: int, boot: str, invocation: str) -> SoakObservation:
    return SoakObservation(
        recorded_at=_START + dt.timedelta(seconds=offset_seconds),
        monotonic_ns=offset_seconds * 1_000_000_000,
        boot_sha256=hashlib.sha256(boot.encode()).hexdigest(),
        invocation_sha256=hashlib.sha256(invocation.encode()).hexdigest(),
    )


def _as_actual_mode(records: tuple[SoakCheckpoint, ...]) -> tuple[SoakCheckpoint, ...]:
    converted: list[SoakCheckpoint] = []
    previous = "0" * 64
    for record in records:
        payload = record.payload.model_copy(
            update={"evidence_mode": SoakEvidenceMode.ACTUAL, "previous_sha256": previous}
        )
        canonical = canonical_payload_json(payload)
        checkpoint = SoakCheckpoint(payload=payload, checkpoint_sha256=hashlib.sha256(canonical.encode()).hexdigest())
        converted.append(checkpoint)
        previous = checkpoint.checkpoint_sha256
    return tuple(converted)
