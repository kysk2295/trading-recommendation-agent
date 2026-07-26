from __future__ import annotations

import base64
import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from trading_agent.dashboard_system_current_authority import (
    RailwayCurrentAuthority,
    RelayCurrentAuthority,
    SystemAuthorityVerifier,
    canonical_authority_payload,
)
from trading_agent.dashboard_system_evidence import MILESTONE_IDS

type JsonScalar = str | int | None
type JsonValue = JsonScalar | dict[str, str]
type JsonRow = dict[str, JsonValue]
NOW = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)


def system_root(tmp_path: Path) -> Path:
    root = tmp_path / "outputs/system"
    root.mkdir(parents=True)
    return root


def write_rows(path: Path, rows: tuple[JsonRow, ...]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    path.chmod(0o600)


def milestones() -> tuple[JsonRow, ...]:
    return tuple(
        {
            "schema_version": 2,
            "evidence_type": "milestone",
            "epoch_id": "release-1",
            "milestone_id": milestone,
            "status": "passed",
            "observed_at": NOW.isoformat(),
            "code_sha256": f"{index + 1:064x}",
            "result_code": "stage_passed",
        }
        for index, milestone in enumerate(MILESTONE_IDS)
    )


def operations(mutation: str = "") -> tuple[JsonRow, ...]:
    launchd: JsonRow = {
        "schema_version": 2,
        "evidence_type": "launchd",
        "evidence_id": "launchd-publisher",
        "job_id": "dashboard-publisher",
        "operational_alias": "delivery",
        "schedule": "event_driven",
        "observed_at": NOW.isoformat(),
        "status": "running",
        "pid": 321,
        "process_started_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
        "last_exit_code": None,
        "exit_observed_at": None,
        "terminal_receipt_sha256": None,
        "receipt_sha256": "a" * 64,
    }
    stage: JsonRow = {
        "schema_version": 2,
        "evidence_type": "stage",
        "evidence_id": "stage-release",
        "run_id": "release-1",
        "stage_id": "release-verification",
        "observed_at": NOW.isoformat(),
        "outcome": "passed",
        "result_code": "stage_passed",
        "terminal_receipt_sha256": "b" * 64,
        "receipt_sha256": "c" * 64,
    }
    railway: JsonRow = {
        "schema_version": 2,
        "evidence_type": "railway",
        "evidence_id": "railway-dashboard",
        "deployment_id": "deploy-1",
        "observed_at": NOW.isoformat(),
        "code_sha256": "d" * 64,
        "source_root_sha256": "2" * 64,
        "health": "healthy",
        "service_count": 1,
        "receipt_sha256": "e" * 64,
    }
    relay: JsonRow = {
        "schema_version": 2,
        "evidence_type": "relay",
        "evidence_id": "relay-publisher",
        "transition_id": "transition-1",
        "observed_at": NOW.isoformat(),
        "state": "connected",
        "owner_sha256": "f" * 64,
        "source_root_sha256": "3" * 64,
        "receipt_sha256": "1" * 64,
    }
    if mutation == "stale_pid":
        launchd["observed_at"] = (NOW - dt.timedelta(minutes=6)).isoformat()
        launchd["process_started_at"] = (NOW - dt.timedelta(minutes=7)).isoformat()
    if mutation == "nonzero_exit":
        launchd.update(
            status="failed",
            pid=None,
            process_started_at=None,
            last_exit_code=7,
            exit_observed_at=NOW.isoformat(),
        )
    if mutation == "unverified_exit":
        launchd.update(
            status="exited",
            pid=None,
            process_started_at=None,
            last_exit_code=0,
            exit_observed_at=NOW.isoformat(),
        )
    if mutation == "stage_failed":
        stage.update(outcome="failed", result_code="stage_failed")
    if mutation == "stage_terminal_missing":
        stage["terminal_receipt_sha256"] = None
    if mutation == "railway_unreachable":
        railway["health"] = "unreachable"
    if mutation == "railway_stale":
        railway["observed_at"] = (NOW - dt.timedelta(minutes=6)).isoformat()
    if mutation == "relay_stale":
        relay["observed_at"] = (NOW - dt.timedelta(minutes=6)).isoformat()
    return launchd, stage, railway, relay


@dataclass(frozen=True, slots=True)
class SystemAuthorityTestSigner:
    verifier: SystemAuthorityVerifier
    _private_key: Ed25519PrivateKey = field(repr=False)

    def sign(self, row: JsonRow) -> JsonRow:
        unsigned = {**row, "signature": "A" * 86}
        serialized = json.dumps(unsigned)
        authority = (
            RailwayCurrentAuthority.model_validate_json(serialized)
            if row["kind"] == "railway_deployment"
            else RelayCurrentAuthority.model_validate_json(serialized)
        )
        signature = self._private_key.sign(canonical_authority_payload(authority))
        return {
            **row,
            "signature": base64.urlsafe_b64encode(signature).decode().rstrip("="),
        }


def system_authority_signer(
    *,
    key_id: str = "test-authority-key-1",
) -> SystemAuthorityTestSigner:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    verifier = SystemAuthorityVerifier.from_public_bytes(
        key_id=key_id,
        project_id="trading-recommendation-agent",
        environment="test",
        railway_service_id="dashboard",
        relay_service_id="publisher-relay",
        public_key=public_key,
    )
    return SystemAuthorityTestSigner(verifier=verifier, _private_key=private_key)


def current_authority(
    signer: SystemAuthorityTestSigner,
    *,
    railway_changes: JsonRow | None = None,
    relay_changes: JsonRow | None = None,
    observed_at: dt.datetime = NOW,
    sequence: int = 1,
) -> tuple[JsonRow, JsonRow]:
    common: JsonRow = {
        "schema_version": 2,
        "evidence_type": "system_current_authority",
        "key_id": signer.verifier.key_id,
        "project_id": signer.verifier.project_id,
        "environment": signer.verifier.environment,
        "observed_at": observed_at.isoformat(),
        "sequence": sequence,
    }
    railway: JsonRow = {
        **common,
        "kind": "railway_deployment",
        "service_id": signer.verifier.railway_service_id,
        "deployment_id": "deploy-1",
        "code_sha256": "d" * 64,
        "source_receipt_sha256": "e" * 64,
        "source_root_sha256": "2" * 64,
        "nonce": f"railway-nonce-{sequence:08d}",
        **(railway_changes or {}),
    }
    relay: JsonRow = {
        **common,
        "kind": "relay_socket",
        "service_id": signer.verifier.relay_service_id,
        "transition_id": "transition-1",
        "socket_owner_sha256": "f" * 64,
        "source_receipt_sha256": "1" * 64,
        "source_root_sha256": "3" * 64,
        "nonce": f"relay-nonce-{sequence:08d}",
        **(relay_changes or {}),
    }
    return signer.sign(railway), signer.sign(relay)


def write_current_authority(
    system: Path,
    signer: SystemAuthorityTestSigner | None = None,
    *,
    railway_changes: JsonRow | None = None,
    relay_changes: JsonRow | None = None,
    observed_at: dt.datetime = NOW,
    sequence: int = 1,
) -> SystemAuthorityVerifier:
    authority_signer = system_authority_signer() if signer is None else signer
    root = system.parent / "source_evidence"
    root.mkdir(parents=True, exist_ok=True)
    write_rows(
        root / "system-current-authority.v2.jsonl",
        current_authority(
            authority_signer,
            railway_changes=railway_changes,
            relay_changes=relay_changes,
            observed_at=observed_at,
            sequence=sequence,
        ),
    )
    return authority_signer.verifier


def control_receipts(mutation: str = "") -> tuple[JsonRow, ...]:
    rows = [dict(row) for row in typed_control_receipts()]
    if mutation == "cleanup_failed":
        rows[-1].update(
            state="failed",
            blocker_code="autonomous_cleanup_failed",
        )
    if mutation == "budget_blocked":
        rows[3].update(
            state="blocked",
            blocker_code="family_token_budget_exhausted",
        )
        for row in rows[4:8]:
            row.update(
                state="blocked",
                blocker_code="family_token_budget_exhausted",
            )
        rows[4]["cooldown_until"] = (NOW + dt.timedelta(minutes=1)).isoformat()
        rows[5]["active_count"] = rows[5]["max_concurrency"]
        rows[6]["failure_count"] = rows[6]["max_failures"]
    return tuple(rows)


def typed_control_receipts(now: dt.datetime = NOW) -> tuple[JsonRow, ...]:
    fields: tuple[dict[str, JsonValue], ...] = (
        {"state": "scheduled", "schedule_id": "schedule-1"},
        {"state": "accepted", "trigger_id": "trigger-1"},
        {"state": "claimed", "claim_id": "claim-1"},
        {"state": "authorized", "token_budget": 1000, "tokens_remaining": 900},
        {
            "state": "passed",
            "cooldown_until": (now - dt.timedelta(seconds=1)).isoformat(),
        },
        {"state": "passed", "active_count": 1, "max_concurrency": 2},
        {"state": "passed", "failure_count": 0, "max_failures": 2},
        {"state": "authorized", "isolation_receipt_sha256": "8" * 64},
        {"state": "completed", "terminal_receipt_sha256": "9" * 64},
    )
    rows: list[JsonRow] = []
    previous: str | None = None
    for index, (component, specific) in enumerate(
        zip(
            (
                "scheduler",
                "trigger",
                "claim",
                "budget",
                "cooldown",
                "concurrency",
                "failure_budget",
                "worktree",
                "cleanup",
            ),
            fields,
            strict=True,
        )
    ):
        receipt_sha256 = f"{index + 10:064x}"
        row: JsonRow = {
            "schema_version": 2,
            "evidence_type": "autonomous_control",
            "evidence_id": f"autonomous-{component}",
            "component": component,
            "run_id": "autonomous-run-1",
            "agent_family_id": "systematic_quant",
            "trigger_type": "new_data",
            "observed_at": now.isoformat(),
            "blocker_code": None,
            "previous_receipt_sha256": previous,
            "receipt_sha256": receipt_sha256,
            **specific,
        }
        rows.append(row)
        previous = receipt_sha256
    return tuple(rows)
