from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from dashboard_system_fixtures import (
    NOW,
    JsonRow,
    JsonValue,
    control_receipts,
    current_authority,
    milestones,
    operations,
    system_authority_signer,
    system_root,
    typed_control_receipts,
    write_current_authority,
    write_rows,
)

from trading_agent.dashboard_projection_system import project_system
from trading_agent.dashboard_system_control_receipts import AUTONOMOUS_CONTROL_FILE
from trading_agent.dashboard_system_current_authority import (
    SYSTEM_CURRENT_AUTHORITY_FILE,
    SYSTEM_CURRENT_AUTHORITY_ROOT,
    SystemAuthorityVerifier,
    read_system_current_authority,
)
from trading_agent.dashboard_system_evidence import MILESTONE_FILE
from trading_agent.dashboard_system_operations import OPERATIONS_FILE


@pytest.mark.parametrize(
    ("mutation", "blocker"),
    [
        ("railway_deployment", "railway_current_deployment_mismatch"),
        ("railway_code", "railway_current_code_mismatch"),
        ("railway_receipt", "railway_current_receipt_mismatch"),
        ("railway_source", "railway_current_source_mismatch"),
        ("relay_transition", "relay_current_transition_mismatch"),
        ("relay_owner", "relay_current_owner_mismatch"),
        ("relay_receipt", "relay_current_receipt_mismatch"),
        ("relay_source", "relay_current_source_mismatch"),
    ],
)
def test_recomputed_operation_hashes_cannot_replace_current_authority(
    tmp_path: Path,
    mutation: str,
    blocker: str,
) -> None:
    root = system_root(tmp_path)
    rows = list(operations())
    index = 2 if mutation.startswith("railway") else 3
    field = {
        "railway_deployment": "deployment_id",
        "railway_code": "code_sha256",
        "railway_receipt": "receipt_sha256",
        "railway_source": "source_root_sha256",
        "relay_transition": "transition_id",
        "relay_owner": "owner_sha256",
        "relay_receipt": "receipt_sha256",
        "relay_source": "source_root_sha256",
    }[mutation]
    rows[index] = {
        **rows[index],
        field: "spoofed-current" if field.endswith("_id") else "7" * 64,
    }
    verifier = _write_complete(root, tuple(rows), control_receipts())

    projection = project_system(
        tmp_path / "outputs",
        now=NOW,
        authority_verifier=verifier,
    )

    assert projection.workspace.state in {"blocked", "corrupt", "unavailable"}
    assert projection.workspace.blocker_code == blocker


def test_matching_attacker_authority_in_operations_root_is_not_independent(
    tmp_path: Path,
) -> None:
    root = system_root(tmp_path)
    rows = list(operations())
    rows[2] = {
        **rows[2],
        "code_sha256": "7" * 64,
        "receipt_sha256": "8" * 64,
    }
    attacker: JsonRow = {"attacker": "recomputed"}
    write_rows(root / MILESTONE_FILE, milestones())
    write_rows(root / OPERATIONS_FILE, tuple(rows))
    write_rows(root / "system-current-authority.v1.json", (attacker,))
    write_rows(root / AUTONOMOUS_CONTROL_FILE, control_receipts())

    projection = project_system(
        tmp_path / "outputs",
        now=NOW,
        authority_verifier=system_authority_signer().verifier,
    )

    assert projection.workspace.state == "unavailable"
    assert projection.workspace.blocker_code == "system_current_authority_missing"


def test_recomputed_operation_and_independent_authority_without_trusted_verifier_is_unavailable(
    tmp_path: Path,
) -> None:
    root = system_root(tmp_path)
    rows = list(operations())
    rows[2] = {
        **rows[2],
        "code_sha256": "7" * 64,
        "receipt_sha256": "8" * 64,
    }
    attacker: JsonRow = {"attacker": "recomputed"}
    write_rows(root / MILESTONE_FILE, milestones())
    write_rows(root / OPERATIONS_FILE, tuple(rows))
    authority_root = root.parent / "source_evidence"
    authority_root.mkdir()
    write_rows(
        authority_root / "system-current-authority.v1.json",
        (attacker,),
    )
    write_rows(root / AUTONOMOUS_CONTROL_FILE, control_receipts())

    projection = project_system(tmp_path / "outputs", now=NOW)

    assert projection.workspace.state == "unavailable"
    assert (
        projection.workspace.blocker_code
        == "system_current_authority_verifier_missing"
    )


