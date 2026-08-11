from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import run_future_session_materialize
from tests.test_future_session_us_materializer import _authority_files
from trading_agent.future_session_coordinator_models import (
    UsFutureSessionActivationReceipt,
)
from trading_agent.future_session_materialize_cli_parser import (
    build_future_session_parser,
)


def test_coordinate_cli_reports_bad_input_and_canonical_waiting(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: one malformed request and one canonical request with stale scheduler authority.
    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(b"{\n")
    invalid.chmod(0o600)
    ready_root = tmp_path / "ready"
    ready_root.mkdir()
    _request, _plan, request_path, _plan_path = _authority_files(ready_root)
    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    request_payload["scheduler_main_sha"] = "f" * 40
    request_path.write_text(
        json.dumps(request_payload, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    request_path.chmod(0o600)
    plan_path = tmp_path / "coordinator-plan.json"
    launch_agents = tmp_path / "Library" / "LaunchAgents"

    # When
    bad_code = run_future_session_materialize.main(
        (
            "coordinate",
            "--request",
            str(invalid),
            "--plan",
            str(plan_path),
            "--launch-agents-dir",
            str(launch_agents),
        )
    )
    bad_payload = json.loads(capsys.readouterr().out)
    waiting_code = run_future_session_materialize.main(
        (
            "coordinate",
            "--request",
            str(request_path),
            "--plan",
            str(plan_path),
            "--launch-agents-dir",
            str(launch_agents),
        )
    )
    waiting_payload = json.loads(capsys.readouterr().out)

    # Then
    assert "coordinate" in build_future_session_parser().format_help()
    assert bad_code == 2
    assert bad_payload == {"reason": "invalid_request", "result": "blocked"}
    assert waiting_code == 0
    assert waiting_payload["result"] == "waiting_authority"
    assert waiting_payload["preparation"] == "not_prepared"
    assert waiting_payload["activation"] == "not_activated"
    assert not plan_path.exists()


def test_activation_receipt_rejects_schema_coercion() -> None:
    # Given / When / Then: an external string cannot be coerced into a schema integer.
    with pytest.raises(ValidationError):
        UsFutureSessionActivationReceipt.model_validate(
            {
                "schema_version": "2",
                "labels": ["ai.trading-agent.example"],
                "manifest_sha256": "0" * 64,
                "result": "activated",
            }
        )
