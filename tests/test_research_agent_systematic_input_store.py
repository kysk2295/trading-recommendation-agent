from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

import trading_agent.private_immutable_file as private_file
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.research_agent_systematic_input_models import (
    BlockedSystematicInputActivation,
    ReadySystematicInputActivation,
    SystematicInputActivation,
)
from trading_agent.research_agent_systematic_input_store import (
    InvalidSystematicInputActivationError,
    canonical_systematic_input_activation_json,
    load_systematic_input_activation,
    write_systematic_input_activation,
)

_NOW = dt.datetime(2026, 8, 3, 4, 5, tzinfo=dt.UTC)
_SHA = "a" * 64


def _ready(root: Path, **updates: int | float) -> ReadySystematicInputActivation:
    artifacts = {
        "dataset.csv": "timestamp,symbol,close\n2026-07-31T14:30:00Z,SPY,632.08\n",
        "dataset-receipt.json": '{"kind":"dataset"}\n',
        "catalog-receipt.json": '{"kind":"catalog"}\n',
        "binding-receipt.json": '{"kind":"binding"}\n',
        "foundation.json": '{"decision":"READY"}\n',
    }
    digests = {name: hashlib.sha256(payload.encode()).hexdigest() for name, payload in artifacts.items()}
    for name, payload in artifacts.items():
        _ = publish_private_immutable_text(root / name, payload)
    values = {
        "max_sessions": 60,
        "max_bars": 100_000,
        "rss_limit_gib": 10.0,
        **updates,
    }
    return ReadySystematicInputActivation(
        input_csv_path=root / "dataset.csv",
        input_csv_sha256=digests["dataset.csv"],
        dataset_receipt_path=root / "dataset-receipt.json",
        dataset_receipt_sha256=digests["dataset-receipt.json"],
        catalog_receipt_path=root / "catalog-receipt.json",
        catalog_receipt_sha256=digests["catalog-receipt.json"],
        input_binding_receipt_path=root / "binding-receipt.json",
        input_binding_receipt_sha256=digests["binding-receipt.json"],
        foundation_path=root / "foundation.json",
        foundation_sha256=digests["foundation.json"],
        producer_commit_sha="6" * 40,
        input_sha256=digests["dataset.csv"],
        selected_session_dates=(dt.date(2026, 7, 31),),
        bar_count=500,
        activated_at=_NOW,
        **values,
    )


def test_blocked_to_ready_replaces_pointer_atomically(tmp_path: Path) -> None:
    # Given
    pointer = tmp_path / "systematic-input.json"
    blocked = BlockedSystematicInputActivation(reason_code="catalog_unavailable", attempted_at=_NOW)
    ready = _ready(tmp_path)
    write_systematic_input_activation(pointer, blocked)

    # When
    write_systematic_input_activation(pointer, ready)

    # Then
    assert load_systematic_input_activation(pointer) == ready
    metadata = pointer.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_uid == os.getuid()
    assert metadata.st_nlink == 1


def test_exact_replay_is_byte_identical_and_canonical(tmp_path: Path) -> None:
    # Given
    pointer = tmp_path / "systematic-input.json"
    ready = _ready(tmp_path)

    # When
    write_systematic_input_activation(pointer, ready)
    first = pointer.read_bytes()
    replay = load_systematic_input_activation(pointer)
    write_systematic_input_activation(pointer, replay)

    # Then
    assert pointer.read_bytes() == first
    assert first == canonical_systematic_input_activation_json(ready).encode()
    assert first.endswith(b"\n") and not first.endswith(b"\n\n")
    assert json.loads(first) == json.loads(json.dumps(json.loads(first), sort_keys=True))


def test_blocked_attempt_report_is_bound_without_usable_data_paths(tmp_path: Path) -> None:
    # Given
    report = tmp_path / "attempt.json"
    payload = '{"reason":"no_graph"}\n'
    assert publish_private_immutable_text(report, payload)
    blocked = BlockedSystematicInputActivation(
        reason_code="no_connected_graph",
        attempted_at=_NOW,
        attempt_report_path=report,
        attempt_report_sha256=hashlib.sha256(payload.encode()).hexdigest(),
    )
    pointer = tmp_path / "activation.json"

    # When
    write_systematic_input_activation(pointer, blocked)

    # Then
    serialized = json.loads(pointer.read_text(encoding="utf-8"))
    assert load_systematic_input_activation(pointer) == blocked
    assert not any("dataset" in key or "foundation" in key for key in serialized)


def test_blocked_attempt_report_requires_private_mode_and_matching_digest(tmp_path: Path) -> None:
    # Given
    report = tmp_path / "attempt.json"
    report.write_text('{"reason":"no_graph"}\n', encoding="utf-8")
    report.chmod(0o644)
    blocked = BlockedSystematicInputActivation(
        reason_code="no_connected_graph",
        attempted_at=_NOW,
        attempt_report_path=report,
        attempt_report_sha256=_SHA,
    )

    # When / Then
    with pytest.raises(InvalidSystematicInputActivationError):
        write_systematic_input_activation(tmp_path / "activation.json", blocked)
    assert not (tmp_path / "activation.json").exists()