@pytest.mark.parametrize(
    "mutation",
    ["code_sha256", "source_receipt_sha256", "source_root_sha256"],
)
def test_recomputed_authority_payload_without_new_signature_is_rejected(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = system_root(tmp_path)
    signer = system_authority_signer()
    rows = list(current_authority(signer))
    rows[0] = {**rows[0], mutation: "7" * 64}
    _write_authority_rows(root, tuple(rows))

    result = _read_authority(root, signer.verifier)

    assert result == "system_current_authority_signature_invalid"


def test_recomputed_operations_and_authority_without_private_key_never_populate(
    tmp_path: Path,
) -> None:
    root = system_root(tmp_path)
    signer = system_authority_signer()
    operation_rows = list(operations())
    operation_rows[2] = {
        **operation_rows[2],
        "code_sha256": "7" * 64,
        "receipt_sha256": "8" * 64,
    }
    authority_rows = list(current_authority(signer))
    authority_rows[0] = {
        **authority_rows[0],
        "code_sha256": "7" * 64,
        "source_receipt_sha256": "8" * 64,
    }
    write_rows(root / MILESTONE_FILE, milestones())
    write_rows(root / OPERATIONS_FILE, tuple(operation_rows))
    _write_authority_rows(root, tuple(authority_rows))
    write_rows(root / AUTONOMOUS_CONTROL_FILE, control_receipts())

    projection = project_system(
        tmp_path / "outputs",
        now=NOW,
        authority_verifier=signer.verifier,
    )

    assert projection.workspace.state == "corrupt"
    assert (
        projection.workspace.blocker_code
        == "system_current_authority_signature_invalid"
    )


def test_authority_signed_by_wrong_key_is_rejected(tmp_path: Path) -> None:
    root = system_root(tmp_path)
    trusted = system_authority_signer()
    attacker = system_authority_signer(key_id=trusted.verifier.key_id)
    _write_authority_rows(root, current_authority(attacker))

    result = _read_authority(root, trusted.verifier)

    assert result == "system_current_authority_signature_invalid"


def test_receipt_public_key_substitution_is_rejected(tmp_path: Path) -> None:
    root = system_root(tmp_path)
    signer = system_authority_signer()
    rows = list(current_authority(signer))
    rows[0] = {**rows[0], "public_key": "attacker-controlled"}
    _write_authority_rows(root, tuple(rows))

    result = _read_authority(root, signer.verifier)

    assert result == "system_current_authority_invalid"


def test_railway_and_relay_signatures_cannot_be_swapped(tmp_path: Path) -> None:
    root = system_root(tmp_path)
    signer = system_authority_signer()
    railway, relay = current_authority(signer)
    rows = (
        {**railway, "signature": relay["signature"]},
        {**relay, "signature": railway["signature"]},
    )
    _write_authority_rows(root, rows)

    result = _read_authority(root, signer.verifier)

    assert result == "system_current_authority_signature_invalid"


def test_lower_signed_sequence_is_rejected_after_newer_authority(
    tmp_path: Path,
) -> None:
    root = system_root(tmp_path)
    signer = system_authority_signer()
    _write_authority_rows(root, current_authority(signer, sequence=2))
    first = _read_authority(root, signer.verifier)
    _write_authority_rows(root, current_authority(signer, sequence=1))

    replay = _read_authority(root, signer.verifier)

    assert isinstance(first, tuple)
    assert replay == "system_current_authority_replay"


@pytest.mark.parametrize(
    ("observed_at", "blocker"),
    [
        (NOW - dt.timedelta(minutes=6), "system_current_authority_stale"),
        (NOW + dt.timedelta(minutes=6), "system_current_authority_future"),
    ],
)
def test_signed_authority_freshness_is_bounded(
    tmp_path: Path,
    observed_at: dt.datetime,
    blocker: str,
) -> None:
    root = system_root(tmp_path)
    signer = system_authority_signer()
    _write_authority_rows(
        root,
        current_authority(signer, observed_at=observed_at),
    )

    result = _read_authority(root, signer.verifier)

    assert result == blocker


@pytest.mark.parametrize("field", ["project_id", "environment", "service_id"])
def test_signed_but_unpinned_identity_is_rejected(
    tmp_path: Path,
    field: str,
) -> None:
    root = system_root(tmp_path)
    signer = system_authority_signer()
    railway_changes: JsonRow = {field: "attacker-scope"}
    _write_authority_rows(
        root,
        current_authority(signer, railway_changes=railway_changes),
    )

    result = _read_authority(root, signer.verifier)

    assert result == "system_current_authority_identity_mismatch"


def test_missing_independent_current_authority_is_unavailable(tmp_path: Path) -> None:
    root = system_root(tmp_path)
    write_rows(root / MILESTONE_FILE, milestones())
    write_rows(root / OPERATIONS_FILE, operations())
    write_rows(root / AUTONOMOUS_CONTROL_FILE, control_receipts())

    projection = project_system(
        tmp_path / "outputs",
        now=NOW,
        authority_verifier=system_authority_signer().verifier,
    )

    assert projection.workspace.state == "unavailable"
    assert projection.workspace.blocker_code == "system_current_authority_missing"


def test_component_specific_autonomous_receipts_require_linked_semantics(
    tmp_path: Path,
) -> None:
    root = system_root(tmp_path)
    verifier = _write_complete(root, operations(), typed_control_receipts())

    projection = project_system(
        tmp_path / "outputs",
        now=NOW,
        authority_verifier=verifier,
    )

    assert projection.workspace.state == "populated"
    assert projection.workspace.blocker_code is None


def test_running_cleanup_and_missing_budget_fail_closed_precisely(
    tmp_path: Path,
) -> None:
    root = system_root(tmp_path)
    running = list(typed_control_receipts())
    running[-1] = {
        **running[-1],
        "state": "running",
        "terminal_receipt_sha256": None,
    }
    verifier = _write_complete(root, operations(), tuple(running))
    running_projection = project_system(
        tmp_path / "outputs",
        now=NOW,
        authority_verifier=verifier,
    )

    assert running_projection.workspace.state == "blocked"
    assert running_projection.workspace.blocker_code == "autonomous_cleanup_incomplete"

    write_rows(
        root / AUTONOMOUS_CONTROL_FILE,
        tuple(row for row in typed_control_receipts() if row["component"] != "budget"),
    )
    missing_projection = project_system(
        tmp_path / "outputs",
        now=NOW,
        authority_verifier=verifier,
    )

    assert missing_projection.workspace.state == "unavailable"
    assert missing_projection.workspace.blocker_code == "autonomous_budget_missing"


@pytest.mark.parametrize(
    ("component", "changes", "blocker"),
    [
        ("scheduler", {"state": "completed"}, "autonomous_control_invalid"),
        ("scheduler", {"schedule_id": None}, "autonomous_control_invalid"),
        (
            "trigger",
            {"previous_receipt_sha256": "f" * 64},
            "autonomous_control_link_mismatch",
        ),
        ("budget", {"tokens_remaining": 1001}, "autonomous_control_invalid"),
        (
            "cleanup",
            {"state": "completed", "terminal_receipt_sha256": None},
            "autonomous_control_invalid",
        ),
    ],
)
def test_component_specific_autonomous_semantics_fail_closed(
    tmp_path: Path,
    component: str,
    changes: dict[str, JsonValue],
    blocker: str,
) -> None:
    root = system_root(tmp_path)
    rows = list(typed_control_receipts())
    index = next(i for i, row in enumerate(rows) if row["component"] == component)
    rows[index] = {**rows[index], **changes}
    verifier = _write_complete(root, operations(), tuple(rows))

    projection = project_system(
        tmp_path / "outputs",
        now=NOW,
        authority_verifier=verifier,
    )

    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == blocker


@pytest.mark.parametrize(
    ("mutation", "blocker"),
    [
        ("trigger_conflict", "autonomous_control_trigger_conflict"),
        ("continued_after_budget_block", "autonomous_control_terminal_violation"),
    ],
)
def test_autonomous_cross_receipt_semantics_reject_disconnected_chains(
    tmp_path: Path,
    mutation: str,
    blocker: str,
) -> None:
    root = system_root(tmp_path)
    rows = list(typed_control_receipts())
    if mutation == "trigger_conflict":
        rows[4] = {**rows[4], "trigger_type": "market_event"}
    else:
        rows[3] = {
            **rows[3],
            "state": "blocked",
            "blocker_code": "family_token_budget_exhausted",
        }
    verifier = _write_complete(root, operations(), tuple(rows))

    projection = project_system(
        tmp_path / "outputs",
        now=NOW,
        authority_verifier=verifier,
    )

    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == blocker


def _write_complete(
    root: Path,
    operation_rows: tuple[dict[str, JsonValue], ...],
    autonomous_rows: tuple[dict[str, JsonValue], ...],
) -> SystemAuthorityVerifier:
    write_rows(root / MILESTONE_FILE, milestones())
    write_rows(root / OPERATIONS_FILE, operation_rows)
    verifier = write_current_authority(root)
    write_rows(root / AUTONOMOUS_CONTROL_FILE, autonomous_rows)
    return verifier


def _write_authority_rows(
    system: Path,
    rows: tuple[dict[str, JsonValue], ...],
) -> None:
    authority_root = system.parent / SYSTEM_CURRENT_AUTHORITY_ROOT
    authority_root.mkdir(parents=True, exist_ok=True)
    write_rows(authority_root / SYSTEM_CURRENT_AUTHORITY_FILE, rows)


def _read_authority(
    system: Path,
    verifier: SystemAuthorityVerifier,
) -> tuple[object, ...] | str:
    return read_system_current_authority(
        system.parent / SYSTEM_CURRENT_AUTHORITY_ROOT / SYSTEM_CURRENT_AUTHORITY_FILE,
        NOW,
        verifier=verifier,
    )
