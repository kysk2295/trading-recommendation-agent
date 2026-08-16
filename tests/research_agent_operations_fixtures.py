from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
from pathlib import Path

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_autonomous_research import AutonomousTaskReceiptV1
from trading_agent.research_agent_cycle_models import (
    CycleId,
    EvidenceId,
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
    research_agent_result_id,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_cycle_store_codec import canonical_cycle_json
from trading_agent.research_agent_operations_models import (
    ResearchAgentOperationsInputs,
    ResearchAgentOperationsLimits,
)

NOW = dt.datetime(2026, 8, 3, 3, tzinfo=dt.UTC)


def sources(tmp_path: Path) -> ResearchAgentOperationsInputs:
    receipts, runs = tmp_path / "receipts", tmp_path / "runs"
    receipts.mkdir(mode=0o700)
    runs.mkdir(mode=0o700)
    return ResearchAgentOperationsInputs(
        cycle_database=tmp_path / "cycles.sqlite3",
        task_receipt_root=receipts,
        systematic_runs_root=runs,
        as_of=NOW,
    )


def limits() -> ResearchAgentOperationsLimits:
    return ResearchAgentOperationsLimits(
        max_evidence_age_seconds=300,
        daily_token_limit_per_family=1_000,
        daily_cost_limit_microusd_per_family=10_000,
        systematic_heavy_experiment_limit=2,
        storage_limit_bytes=10_000_000,
    )


def seed_cycles(path: Path, families: tuple[AgentFamilyId, ...], evidence_at: dt.datetime) -> None:
    with ResearchAgentCycleStore(path) as store:
        for index, family in enumerate(families, start=1):
            digest = hashlib.sha256(f"{family}:{index}".encode()).hexdigest()
            evidence = ResearchAgentEvidenceV1(
                evidence_id=EvidenceId(digest),
                agent_family_id=family,
                trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
                source_key=f"source.{family}",
                evidence_refs=(digest,),
                observed_at=evidence_at,
                available_at=evidence_at,
                payload_sha256=digest,
                market_id="none",
            )
            store.append_evidence(evidence)
            cycle = store.start_cycle(store.runnable_evidence(family, NOW)[0], evidence_at)
            store.finish_cycle(
                cycle,
                ResearchAgentResultV1(
                    result_id=research_agent_result_id(cycle.cycle_id),
                    cycle_id=cycle.cycle_id,
                    agent_family_id=family,
                    market_id="none",
                    status=ResearchAgentResultStatus.COMPLETED,
                    question="Does this evidence support a bounded research action?",
                    summary="The bounded research action completed without trading authority.",
                    reason=None,
                    continuation=None,
                    evidence_refs=(digest,),
                    artifact_refs=(hashlib.sha256(f"{family}:{index}:artifact".encode()).hexdigest(),),
                    occurred_at=evidence_at + dt.timedelta(seconds=1),
                    next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
                    next_wake_at=None,
                ),
            )
        if families:
            store.upsert_open_work(
                ResearchAgentOpenWorkV1(
                    work_id=f"work.{families[0]}",
                    cycle_id=CycleId("f" * 64),
                    agent_family_id=families[0],
                    state=ResearchAgentOpenWorkState.TERMINAL,
                    evidence_refs=("e" * 64,),
                    next_wake_at=None,
                    updated_at=NOW,
                    source_evidence_id=None,
                )
            )


def claim(family: AgentFamilyId, summary: str) -> AutonomousTaskReceiptV1:
    return AutonomousTaskReceiptV1(
        public_task_id="a" * 32,
        event_id="b" * 64,
        agent_family_id=family,
        trigger_type="new_data",
        policy_version="operations-v1",
        code_version="c" * 40,
        sequence=0,
        kind="claim",
        state="claimed",
        occurred_at=NOW - dt.timedelta(seconds=10),
        reason=None,
        evidence_refs=(),
        result_sha256=None,
        summary=summary,
        consumed_tokens=100,
        consumed_cost_microusd=1_000,
    )


def write_receipt(root: Path, receipt: AutonomousTaskReceiptV1) -> None:
    private_text(root / "claim.json", receipt.model_dump_json())


def open_wal_evidence(
    path: Path,
    family: AgentFamilyId,
    evidence_at: dt.datetime,
) -> sqlite3.Connection:
    seed_cycles(path, (), evidence_at)
    digest = hashlib.sha256(f"wal:{family}".encode()).hexdigest()
    evidence = ResearchAgentEvidenceV1(
        evidence_id=EvidenceId(digest),
        agent_family_id=family,
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"source.wal.{family}",
        evidence_refs=(digest,),
        observed_at=evidence_at,
        available_at=evidence_at,
        payload_sha256=digest,
        market_id="none",
    )
    connection = sqlite3.connect(path)
    _ = connection.execute("PRAGMA journal_mode=WAL")
    _ = connection.execute(
        "INSERT INTO evidence(evidence_id,agent_family_id,available_at,payload_json) VALUES(?,?,?,?)",
        (digest, family, evidence_at.isoformat(), canonical_cycle_json(evidence)),
    )
    connection.commit()
    Path(f"{path}-wal").chmod(0o600)
    Path(f"{path}-shm").chmod(0o600)
    return connection


def write_complete_run(root: Path) -> None:
    output = root / "run-001" / "output"
    output.mkdir(mode=0o700, parents=True)
    os.chmod(output.parent, 0o700)
    private_text(
        output / "autonomous_research_cycle_ko.md",
        "\n".join(
            (
                "- result: complete",
                "- strategy_artifact_id: strategy-1",
                "- trial_id: trial-1",
                "- experiment_artifact_id: experiment-1",
                "- review_artifact_id: review-1",
                "- reviewer_decision: accepted",
                "- lifecycle authority: false",
                "- allocation authority: false",
                "- order authority: false",
                "- trading mutation: 0",
            )
        ),
    )


def private_text(path: Path, payload: str) -> None:
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o600)


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def mutate_cycle_store(
    inputs: ResearchAgentOperationsInputs,
    mutation: str,
) -> ResearchAgentOperationsInputs:
    path = inputs.cycle_database
    if mutation == "missing":
        path = path.with_name("missing.sqlite3")
    elif mutation == "nonprivate":
        path.chmod(0o644)
    elif mutation == "symlink":
        link = path.with_name("linked.sqlite3")
        link.symlink_to(path)
        path = link
    elif mutation == "hardlink":
        link = path.with_name("hard.sqlite3")
        os.link(path, link)
        path = link
    elif mutation == "malformed":
        path.write_bytes(b"not-sqlite")
        path.chmod(0o600)
    elif mutation == "wrong_schema":
        path.unlink()
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA user_version=1")
        path.chmod(0o600)
    return inputs.model_copy(update={"cycle_database": path})


def mutate_receipt_store(inputs: ResearchAgentOperationsInputs, mutation: str) -> str:
    root = inputs.task_receipt_root
    marker = "receipt-authoritative-private-marker"
    if mutation == "malformed":
        private_text(root / "receipt.json", f'{{"private":"{marker}"}}')
        return marker
    original = root / "receipt.json"
    private_text(original, claim("systematic_quant", marker).model_dump_json())
    if mutation == "nonprivate":
        original.chmod(0o644)
    elif mutation == "symlink":
        (root / "linked.json").symlink_to(original)
    elif mutation == "hardlink":
        os.link(original, root / "hard.json")
    return marker


def cli_command(inputs: ResearchAgentOperationsInputs) -> tuple[str, ...]:
    return (
        "uv",
        "run",
        "run_research_agent_operations.py",
        "--cycle-database",
        str(inputs.cycle_database),
        "--task-receipt-root",
        str(inputs.task_receipt_root),
        "--systematic-runs-root",
        str(inputs.systematic_runs_root),
        "--as-of",
        NOW.isoformat(),
        "--max-evidence-age-seconds",
        "300",
    )
