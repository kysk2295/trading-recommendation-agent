from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest
from research_agent_operations_fixtures import (
    NOW,
    claim,
    cli_command,
    limits,
    mutate_cycle_store,
    mutate_receipt_store,
    open_wal_evidence,
    seed_cycles,
    snapshot,
    sources,
    write_complete_run,
    write_receipt,
)

from trading_agent import research_agent_operations_readers as operations_readers
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES
from trading_agent.research_agent_operations import (
    build_research_agent_operations_status,
    canonical_research_agent_operations_json,
)
from trading_agent.research_agent_operations_models import (
    InvalidResearchAgentOperationsSourceError,
    OperationsAlertReason,
)
from trading_agent.research_agent_operations_sqlite import open_cycle_database_query_only


def test_status_is_six_family_canonical_restart_safe_and_query_only(tmp_path: Path) -> None:
    # Given: six fresh terminal families, one reservation, and one completed bounded Systematic run
    inputs = sources(tmp_path)
    seed_cycles(inputs.cycle_database, PRIMARY_AGENT_FAMILIES, NOW - dt.timedelta(seconds=30))
    secret = "receipt-private-summary"
    write_receipt(inputs.task_receipt_root, claim("systematic_quant", secret))
    write_complete_run(inputs.systematic_runs_root)
    before = snapshot(tmp_path)

    # When: status is collected twice after a process-style restart boundary
    first = build_research_agent_operations_status(inputs, limits())
    second = build_research_agent_operations_status(inputs, limits())

    # Then: output is stable, exactly six-family, redacted, and source bytes are unchanged
    payload = canonical_research_agent_operations_json(first)
    assert first == second
    assert tuple(item.family_id for item in first.families) == PRIMARY_AGENT_FAMILIES
    assert first.status == "ready"
    assert first.families[3].reserved_tokens == 100
    assert first.families[3].reserved_cost_microusd == 1_000
    assert first.families[3].reserved_model_calls == 1
    assert all(item.last_terminal_at is not None for item in first.families)
    assert all(item.last_success_at is not None for item in first.families)
    assert all(item.consecutive_failures == 0 for item in first.families)
    assert first.systematic_heavy_experiments.completions == 1
    assert first.invocation_effects.model_dump() == {
        "provider_calls": 0,
        "model_calls": 0,
        "heavy_processes": 0,
        "broker_mutation": 0,
    }
    assert str(tmp_path) not in payload and secret not in payload
    assert json.dumps(json.loads(payload), sort_keys=True, separators=(",", ":")) == payload
    assert snapshot(tmp_path) == before


def test_status_reads_latest_evidence_from_active_private_wal_without_writes(tmp_path: Path) -> None:
    # Given: the only current evidence row is committed in an open private WAL
    inputs = sources(tmp_path)
    connection = open_wal_evidence(inputs.cycle_database, "opportunity_manager", NOW)
    before = snapshot(tmp_path)

    # When: the operations surface reads the active database
    try:
        status = build_research_agent_operations_status(inputs, limits())

        # Then: WAL evidence is current and every source byte remains unchanged
        assert status.families[0].last_evidence_at == NOW
        assert status.families[0].evidence_state == "fresh"
        cycle_storage = sum(
            len(payload)
            for name, payload in before.items()
            if name in {"cycles.sqlite3", "cycles.sqlite3-wal", "cycles.sqlite3-shm"}
        )
        assert status.storage.used_bytes == cycle_storage
        assert snapshot(tmp_path) == before
    finally:
        connection.close()


def test_status_blocks_stale_and_missing_evidence(tmp_path: Path) -> None:
    # Given: one prior evidence record and five families without evidence
    inputs = sources(tmp_path)
    seed_cycles(inputs.cycle_database, ("opportunity_manager",), NOW - dt.timedelta(hours=2))

    # When: the current status evaluates a five-minute freshness bound
    status = build_research_agent_operations_status(inputs, limits())

    # Then: stale and missing states are explicit safe alerts
    assert status.status == "blocked"
    assert status.families[0].evidence_state == "stale"
    assert status.families[0].evidence_age_seconds == 7_200
    assert all(item.evidence_state == "missing" for item in status.families[1:])
    assert OperationsAlertReason.EVIDENCE_STALE in status.alerts
    assert OperationsAlertReason.EVIDENCE_MISSING in status.alerts