def test_ready_artifact_digest_mismatch_is_rejected_before_pointer_write(tmp_path: Path) -> None:
    # Given
    ready = _ready(tmp_path)
    ready.foundation_path.write_text('{"decision":"BLOCKED"}\n', encoding="utf-8")
    pointer = tmp_path / "activation.json"

    # When / Then
    with pytest.raises(InvalidSystematicInputActivationError):
        write_systematic_input_activation(pointer, ready)
    assert not pointer.exists()


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"max_sessions": 61}, "max_sessions"),
        ({"max_bars": 100_001}, "max_bars"),
        ({"rss_limit_gib": 10.1}, "rss_limit_gib"),
    ],
)
def test_over_budget_ready_activation_is_rejected(
    tmp_path: Path,
    updates: dict[str, int | float],
    expected: str,
) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match=expected):
        _ = _ready(tmp_path, **updates)


def test_blocked_contains_paths_is_rejected() -> None:
    # Given
    adapter = TypeAdapter(SystematicInputActivation)
    payload = {
        "schema_version": 1,
        "status": "blocked",
        "reason_code": "no_graph",
        "attempted_at": _NOW.isoformat(),
        "input_csv_path": "/private/data.csv",
    }

    # When / Then
    with pytest.raises(ValidationError, match="input_csv_path"):
        _ = adapter.validate_python(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": 2},
        {"activated_at": dt.datetime(2026, 8, 3, 4, 5)},
        {"input_csv_path": Path("relative.csv")},
        {"input_csv_sha256": "A" * 64},
        {"selected_session_dates": (dt.date(2026, 8, 1), dt.date(2026, 7, 31))},
        {"bar_count": 100_001},
    ],
)
def test_ready_rejects_schema_time_path_digest_and_bound_violations(
    tmp_path: Path,
    mutation: dict[str, int | str | Path | dt.datetime | tuple[dt.date, ...]],
) -> None:
    # Given
    payload = _ready(tmp_path).model_dump(mode="python") | mutation

    # When / Then
    with pytest.raises(ValidationError):
        _ = ReadySystematicInputActivation.model_validate(payload)


def test_ready_model_is_strict_frozen_and_forbids_unknown_fields(tmp_path: Path) -> None:
    # Given
    ready = _ready(tmp_path)

    # When / Then
    with pytest.raises(ValidationError):
        _ = ReadySystematicInputActivation.model_validate(
            ready.model_dump(mode="python") | {"max_sessions": "60"}
        )
    with pytest.raises(ValidationError):
        _ = ReadySystematicInputActivation.model_validate(
            ready.model_dump(mode="python") | {"account_id": "forbidden"}
        )
    with pytest.raises(ValidationError):
        ready.max_sessions = 30


def test_serialized_activation_excludes_secret_and_account_fields(tmp_path: Path) -> None:
    # Given
    ready = _ready(tmp_path)

    # When
    serialized = canonical_systematic_input_activation_json(ready).lower()

    # Then
    forbidden = ("api_key", "secret", "token", "credential", "authorization", "header", "account")
    assert not any(term in serialized for term in forbidden)


def test_symlink_pointer_is_rejected_before_replacement(tmp_path: Path) -> None:
    # Given
    target = tmp_path / "target.json"
    target.write_text("preserve\n", encoding="utf-8")
    target.chmod(0o600)
    pointer = tmp_path / "activation.json"
    pointer.symlink_to(target)

    # When / Then
    with pytest.raises(InvalidSystematicInputActivationError):
        write_systematic_input_activation(
            pointer,
            BlockedSystematicInputActivation(reason_code="no_graph", attempted_at=_NOW),
        )
    assert target.read_text(encoding="utf-8") == "preserve\n"


@pytest.mark.parametrize("invalid_mode", [0o644, 0o400])
def test_mode_invalid_pointer_is_rejected(tmp_path: Path, invalid_mode: int) -> None:
    # Given
    pointer = tmp_path / "activation.json"
    pointer.write_text("{}\n", encoding="utf-8")
    pointer.chmod(invalid_mode)

    # When / Then
    with pytest.raises(InvalidSystematicInputActivationError):
        write_systematic_input_activation(
            pointer,
            BlockedSystematicInputActivation(reason_code="no_graph", attempted_at=_NOW),
        )


def test_owner_invalid_pointer_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    pointer = tmp_path / "activation.json"
    pointer.write_text("{}\n", encoding="utf-8")
    pointer.chmod(0o600)
    owner = os.getuid()
    monkeypatch.setattr(private_file.os, "getuid", lambda: owner + 1)

    # When / Then
    with pytest.raises(InvalidSystematicInputActivationError):
        _ = load_systematic_input_activation(pointer)


def test_noncanonical_payload_is_rejected_on_read(tmp_path: Path) -> None:
    # Given
    pointer = tmp_path / "activation.json"
    blocked = BlockedSystematicInputActivation(reason_code="no_graph", attempted_at=_NOW)
    pointer.write_text(blocked.model_dump_json() + "\n", encoding="utf-8")
    pointer.chmod(0o600)

    # When / Then
    with pytest.raises(InvalidSystematicInputActivationError):
        _ = load_systematic_input_activation(pointer)
