from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path

import pytest
from dashboard_system_fixtures import (
    JsonRow,
    SystemAuthorityTestSigner,
    milestones,
    operations,
    system_authority_signer,
    typed_control_receipts,
    write_current_authority,
    write_rows,
)
from typer.testing import CliRunner

import run_dashboard_publisher
from trading_agent.dashboard_system_authority_config import (
    load_system_authority_verifier,
)
from trading_agent.dashboard_system_control_receipts import AUTONOMOUS_CONTROL_FILE
from trading_agent.dashboard_system_current_authority import (
    SystemAuthorityVerifier,
    UnavailableSystemAuthorityVerifier,
)
from trading_agent.dashboard_system_evidence import MILESTONE_FILE
from trading_agent.dashboard_system_operations import OPERATIONS_FILE


def test_public_verifier_config_requires_trusted_file_and_never_accepts_private_key(
    tmp_path: Path,
) -> None:
    signer = system_authority_signer()
    valid = tmp_path / "system-authority.json"
    _write_public_config(valid, signer)

    loaded = load_system_authority_verifier(valid)
    missing = load_system_authority_verifier(tmp_path / "missing.json")
    private = tmp_path / "private.json"
    private.write_text(
        json.dumps(
            {
                **_public_config(signer),
                "ed25519_private_key_base64": "forbidden",
            }
        ),
        encoding="utf-8",
    )
    private.chmod(0o600)
    rejected_private = load_system_authority_verifier(private)
    unsafe_mode = tmp_path / "unsafe-mode.json"
    _write_public_config(unsafe_mode, signer)
    unsafe_mode.chmod(0o666)
    rejected_mode = load_system_authority_verifier(unsafe_mode)
    symlink = tmp_path / "authority-link.json"
    symlink.symlink_to(valid)
    rejected_symlink = load_system_authority_verifier(symlink)
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    output_config = outputs / "authority.json"
    _write_public_config(output_config, signer)
    rejected_output = load_system_authority_verifier(
        output_config,
        untrusted_root=outputs,
    )

    assert isinstance(loaded, SystemAuthorityVerifier)
    assert isinstance(missing, UnavailableSystemAuthorityVerifier)
    assert missing.reason == "system_current_authority_verifier_missing"
    assert isinstance(rejected_private, UnavailableSystemAuthorityVerifier)
    assert rejected_private.reason == "system_current_authority_verifier_invalid"
    assert isinstance(rejected_mode, UnavailableSystemAuthorityVerifier)
    assert rejected_mode.reason == "system_current_authority_verifier_invalid"
    assert isinstance(rejected_symlink, UnavailableSystemAuthorityVerifier)
    assert rejected_symlink.reason == "system_current_authority_verifier_invalid"
    assert isinstance(rejected_output, UnavailableSystemAuthorityVerifier)
    assert rejected_output.reason == "system_current_authority_verifier_invalid"


@pytest.mark.parametrize(
    ("config_kind", "state", "blocker"),
    [
        (
            "missing",
            "unavailable",
            "system_current_authority_verifier_missing",
        ),
        (
            "invalid",
            "unavailable",
            "system_current_authority_verifier_invalid",
        ),
        (
            "wrong",
            "corrupt",
            "system_current_authority_signature_invalid",
        ),
        (
            "output",
            "unavailable",
            "system_current_authority_verifier_invalid",
        ),
    ],
)
def test_cli_missing_invalid_or_wrong_public_key_fails_closed_without_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_kind: str,
    state: str,
    blocker: str,
) -> None:
    monkeypatch.setattr(
        run_dashboard_publisher,
        "require_current_main_authority",
        lambda: None,
    )
    now = dt.datetime.now(dt.UTC)
    signer = system_authority_signer()
    outputs = _write_system_outputs(tmp_path, signer, now)
    credentials = _write_credentials(tmp_path)
    config = outputs / "system-authority.json" if config_kind == "output" else tmp_path / "system-authority.json"
    if config_kind == "invalid":
        config.write_text("{}", encoding="utf-8")
        config.chmod(0o600)
    elif config_kind in {"wrong", "output"}:
        _write_public_config(
            config,
            (system_authority_signer(key_id=signer.verifier.key_id) if config_kind == "wrong" else signer),
        )

    result = CliRunner().invoke(
        run_dashboard_publisher.app,
        [
            "--outputs",
            str(outputs),
            "--credentials",
            str(credentials),
            "--system-authority-config",
            str(config),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["workspaces"]["system"]["state"] == state
    assert payload["workspaces"]["system"]["blocker_code"] == blocker
    assert str(config) not in result.stdout
    assert base64.b64encode(signer.public_key).decode() not in result.stdout


def test_cli_signed_system_projects_populated_without_key_or_path_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_dashboard_publisher,
        "require_current_main_authority",
        lambda: None,
    )
    now = dt.datetime.now(dt.UTC)
    signer = system_authority_signer()
    outputs = _write_system_outputs(tmp_path, signer, now)
    credentials = _write_credentials(tmp_path)
    config = tmp_path / "system-authority.json"
    _write_public_config(config, signer)

    result = CliRunner().invoke(
        run_dashboard_publisher.app,
        [
            "--outputs",
            str(outputs),
            "--credentials",
            str(credentials),
            "--system-authority-config",
            str(config),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["workspaces"]["system"]["state"] == "populated"
    assert payload["workspaces"]["system"]["blocker_code"] is None
    assert str(config) not in result.stdout
    assert base64.b64encode(signer.public_key).decode() not in result.stdout


def _write_system_outputs(
    tmp_path: Path,
    signer: SystemAuthorityTestSigner,
    now: dt.datetime,
) -> Path:
    outputs = tmp_path / "outputs"
    system = outputs / "system"
    system.mkdir(parents=True)
    milestone_rows = tuple({**row, "observed_at": now.isoformat()} for row in milestones())
    operation_rows = tuple(_current_operation(row, now) for row in operations())
    write_rows(system / MILESTONE_FILE, milestone_rows)
    write_rows(system / OPERATIONS_FILE, operation_rows)
    write_current_authority(system, signer, observed_at=now)
    write_rows(
        system / AUTONOMOUS_CONTROL_FILE,
        typed_control_receipts(now),
    )
    return outputs


def _current_operation(row: JsonRow, now: dt.datetime) -> JsonRow:
    current = {**row, "observed_at": now.isoformat()}
    if current["evidence_type"] == "launchd":
        current["process_started_at"] = (now - dt.timedelta(minutes=1)).isoformat()
    return current


def _write_credentials(tmp_path: Path) -> Path:
    path = tmp_path / "dashboard.env"
    path.write_text(
        "DASHBOARD_URL=https://example.test\nDASHBOARD_INGEST_TOKEN=fixture-value-with-adequate-length\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _write_public_config(
    path: Path,
    signer: SystemAuthorityTestSigner,
) -> None:
    path.write_text(json.dumps(_public_config(signer)), encoding="utf-8")
    path.chmod(0o644)


def _public_config(signer: SystemAuthorityTestSigner) -> dict[str, str]:
    verifier = signer.verifier
    return {
        "key_id": verifier.key_id,
        "project_id": verifier.project_id,
        "environment": verifier.environment,
        "railway_service_id": verifier.railway_service_id,
        "relay_service_id": verifier.relay_service_id,
        "ed25519_public_key_base64": base64.b64encode(signer.public_key).decode(),
    }
