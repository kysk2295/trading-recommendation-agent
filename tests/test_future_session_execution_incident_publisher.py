from __future__ import annotations

import datetime as dt
import multiprocessing
from multiprocessing.synchronize import Barrier
from pathlib import Path

import pytest

import run_future_session_execution_incident_publisher as publisher
from tests.test_forward_runtime_readiness_cli import _git
from trading_agent.future_session_execution_incident import (
    FutureSessionExecutionIncidentReceipt,
    canonical_execution_incident_json,
)
from trading_agent.future_session_execution_incident_artifact import (
    read_execution_incident_publisher_at_commit,
)
from trading_agent.future_session_execution_incident_queue import (
    FutureSessionExecutionIncidentQueuePointer,
    canonical_execution_incident_queue_json,
)


def test_pointer_failure_retries_from_same_durable_incident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt, queue, manifest, target = _layout(tmp_path)
    original_publish = publisher._publish_exact

    def fail_queue(parent: int, name: str, payload: bytes) -> None:
        if name == queue.name:
            raise publisher.IncidentPublicationError
        original_publish(parent, name, payload)

    monkeypatch.setattr(publisher, "_publish_exact", fail_queue)
    with pytest.raises(publisher.IncidentPublicationError):
        _publish(receipt, queue, manifest, target)
    original_receipt = receipt.read_bytes()
    assert not queue.exists()

    monkeypatch.setattr(publisher, "_publish_exact", original_publish)
    _publish(receipt, queue, manifest, target)

    execution_incident = FutureSessionExecutionIncidentReceipt.model_validate_json(original_receipt)
    pointer = FutureSessionExecutionIncidentQueuePointer.model_validate_json(queue.read_bytes())
    assert receipt.read_bytes() == original_receipt
    assert canonical_execution_incident_json(execution_incident).encode() == original_receipt
    assert canonical_execution_incident_queue_json(pointer).encode() == queue.read_bytes()


def test_concurrent_publishers_converge_without_queue_artifacts(tmp_path: Path) -> None:
    receipt, queue, manifest, target = _layout(tmp_path)
    context = multiprocessing.get_context("spawn")
    gate = context.Barrier(3)
    processes = [
        context.Process(target=_publish_after_gate, args=(receipt, queue, manifest, target, gate))
        for _index in range(2)
    ]
    try:
        for process in processes:
            process.start()
        _ = gate.wait(timeout=10)
        for process in processes:
            process.join(timeout=10)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=10)

    assert tuple(process.exitcode for process in processes) == (0, 0)
    assert tuple(item.name for item in queue.parent.iterdir()) == (queue.name,)
    assert tuple(item.name for item in receipt.parent.iterdir()) == (receipt.name,)


def test_session_tree_swap_blocks_queue_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt, queue, manifest, target = _layout(tmp_path)
    state = tmp_path / "state"
    displaced = tmp_path / "displaced-state"
    original_publish = publisher._publish_receipt

    def swap_after_receipt(parent: int, name: str, candidate: dict[str, object]) -> bytes:
        payload = original_publish(parent, name, candidate)
        state.rename(displaced)
        _ = _layout(tmp_path)
        return payload

    monkeypatch.setattr(publisher, "_publish_receipt", swap_after_receipt)
    with pytest.raises(publisher.IncidentPublicationError):
        _publish(receipt, queue, manifest, target)

    assert not queue.exists()
    assert (displaced / "artifacts" / "us" / target.isoformat() / "execution-incidents" / receipt.name).exists()
    assert not (displaced / "pending-execution-incidents" / queue.name).exists()


def test_publisher_artifact_ignores_dirty_worktree_replacements_and_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "runtime"
    repository.mkdir(mode=0o700)
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "publisher@example.invalid")
    _git(repository, "config", "user.name", "Publisher Test")
    source = repository / "run_future_session_execution_incident_publisher.py"
    source.write_bytes(b"trusted publisher\n")
    _git(repository, "add", source.name)
    _git(repository, "commit", "--quiet", "-m", "publisher")
    commit = _git(repository, "rev-parse", "HEAD")
    trusted_blob = _git(repository, "rev-parse", f"{commit}:{source.name}")
    malicious_source = tmp_path / "malicious.py"
    malicious_source.write_bytes(b"malicious replacement publisher\n")
    malicious_blob = _git(repository, "hash-object", "-w", str(malicious_source))
    _git(repository, "replace", trusted_blob, malicious_blob)
    source.write_bytes(b"malicious publisher\n")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(mode=0o700)
    fake_marker = tmp_path / "fake-git-executed"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        f"#!/bin/sh\n/usr/bin/touch {fake_marker}\n/usr/bin/printf 'malicious path publisher\\n'\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o700)
    monkeypatch.setenv("PATH", str(fake_bin))

    assert read_execution_incident_publisher_at_commit(repository, commit) == b"trusted publisher\n"
    assert not fake_marker.exists()


def _layout(tmp_path: Path) -> tuple[Path, Path, Path, dt.date]:
    target = dt.date(2026, 8, 17)
    artifact = tmp_path / "state" / "artifacts" / "us" / target.isoformat()
    incident_dir = artifact / "execution-incidents"
    incident_dir.mkdir(parents=True, mode=0o700)
    for directory in (tmp_path / "state", tmp_path / "state" / "artifacts", artifact.parent, artifact):
        directory.chmod(0o700)
    manifest = artifact / "preparation-manifest.json"
    manifest.write_text("fixture\n", encoding="utf-8")
    manifest.chmod(0o600)
    queue_root = tmp_path / "state" / "pending-execution-incidents"
    queue_root.mkdir(mode=0o700)
    receipt = incident_dir / "us_orb_watcher.json"
    queue = queue_root / f"us--{target.isoformat()}--us_orb_watcher.json"
    return receipt, queue, manifest, target


def _publish_after_gate(
    receipt: Path,
    queue: Path,
    manifest: Path,
    target: dt.date,
    gate: Barrier,
) -> None:
    _ = gate.wait(timeout=10)
    _publish(receipt, queue, manifest, target)


def _publish(
    receipt: Path,
    queue: Path,
    manifest: Path,
    target: dt.date,
) -> None:
    publisher.publish_execution_incident(
        receipt_path=receipt,
        queue_path=queue,
        manifest_path=manifest,
        market="us",
        target_session=target,
        role="us_orb_watcher",
        request_sha256="a" * 64,
        plan_sha256="b" * 64,
        scheduler_main_sha="c" * 40,
        runtime_commit_sha="c" * 40,
    )
