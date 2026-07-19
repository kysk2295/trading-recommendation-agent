from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

from tests.alpaca_sip_dynamic_reconnect_fixtures import ConnectorQueue, FixtureClock
from tests.test_alpaca_sip_live_actionability import _CAPTURE_AT, _EPOCH, _connection, _dependencies
from tests.test_us_runtime_live_actionability_dispatch import (
    _dispatch_request,
    _long_lived_request,
    _write_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    build_alpaca_sip_quote_actionability_manifest,
    write_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.us_runtime_live_actionability_dispatch import dispatch_us_runtime_live_actionability
from trading_agent.us_runtime_live_evidence_verifier import (
    RuntimeLiveEvidenceVerificationError,
    RuntimeLiveEvidenceVerificationRequest,
    verify_runtime_live_evidence,
)
from trading_agent.us_runtime_minute_supervisor import (
    RuntimeSupervisorStatus,
    build_runtime_minute_supervisor_record,
)
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore
from trading_agent.us_runtime_supervisor_live_audit import (
    RuntimeSupervisorLiveOutcome,
    RuntimeSupervisorLiveStatus,
    build_runtime_supervisor_live_audit,
)


def test_two_minute_created_then_replay_evidence_verifies(tmp_path: Path) -> None:
    request = _evidence(tmp_path)

    result = verify_runtime_live_evidence(request)

    assert (
        result.completed_attempt_count,
        result.selected_manifest_count,
        result.created_terminal_count,
        result.replay_terminal_count,
    ) == (2, 2, 1, 1)
    assert result.actionability_artifact_count == 1


def test_missing_created_receipt_fails_closed(tmp_path: Path) -> None:
    request = _evidence(tmp_path)
    receipt = next(request.receipt_root.glob("*.sqlite3"))
    receipt.unlink()

    with pytest.raises(RuntimeLiveEvidenceVerificationError):
        _ = verify_runtime_live_evidence(request)


def test_public_created_receipt_is_normalized_to_verification_error(tmp_path: Path) -> None:
    request = _evidence(tmp_path)
    receipt = next(request.receipt_root.glob("*.sqlite3"))
    receipt.chmod(0o644)

    with pytest.raises(RuntimeLiveEvidenceVerificationError):
        _ = verify_runtime_live_evidence(request)


def test_child_created_replay_split_mismatch_fails_closed(tmp_path: Path) -> None:
    request = _evidence(
        tmp_path,
        first_outcome=RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.COMPLETED, 1, 0, 1),
    )

    with pytest.raises(RuntimeLiveEvidenceVerificationError):
        _ = verify_runtime_live_evidence(request)


def _evidence(
    tmp_path: Path,
    *,
    first_outcome: RuntimeSupervisorLiveOutcome | None = None,
) -> RuntimeLiveEvidenceVerificationRequest:
    source = _long_lived_request(tmp_path / "state")
    manifest_root = tmp_path / "manifests"
    _write_manifest(manifest_root, source)
    first_request = _dispatch_request(tmp_path, manifest_root, source)
    _ = dispatch_us_runtime_live_actionability(
        first_request,
        _dependencies(ConnectorQueue([_connection()]), FixtureClock(_CAPTURE_AT), (_EPOCH,)),
    )
    next_at = source.manifest.snapshot.observed_at + dt.timedelta(minutes=1)
    next_identity = replace(
        source.manifest.snapshot.identity,
        dataset_id="ds_fixture_next_minute",
        identity_sha256="c" * 64,
    )
    next_manifest = build_alpaca_sip_quote_actionability_manifest(
        source.manifest.base_publication,
        replace(source.manifest.snapshot, identity=next_identity, observed_at=next_at),
        source.manifest.plan,
        scan_started_at=source.manifest.scan_started_at,
    )
    next_path = manifest_root / f"{next_manifest.manifest_id.rpartition(':')[2]}.json"
    assert write_alpaca_sip_quote_actionability_manifest(next_path, next_manifest)
    replay = dispatch_us_runtime_live_actionability(
        replace(first_request, evaluated_at=next_at),
        _dependencies(ConnectorQueue([_connection()]), FixtureClock(next_at), ("2" * 32,)),
    )
    assert (replay.created_count, replay.replay_count) == (0, 1)
    supervisor = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    _append_attempt(
        supervisor,
        1,
        source.manifest.snapshot.observed_at,
        _CAPTURE_AT + dt.timedelta(seconds=1),
        (
            RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.COMPLETED, 1, 1, 0)
            if first_outcome is None
            else first_outcome
        ),
    )
    _append_attempt(
        supervisor,
        2,
        next_at,
        next_at + dt.timedelta(seconds=1),
        RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.COMPLETED, 1, 0, 1),
    )
    return RuntimeLiveEvidenceVerificationRequest(
        supervisor.path,
        manifest_root,
        tmp_path / "live-receipts",
        tmp_path / "actionability.sqlite3",
    )


def _append_attempt(
    store: RuntimeMinuteSupervisorStore,
    index: int,
    started_at: dt.datetime,
    finished_at: dt.datetime,
    outcome: RuntimeSupervisorLiveOutcome,
) -> None:
    parent = build_runtime_minute_supervisor_record(
        index,
        started_at,
        finished_at,
        RuntimeSupervisorStatus.READY,
        None,
        str(index) * 64,
    )
    assert store.append_attempt(parent, build_runtime_supervisor_live_audit(parent.attempt_id, outcome))
