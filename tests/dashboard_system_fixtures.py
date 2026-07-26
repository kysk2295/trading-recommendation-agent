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
        "expected_code_sha256": "d" * 64,
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
    if mutation == "relay_stale":
        relay["observed_at"] = (NOW - dt.timedelta(minutes=6)).isoformat()
    return launchd, stage, railway, relay


def control_receipts(mutation: str = "") -> tuple[JsonRow, ...]:
    components = (
        "scheduler",
        "trigger",
        "claim",
        "budget",
        "cooldown",
        "concurrency",
        "failure_budget",
        "worktree",
        "cleanup",
    )
    rows: list[JsonRow] = []
    for index, component in enumerate(components):
        state = "passed"
        blocker_code = None
        if mutation == "cleanup_failed" and component == "cleanup":
            state, blocker_code = "failed", "autonomous_cleanup_failed"
        if mutation == "budget_blocked" and component == "budget":
            state, blocker_code = "blocked", "family_token_budget_exhausted"
        rows.append(
            {
                "schema_version": 1,
                "evidence_type": "autonomous_control",
                "evidence_id": f"autonomous-{component}",
                "component": component,
                "agent_family_id": "systematic_quant",
                "trigger_type": "new_data",
                "observed_at": NOW.isoformat(),
                "state": state,
                "blocker_code": blocker_code,
                "receipt_sha256": f"{index + 2:064x}",
            }
        )
    return tuple(rows)
