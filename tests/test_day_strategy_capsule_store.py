from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.test_day_research_attempt_binding import (
    SHA_A,
    _attempt,
    _binding,
    _family,
    _manifest,
    _version,
)
from tests.test_day_strategy_capsule import _builtin_capsule
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.strategy_research_types import AttemptStatus


def _prepared_store(
    path: Path,
    status: AttemptStatus = AttemptStatus.SUCCEEDED,
) -> tuple[ExperimentLedgerStore, StrategyCapsule]:
    store = ExperimentLedgerStore(path)
    family = _family()
    version = _version(family)
    attempt = _attempt(0, status)
    binding = _binding(attempt, version)
    base = _builtin_capsule()
    payload = base.model_dump(mode="python") | {
        "capsule_id": "",
        "hypothesis_version_id": version.hypothesis_version_id,
        "attempt_binding_id": binding.binding_id,
        "market_id": version.market_id,
        "artifact_ref": binding.artifact_ref,
        "artifact_sha256": SHA_A,
        "evaluation_cadence": version.evaluation_cadence,
        "entry_rule": version.entry_rule,
        "exit_rule": version.exit_rule,
        "stop_rule": version.stop_rule,
        "cost_model": version.cost_model,
        "protocol_sha256": version.protocol_sha256,
        "published_at": binding.bound_at.replace(minute=binding.bound_at.minute + 1),
    }
    capsule = StrategyCapsule.model_validate(payload | {"capsule_id": StrategyCapsule.canonical_id_for(payload)})
    with store.writer() as writer:
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(version)
        assert writer.append_strategy_research_attempt(attempt)
        assert writer.register_day_research_attempt_binding(binding)
    return store, capsule


def test_verified_capsule_publication_is_idempotent_and_queryable(tmp_path: Path) -> None:
    # Given: a successful same-market attempt binding and exact artifact declaration.
    store, capsule = _prepared_store(tmp_path / "ledger.sqlite3")

    # When: the capsule is published and replayed.
    with store.writer() as writer:
        assert writer.register_day_strategy_capsule(capsule) is True
        assert writer.register_day_strategy_capsule(capsule) is False

    # Then: deterministic readers expose one validated immutable capsule.
    stored = store.reader().day_strategy_capsule(capsule.capsule_id)
    assert stored is not None and stored.capsule == capsule
    assert tuple(item.capsule for item in store.day_strategy_capsules(capsule.market_id)) == (capsule,)


def test_capsule_requires_successful_exact_attempt_binding(tmp_path: Path) -> None:
    # Given: a terminal but failed attempt binding.
    store, capsule = _prepared_store(tmp_path / "ledger.sqlite3", AttemptStatus.FAILED)

    # When/Then: publication fails closed and leaves no capsule row.
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_strategy_capsule(capsule)
    assert store.day_strategy_capsules() == ()


def test_changed_payload_under_same_capsule_id_conflicts(tmp_path: Path) -> None:
    # Given: one stored capsule and a stale-ID mutation.
    store, capsule = _prepared_store(tmp_path / "ledger.sqlite3")
    with store.writer() as writer:
        assert writer.register_day_strategy_capsule(capsule)
    conflicting = capsule.model_copy()
    object.__setattr__(conflicting, "target_rule", "changed_target_rule")

    # When/Then: identity reuse with changed content maps to the ledger conflict error.
    with pytest.raises(ExperimentLedgerConflictError), store.writer() as writer:
        _ = writer.register_day_strategy_capsule(conflicting)


def test_reader_rejects_tampered_index_and_noncanonical_payload(tmp_path: Path) -> None:
    # Given: a stored capsule whose append-only trigger is deliberately removed for corruption simulation.
    store, capsule = _prepared_store(tmp_path / "ledger.sqlite3")
    with store.writer() as writer:
        assert writer.register_day_strategy_capsule(capsule)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER day_strategy_capsules_no_update")
        connection.execute(
            "UPDATE day_strategy_capsules SET market_id=?,payload_json=? WHERE capsule_id=?",
            (
                "kr_equities",
                canonical_experiment_ledger_json(capsule) + " ",
                capsule.capsule_id,
            ),
        )

    # When/Then: the read boundary validates both canonical payload and indexed columns.
    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = store.day_strategy_capsules()


def test_capsule_rejects_version_declaration_mismatch(tmp_path: Path) -> None:
    # Given: a capsule whose cadence differs from its same-market hypothesis version.
    store, capsule = _prepared_store(tmp_path / "ledger.sqlite3")
    payload = capsule.model_dump(mode="python") | {
        "capsule_id": "",
        "evaluation_cadence": "session_close_only",
    }
    mismatched = StrategyCapsule.model_validate(
        payload | {"capsule_id": StrategyCapsule.canonical_id_for(payload)}
    )

    # When/Then: parent declaration coherence fails before insertion.
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_strategy_capsule(mismatched)
    assert store.day_strategy_capsules() == ()


def test_missing_store_is_empty_and_reader_connection_is_query_only(tmp_path: Path) -> None:
    # Given: an uninitialized ledger path.
    store = ExperimentLedgerStore(tmp_path / "missing.sqlite3")

    # When/Then: deterministic queries are empty and an initialized reader cannot mutate.
    assert store.day_strategy_capsules() == ()
    assert store.day_strategy_capsule("a" * 64) is None
    initialized, _ = _prepared_store(tmp_path / "initialized.sqlite3")
    with initialized.reader()._reader_connection() as connection, pytest.raises(
        sqlite3.OperationalError,
        match="readonly",
    ):
        connection.execute("INSERT INTO day_strategy_capsules VALUES ('x','x','us_equities','x','x')")