def test_status_blocks_exhausted_reservations_heavy_limit_and_storage(tmp_path: Path) -> None:
    # Given: fresh evidence with reservations and a completed run at exact limits
    inputs = sources(tmp_path)
    seed_cycles(inputs.cycle_database, PRIMARY_AGENT_FAMILIES, NOW - dt.timedelta(seconds=30))
    write_receipt(inputs.task_receipt_root, claim("systematic_quant", "bounded"))
    write_complete_run(inputs.systematic_runs_root)
    bounded_limits = limits().model_copy(
        update={
            "daily_token_limit_per_family": 100,
            "daily_cost_limit_microusd_per_family": 1_000,
            "systematic_heavy_experiment_limit": 1,
            "storage_limit_bytes": 1,
        }
    )

    # When: aggregate bounds are evaluated
    status = build_research_agent_operations_status(inputs, bounded_limits)

    # Then: reservations are not actual usage and every exhausted bound is blocked
    systematic = status.families[3]
    assert systematic.reservation_status == "exhausted"
    assert systematic.reserved_tokens == 100
    assert systematic.reserved_cost_microusd == 1_000
    assert status.systematic_heavy_experiments.status == "exhausted"
    assert status.storage.status == "over_limit"
    assert set(status.alerts) >= {
        OperationsAlertReason.TOKEN_BUDGET_EXHAUSTED,
        OperationsAlertReason.COST_BUDGET_EXHAUSTED,
        OperationsAlertReason.HEAVY_EXPERIMENT_BUDGET_EXHAUSTED,
        OperationsAlertReason.STORAGE_LIMIT_EXCEEDED,
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("missing", OperationsAlertReason.CYCLE_STORE_MISSING),
        ("nonprivate", OperationsAlertReason.CYCLE_STORE_NONPRIVATE),
        ("symlink", OperationsAlertReason.CYCLE_STORE_SYMLINK),
        ("hardlink", OperationsAlertReason.CYCLE_STORE_HARDLINK),
        ("malformed", OperationsAlertReason.CYCLE_STORE_MALFORMED),
        ("wrong_schema", OperationsAlertReason.CYCLE_STORE_WRONG_SCHEMA),
    ),
)
def test_status_fails_closed_for_invalid_cycle_store(
    tmp_path: Path,
    mutation: str,
    reason: OperationsAlertReason,
) -> None:
    # Given: valid empty roots and one specifically invalid cycle store identity
    inputs = sources(tmp_path)
    seed_cycles(inputs.cycle_database, (), NOW)
    inputs = mutate_cycle_store(inputs, mutation)

    # When: the read-only boundary inspects the invalid store
    status = build_research_agent_operations_status(inputs, limits())

    # Then: it returns one typed safe reason and still emits exactly six families
    assert status.status == "blocked"
    assert status.alerts == (reason,)
    assert tuple(item.family_id for item in status.families) == PRIMARY_AGENT_FAMILIES


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("malformed", OperationsAlertReason.RECEIPT_STORE_MALFORMED),
        ("nonprivate", OperationsAlertReason.RECEIPT_STORE_NONPRIVATE),
        ("symlink", OperationsAlertReason.RECEIPT_STORE_SYMLINK),
        ("hardlink", OperationsAlertReason.RECEIPT_STORE_HARDLINK),
    ),
)
def test_status_fails_closed_for_invalid_receipt_store(
    tmp_path: Path,
    mutation: str,
    reason: OperationsAlertReason,
) -> None:
    # Given: a receipt store with one invalid authoritative entry
    inputs = sources(tmp_path)
    seed_cycles(inputs.cycle_database, PRIMARY_AGENT_FAMILIES, NOW)
    marker = mutate_receipt_store(inputs, mutation)
    before = snapshot(tmp_path)

    # When: status evaluates the invalid receipt store
    status = build_research_agent_operations_status(inputs, limits())

    # Then: only a typed zero-effect reason is exposed and no source or output changes
    payload = canonical_research_agent_operations_json(status)
    assert status.alerts == (reason,)
    assert status.invocation_effects.model_dump() == {
        "provider_calls": 0,
        "model_calls": 0,
        "heavy_processes": 0,
        "broker_mutation": 0,
    }
    assert str(tmp_path) not in payload and marker not in payload
    assert snapshot(tmp_path) == before


def test_private_store_traversal_bounds_all_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an entry limit of one and two empty private directories
    root = tmp_path / "runs"
    root.mkdir(mode=0o700)
    (root / "one").mkdir(mode=0o700)
    (root / "two").mkdir(mode=0o700)
    monkeypatch.setattr(operations_readers, "_MAX_ENTRIES", 1)

    # When: the bounded reader traverses the directory tree
    with pytest.raises(InvalidResearchAgentOperationsSourceError) as captured:
        _ = operations_readers.private_store_files(root, "runs")

    # Then: empty directories count toward the same malformed-store limit
    assert captured.value.reason is OperationsAlertReason.RUNS_STORE_MALFORMED


def test_sidecar_free_reader_rejects_main_database_replacement(tmp_path: Path) -> None:
    # Given: a private checkpointed database and a distinct private replacement inode
    inputs = sources(tmp_path)
    seed_cycles(inputs.cycle_database, (), NOW)
    replacement = inputs.cycle_database.with_suffix(".replacement")
    replacement.write_bytes(inputs.cycle_database.read_bytes())
    replacement.chmod(0o600)

    # When: the main path is replaced while its immutable snapshot is open
    with (
        pytest.raises(InvalidResearchAgentOperationsSourceError) as captured,
        open_cycle_database_query_only(inputs.cycle_database),
    ):
        replacement.replace(inputs.cycle_database)

    # Then: post-read identity confirmation fails closed
    assert captured.value.reason is OperationsAlertReason.CYCLE_STORE_MALFORMED


def test_cli_happy_path_emits_the_same_canonical_contract(tmp_path: Path) -> None:
    # Given: valid current private sources and the repository PEP-723 CLI
    inputs = sources(tmp_path)
    seed_cycles(inputs.cycle_database, PRIMARY_AGENT_FAMILIES, NOW - dt.timedelta(seconds=30))
    command = cli_command(inputs)

    # When: the CLI runs as a separate process
    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: it exits successfully with canonical six-family JSON only
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert [item["family_id"] for item in payload["families"]] == list(PRIMARY_AGENT_FAMILIES)
    assert payload["invocation_effects"]["broker_mutation"] == 0
