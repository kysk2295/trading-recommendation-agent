from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.strategy_research_contract_fixtures import NOW, SHA_A, SHA_B, hypothesis
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
)
from trading_agent.strategy_research_ledger import (
    AgentResearchStateEvent,
    ExactHoldoutMetric,
    HoldoutReveal,
    StrategyResearchLedgerError,
)
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_results import ResearchAttempt, TerminalResearchResult
from trading_agent.strategy_research_types import (
    AttemptStatus,
    SafeTerminalReason,
    TerminalOutcome,
)


def _manifest() -> PreregistrationManifest:
    return PreregistrationManifest.from_hypothesis(
        hypothesis(),
        preregistered_at=NOW + dt.timedelta(minutes=1),
    )


def _attempt(branch: int, status: AttemptStatus) -> ResearchAttempt:
    terminal = status is not AttemptStatus.STARTED
    successful = status is AttemptStatus.SUCCEEDED
    return ResearchAttempt(
        attempt_id=f"attempt-{branch}",
        hypothesis_id=_manifest().hypothesis.hypothesis_id,
        branch_index=branch,
        input_hashes=(SHA_A,),
        code_sha256=SHA_A,
        data_manifest_sha256=SHA_B,
        started_at=NOW + dt.timedelta(minutes=2 + branch),
        finished_at=NOW + dt.timedelta(minutes=3 + branch) if terminal else None,
        status=status,
        artifact_refs=(f"artifact://safe/{SHA_A}",) if successful else (),
        error_class=None if successful or not terminal else f"{status.value}_error",
        max_cpu_seconds=60,
    )


def _result() -> TerminalResearchResult:
    return TerminalResearchResult(
        result_id="terminal-result-1",
        hypothesis_id=_manifest().hypothesis.hypothesis_id,
        owner_agent_id=_manifest().hypothesis.agent_id,
        outcome=TerminalOutcome.SUPPORTED,
        reason_codes=(SafeTerminalReason.CI_WIDTH_TOO_WIDE,),
        artifact_refs=(f"artifact://safe/{SHA_A}",),
        evaluated_at=NOW + dt.timedelta(hours=1),
    )


def _reveal() -> HoldoutReveal:
    sealed = _manifest().hypothesis.holdout_period_sealed_ref
    return HoldoutReveal(
        reveal_id="holdout-reveal-1",
        hypothesis_id=_manifest().hypothesis.hypothesis_id,
        seal_id=sealed.seal_id,
        commitment_sha256=sealed.commitment_sha256,
        reviewer_id="independent-reviewer-v1",
        exact_metrics=(ExactHoldoutMetric(name="exact_sharpe", value=0.731, lower=0.201, upper=1.044),),
        sanitized_result=_result(),
        revealed_at=NOW + dt.timedelta(hours=1),
    )


def _state_event() -> AgentResearchStateEvent:
    return AgentResearchStateEvent(
        event_id="recovery-event-1",
        agent_id=_manifest().hypothesis.agent_id,
        sequence=1,
        last_event_id="evidence-event-9",
        last_available_at=NOW,
        version=3,
        hypothesis_id=_manifest().hypothesis.hypothesis_id,
        attempt_id="attempt-interrupted",
        state="recovery_pending",
        lease_until=NOW + dt.timedelta(minutes=5),
        checkpoint_sha256=SHA_A,
        retry_count=2,
        next_retry_at=NOW + dt.timedelta(minutes=6),
        reason="repeated_interruption",
    )


