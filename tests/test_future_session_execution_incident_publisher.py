from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

import trading_agent.future_session_execution_incident_publisher as publisher
from trading_agent.future_session_execution_incident import (
    FutureSessionExecutionIncidentReceipt,
    canonical_execution_incident_json,
)
from trading_agent.future_session_execution_incident_queue import (
    FutureSessionExecutionIncidentQueuePointer,
    canonical_execution_incident_queue_json,
)
from trading_agent.future_session_plan_models import FutureSessionMarket
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError


def test_pointer_failure_retries_from_same_durable_incident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = dt.date(2026, 8, 17)
    artifact = tmp_path / "state" / "artifacts" / "us" / target.isoformat()
    incident_dir = artifact / "execution-incidents"
    incident_dir.mkdir(parents=True, mode=0o700)
    manifest = artifact / "preparation-manifest.json"
    manifest.write_text("fixture\n", encoding="utf-8")
    manifest.chmod(0o600)
    queue_root = tmp_path / "state" / "pending-execution-incidents"
    queue_root.mkdir(mode=0o700)
    receipt_path = incident_dir / "us_orb_watcher.json"
    queue_path = queue_root / f"us--{target.isoformat()}--us_orb_watcher.json"
    original_publish = publisher.publish_private_immutable_text_once

    def fail_queue(path: Path, payload: str) -> bool:
        if path == queue_path:
            raise InvalidPrivateImmutableFileError
        return original_publish(path, payload)

    monkeypatch.setattr(publisher, "publish_private_immutable_text_once", fail_queue)
    with pytest.raises(InvalidPrivateImmutableFileError):
        _publish(publisher, receipt_path, queue_path, manifest, target)
    original_receipt = receipt_path.read_bytes()
    assert not queue_path.exists()

    monkeypatch.setattr(publisher, "publish_private_immutable_text_once", original_publish)
    _publish(publisher, receipt_path, queue_path, manifest, target)

    receipt = FutureSessionExecutionIncidentReceipt.model_validate_json(original_receipt)
    pointer = FutureSessionExecutionIncidentQueuePointer.model_validate_json(queue_path.read_bytes())
    assert receipt_path.read_bytes() == original_receipt
    assert canonical_execution_incident_json(receipt).encode() == original_receipt
    assert canonical_execution_incident_queue_json(pointer).encode() == queue_path.read_bytes()


def _publish(
    module,
    receipt: Path,
    queue: Path,
    manifest: Path,
    target: dt.date,
) -> None:
    module.publish_execution_incident(
        receipt_path=receipt,
        queue_path=queue,
        manifest_path=manifest,
        market=FutureSessionMarket.US,
        target_session=target,
        role="us_orb_watcher",
        request_sha256="a" * 64,
        plan_sha256="b" * 64,
        scheduler_main_sha="c" * 40,
        runtime_commit_sha="c" * 40,
    )
