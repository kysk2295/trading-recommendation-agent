from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

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


def current_authority() -> JsonRow:
    return {
        "schema_version": 1,
        "evidence_type": "system_current_authority",
        "observed_at": NOW.isoformat(),
        "railway_deployment_id": "deploy-1",
        "railway_code_sha256": "d" * 64,
        "railway_receipt_sha256": "e" * 64,
        "railway_source_root_sha256": "2" * 64,
        "relay_transition_id": "transition-1",
        "relay_owner_sha256": "f" * 64,
        "relay_receipt_sha256": "1" * 64,
        "relay_source_root_sha256": "3" * 64,
        "receipt_sha256": "4" * 64,
    }


def write_current_authority(system: Path) -> None:
    root = system.parent / "source_evidence"
    root.mkdir(parents=True, exist_ok=True)
    write_rows(root / "system-current-authority.v1.json", (current_authority(),))


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
