from __future__ import annotations

import json
from pathlib import Path

from tests.test_hermes_arm_authority_cli import OWNER, SCOPE, _fixture, _run_cli


def test_cli_status_and_revoke_keep_request_unusable(tmp_path: Path) -> None:
    # Given
    fixture = _fixture(tmp_path)
    common = (
        "--database",
        str(fixture.arm_database),
        "--repository",
        str(fixture.repository),
        "--lane-registry",
        str(fixture.lane_registry),
        "--experiment-ledger",
        str(fixture.experiment_ledger),
        "--signing-key",
        str(fixture.signing_key),
    )
    prepared = _run_cli(
        "prepare",
        *common,
        "--owner-id-hash",
        OWNER,
        "--session-id",
        SCOPE.session_id,
        "--lane-id",
        SCOPE.lane_id.value,
    )
    request_id = json.loads(prepared.stdout)["request_id"]

    # When
    status = _run_cli("status", *common, "--request-id", request_id)
    revoked = _run_cli("revoke", *common, "--owner-id-hash", OWNER, "--request-id", request_id)
    consume = _run_cli(
        "consume",
        *common,
        "--request-id",
        request_id,
        "--session-id",
        SCOPE.session_id,
        "--lane-id",
        SCOPE.lane_id.value,
        check=False,
    )

    # Then
    assert json.loads(status.stdout)["result"] == "prepared"
    assert json.loads(revoked.stdout)["result"] == "revoked"
    assert consume.returncode == 1
    assert json.loads(consume.stdout) == {"reason": "revoked", "result": "blocked"}