def test_register_preregistration_is_byte_identical_replay_only(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    manifest = _manifest()

    # When
    with store.writer() as writer:
        first = writer.register_strategy_research(manifest)
        replay = writer.register_strategy_research(manifest)

    # Then
    assert (first, replay) == (True, False)
    assert ExperimentLedgerReader(store.path).strategy_research_preregistrations() == (manifest,)


def test_protocol_mutation_for_registered_hypothesis_conflicts(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    manifest = _manifest()
    changed_hypothesis = manifest.hypothesis.model_copy(update={"primary_metric": "mutated metric"})
    changed = PreregistrationManifest.from_hypothesis(
        changed_hypothesis,
        preregistered_at=manifest.preregistered_at,
    )
    with store.writer() as writer:
        _ = writer.register_strategy_research(manifest)

    # When / Then
    with pytest.raises(StrategyResearchLedgerError, match="preregistration_conflict"), store.writer() as writer:
        _ = writer.register_strategy_research(changed)


def test_every_attempt_status_survives_restart(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    statuses = tuple(AttemptStatus)
    with ExperimentLedgerStore(database).writer() as writer:
        _ = writer.register_strategy_research(_manifest())
        for branch, status in enumerate(statuses):
            assert writer.append_strategy_research_attempt(_attempt(branch, status)) is True

    # When
    attempts = ExperimentLedgerReader(database).strategy_research_attempts(_manifest().hypothesis.hypothesis_id)

    # Then
    assert tuple(attempt.status for attempt in attempts) == statuses


def test_attempt_replay_is_idempotent_and_different_payload_conflicts(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    attempt = _attempt(0, AttemptStatus.SUCCEEDED)
    conflicting = attempt.model_copy(update={"artifact_refs": (f"artifact://safe/{SHA_B}",)})

    # When
    with store.writer() as writer:
        _ = writer.register_strategy_research(_manifest())
        first = writer.append_strategy_research_attempt(attempt)
        replay = writer.append_strategy_research_attempt(attempt)

    # Then
    assert (first, replay) == (True, False)
    with pytest.raises(StrategyResearchLedgerError, match="attempt_conflict"), store.writer() as writer:
        _ = writer.append_strategy_research_attempt(conflicting)


def test_attempt_requires_prior_preregistration_and_holdout_seal(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")

    # When / Then
    with pytest.raises(StrategyResearchLedgerError, match="preregistration_missing"), store.writer() as writer:
        _ = writer.append_strategy_research_attempt(_attempt(0, AttemptStatus.FAILED))


def test_all_strategy_research_tables_reject_update_and_delete(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    with ExperimentLedgerStore(database).writer() as writer:
        _ = writer.register_strategy_research(_manifest())
        _ = writer.append_strategy_research_attempt(_attempt(0, AttemptStatus.SUCCEEDED))
        _ = writer.append_strategy_research_agent_state(_state_event())
        _ = writer.reveal_strategy_research_holdout(_reveal())

    # When / Then
    with sqlite3.connect(database) as connection:
        for table in (
            "strategy_research_preregistrations",
            "strategy_research_holdout_seals",
            "strategy_research_attempts",
            "strategy_research_agent_state_events",
            "strategy_research_holdout_reveals",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                _ = connection.execute(f"UPDATE {table} SET rowid=rowid")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                _ = connection.execute(f"DELETE FROM {table}")


def test_holdout_reveal_requires_seal_and_second_reveal_fails_closed(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    competing_store = ExperimentLedgerStore(database)
    with store.writer() as writer:
        _ = writer.register_strategy_research(_manifest())
        first = writer.reveal_strategy_research_holdout(_reveal())
        with pytest.raises(ExperimentLedgerWriterLeaseUnavailableError), competing_store.writer():
            pass

    # When / Then
    assert first is True
    with (
        pytest.raises(StrategyResearchLedgerError, match="holdout_already_revealed"),
        competing_store.writer() as writer,
    ):
        _ = writer.reveal_strategy_research_holdout(_reveal())


def test_generator_feedback_reader_excludes_exact_holdout_metrics(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    with ExperimentLedgerStore(database).writer() as writer:
        _ = writer.register_strategy_research(_manifest())
        _ = writer.reveal_strategy_research_holdout(_reveal())

    # When
    feedback = ExperimentLedgerReader(database).strategy_research_feedback(_manifest().hypothesis.agent_id)

    # Then
    assert feedback == (_result(),)
    serialized = "".join(item.model_dump_json() for item in feedback)
    assert "exact_sharpe" not in serialized
    assert "0.731" not in serialized


def test_related_hypothesis_cannot_reveal_same_search_lineage_twice(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    original = _manifest()
    child_id = "hypothesis-catalyst-002"
    child_seal = original.hypothesis.holdout_period_sealed_ref.model_copy(
        update={"seal_id": "sealed-holdout-catalyst-2026q3-child"}
    )
    child_hypothesis = original.hypothesis.model_copy(
        update={
            "hypothesis_id": child_id,
            "parent_hypothesis_id": original.hypothesis.hypothesis_id,
            "holdout_period_sealed_ref": child_seal,
        }
    )
    child_manifest = PreregistrationManifest.from_hypothesis(
        child_hypothesis,
        preregistered_at=original.preregistered_at,
    )
    child_result = _result().model_copy(update={"result_id": "terminal-result-2", "hypothesis_id": child_id})
    child_reveal = _reveal().model_copy(
        update={
            "reveal_id": "holdout-reveal-2",
            "hypothesis_id": child_id,
            "seal_id": child_seal.seal_id,
            "sanitized_result": child_result,
        }
    )
    with store.writer() as writer:
        _ = writer.register_strategy_research(original)
        _ = writer.register_strategy_research(child_manifest)
        _ = writer.reveal_strategy_research_holdout(_reveal())

    # When / Then
    with pytest.raises(StrategyResearchLedgerError, match="holdout_already_revealed"), store.writer() as writer:
        _ = writer.reveal_strategy_research_holdout(child_reveal)


def test_child_preregistration_cannot_change_persisted_parent_search_family(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    parent = _manifest()
    child_hypothesis = parent.hypothesis.model_copy(
        update={
            "hypothesis_id": "hypothesis-catalyst-split-lineage",
            "parent_hypothesis_id": parent.hypothesis.hypothesis_id,
            "search_family_id": "different-search-family",
            "holdout_period_sealed_ref": parent.hypothesis.holdout_period_sealed_ref.model_copy(
                update={"seal_id": "different-family-seal"}
            ),
        }
    )
    child = PreregistrationManifest.from_hypothesis(
        child_hypothesis,
        preregistered_at=parent.preregistered_at,
    )
    with store.writer() as writer:
        _ = writer.register_strategy_research(parent)

    # When / Then
    with pytest.raises(StrategyResearchLedgerError, match="lineage_parent_mismatch"), store.writer() as writer:
        _ = writer.register_strategy_research(child)
    with (
        sqlite3.connect(store.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="lineage-parent-mismatch"),
    ):
        _ = connection.execute(
            """INSERT INTO strategy_research_preregistrations
            (registration_key,hypothesis_id,parent_hypothesis_id,search_family_id,
            agent_id,protocol_version,payload_json) VALUES (?,?,?,?,?,?,?)""",
            (
                child.content_sha256,
                child.hypothesis.hypothesis_id,
                child.hypothesis.parent_hypothesis_id,
                child.hypothesis.search_family_id,
                child.hypothesis.agent_id.value,
                child.hypothesis.protocol_version,
                child.model_dump_json(),
            ),
        )


def test_raw_sql_reveal_rejects_hypothesis_seal_mismatch(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    first = _manifest()
    second_hypothesis = first.hypothesis.model_copy(
        update={
            "hypothesis_id": "hypothesis-unrelated-002",
            "search_family_id": "unrelated-search-family-002",
            "holdout_period_sealed_ref": first.hypothesis.holdout_period_sealed_ref.model_copy(
                update={"seal_id": "unrelated-seal-002"}
            ),
        }
    )
    second = PreregistrationManifest.from_hypothesis(
        second_hypothesis,
        preregistered_at=first.preregistered_at,
    )
    with ExperimentLedgerStore(database).writer() as writer:
        _ = writer.register_strategy_research(first)
        _ = writer.register_strategy_research(second)
    reveal = _reveal()

    # When / Then
    with sqlite3.connect(database) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            _ = connection.execute(
                "INSERT INTO strategy_research_holdout_reveals VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "d" * 64,
                    "raw-mismatched-reveal",
                    first.hypothesis.hypothesis_id,
                    first.hypothesis.search_family_id,
                    second.hypothesis.holdout_period_sealed_ref.seal_id,
                    first.hypothesis.agent_id.value,
                    TerminalOutcome.SUPPORTED.value,
                    reveal.sanitized_result.model_dump_json(),
                    reveal.model_dump_json(),
                ),
            )


def test_two_concurrent_writer_connections_allow_exactly_one_holdout_reveal(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    with ExperimentLedgerStore(database).writer() as writer:
        _ = writer.register_strategy_research(_manifest())
    barrier = threading.Barrier(2)

    def competing_reveal() -> str:
        _ = barrier.wait()
        try:
            with ExperimentLedgerStore(database).writer() as writer:
                _ = writer.reveal_strategy_research_holdout(_reveal())
        except ExperimentLedgerWriterLeaseUnavailableError:
            return "writer_lease_unavailable"
        except StrategyResearchLedgerError as error:
            return str(error)
        return "revealed"

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: competing_reveal(), range(2)))

    # Then
    assert results.count("revealed") == 1
    assert results.count("writer_lease_unavailable") + results.count("holdout_already_revealed") == 1
    with sqlite3.connect(database) as connection:
        stored_count = connection.execute("SELECT count(*) FROM strategy_research_holdout_reveals").fetchone()
    assert stored_count == (1,)


def test_agent_state_events_replay_and_recovery_survive_restart(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    event = _state_event()
    with ExperimentLedgerStore(database).writer() as writer:
        assert writer.append_strategy_research_agent_state(event) is True
        assert writer.append_strategy_research_agent_state(event) is False

    # When
    stored = ExperimentLedgerReader(database).strategy_research_agent_state(event.agent_id)

    # Then
    assert stored == (event,)
