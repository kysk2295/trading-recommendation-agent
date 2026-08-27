from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from run_kr_loop_engineer import main
from tests.test_kr_autonomous_outcome_learning import _paths
from trading_agent.autonomous_memory_models import AutonomousMemoryRecord, AutonomousMemoryScope
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_task_models import AutonomousTaskId
from trading_agent.kr_autonomous_outcome_models import (
    KrLoopEngineerEvidenceBundle,
    KrLoopFailureCode,
    canonical_kr_loop_engineer_bundle_json,
    kr_loop_engineer_bundle_id,
)
from trading_agent.kr_loop_engineer_models import KrLoopCandidateState
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore
from trading_agent.kr_loop_engineer_sync import sync_kr_loop_bundles

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 27, 18, 0, tzinfo=KST)
BASE = "a" * 40


def test_bundle_sync_enqueues_one_detected_candidate_without_running_mutation(tmp_path: Path) -> None:
    # Given: one durable self-improvement memory and empty Loop Engineer state.
    paths = _paths(tmp_path)
    bundle = _bundle()
    record = _memory_record(bundle)
    with AutonomousMemoryStore(paths.memory_database).writer() as writer:
        assert writer.append(record)

    # When: service synchronization runs twice.
    first = sync_kr_loop_bundles(paths, base_commit=BASE, now=NOW)
    replay = sync_kr_loop_bundles(paths, base_commit=BASE, now=NOW)

    # Then: one detected candidate is queued and no coding worker or release is started.
    assert first.inserted == 1
    assert replay.inserted == 0
    snapshots = KrLoopEngineerStore(paths.loop_database).snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].state is KrLoopCandidateState.DETECTED
    assert KrLoopEngineerStore(paths.loop_database).releases() == ()


def test_cli_help_and_bad_receipt_do_not_mutate_loop_state(
    tmp_path: Path,
    capsys,
) -> None:
    # Given: an empty private loop database and a malformed receipt.
    database = tmp_path / "private" / "loop.sqlite3"
    receipt = tmp_path / "private" / "bad.json"
    receipt.parent.mkdir(mode=0o700)
    receipt.write_text('{"release_id":"not-valid"}\n', encoding="utf-8")
    receipt.chmod(0o600)

    # When: the operator reads help and submits the malformed health receipt.
    assert main(("--help",)) == 0
    result = main(("health", "--database", str(database), "--receipt", str(receipt)))

    # Then: input is rejected with a sanitized message and no database is created.
    captured = capsys.readouterr()
    assert result == 2
    assert "invalid Loop Engineer request" in captured.err
    assert str(tmp_path) not in captured.err
    assert not database.exists()


def test_cli_status_prints_only_candidate_ids_and_states(tmp_path: Path, capsys) -> None:
    # Given: one synchronized candidate in a private database.
    paths = _paths(tmp_path)
    bundle = _bundle()
    record = _memory_record(bundle)
    with AutonomousMemoryStore(paths.memory_database).writer() as writer:
        assert writer.append(record)
    assert sync_kr_loop_bundles(paths, base_commit=BASE, now=NOW).inserted == 1

    # When: the CLI status surface is invoked.
    assert main(("status", "--database", str(paths.loop_database))) == 0

    # Then: output contains bounded lifecycle metadata and no filesystem path.
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"][0]["state"] == "detected"
    assert payload["candidates"][0]["candidate_id"]
    assert str(tmp_path) not in json.dumps(payload)


def _bundle() -> KrLoopEngineerEvidenceBundle:
    draft = KrLoopEngineerEvidenceBundle.model_construct(
        bundle_id="",
        failure_code=KrLoopFailureCode.CRITIC_CLUSTER_COUNT,
        subject_ref="symbol:005930",
        source_memory_ids=("1" * 64, "2" * 64, "3" * 64),
        source_task_ids=("4" * 64, "5" * 64, "6" * 64),
        evidence_refs=("evidence:1",),
        change_hypothesis="Tighten independent-source clustering with deterministic replay evidence.",
        created_at=NOW,
    )
    return KrLoopEngineerEvidenceBundle.model_validate(
        draft.model_copy(update={"bundle_id": kr_loop_engineer_bundle_id(draft)}).model_dump(mode="python")
    )


def _memory_record(bundle: KrLoopEngineerEvidenceBundle) -> AutonomousMemoryRecord:
    return AutonomousMemoryRecord.model_validate(
        {
            "memory_key": "self_improvement.kr.critic_cluster_count.fixture",
            "version": 1,
            "scope": AutonomousMemoryScope.SELF_IMPROVEMENT,
            "summary": canonical_kr_loop_engineer_bundle_json(bundle),
            "fact_refs": bundle.source_memory_ids,
            "inference_refs": (bundle.bundle_id,),
            "subject_refs": ("failure:critic_cluster_count", "symbol:005930"),
            "evidence_refs": bundle.evidence_refs,
            "source_task_ids": tuple(AutonomousTaskId(item) for item in bundle.source_task_ids),
            "recorded_at": NOW,
        }
    )
