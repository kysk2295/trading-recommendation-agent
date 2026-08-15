from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_forward_runtime_readiness_cli import _git
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.future_session_execution_incident import (
    FutureSessionExecutionIncidentReceipt,
    canonical_execution_incident_json,
    project_execution_incident,
)
from trading_agent.future_session_kr_payload import (
    KrRestartableRunnerSpec,
    render_kr_restartable_runner,
)
from trading_agent.launchd_one_shot_runner import OneShotRunnerSpec, render_persistent_runner


@pytest.mark.parametrize("market", ("us", "kr"))
def test_runtime_authority_failure_projects_one_dashboard_incident(
    tmp_path: Path,
    market: str,
) -> None:
    manifest = tmp_path / "preparation-manifest.json"
    manifest.write_text("fixture\n", encoding="utf-8")
    manifest.chmod(0o600)
    receipt = tmp_path / "receipt.json"
    incident_dir = tmp_path / "execution-incidents"
    incident_dir.mkdir(mode=0o700)
    role = "us_orb_watcher" if market == "us" else "kr_supervisor"
    incident = incident_dir / f"{role}.json"
    wrapper = tmp_path / "runner.zsh"
    plist = tmp_path / "job.plist"
    plist.touch(mode=0o600)
    dashboard_now = dt.datetime.now(dt.UTC)
    target = (dashboard_now - dt.timedelta(days=1)).date()
    target_compact = target.strftime("%Y%m%d")
    request_sha256 = "a" * 64
    plan_sha256 = "b" * 64
    runtime_commit_sha = "c" * 40
    scheduler_main_sha = runtime_commit_sha
    if market == "us":
        repository = tmp_path / "runtime"
        repository.mkdir(mode=0o700)
        _git(repository, "init", "--quiet")
        _git(repository, "config", "user.email", "incident@example.invalid")
        _git(repository, "config", "user.name", "Incident Test")
        (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
        _git(repository, "add", "tracked.txt")
        _git(repository, "commit", "--quiet", "-m", "fixture")
        runtime_commit_sha = _git(repository, "rev-parse", "HEAD")
        scheduler_main_sha = runtime_commit_sha
        wrapper_text = render_persistent_runner(
            OneShotRunnerSpec(
                label=f"ai.trading-agent.us-orb-watcher-{target_compact}",
                run_at=dt.datetime(1970, 1, 1, tzinfo=dt.UTC),
                receipt=receipt,
                command=("/usr/bin/true",),
                expires_at=dt.datetime(2100, 1, 1, tzinfo=dt.UTC),
                persistent_plist=plist,
                authority_repository=repository,
                source_commit=scheduler_main_sha,
                role=role,
                request_sha256=request_sha256,
                plan_sha256=plan_sha256,
                runtime_commit_sha=runtime_commit_sha,
                runtime_attestation_sha256="d" * 64,
                preparation_manifest=manifest,
                authority_mode="frozen_runtime",
                market="us",
                target_session=target,
                execution_incident_receipt=incident,
            )
        )
        (repository / "untracked.py").write_text("raise RuntimeError\n", encoding="utf-8")
    else:
        wrapper_text = render_kr_restartable_runner(
            KrRestartableRunnerSpec(
                label=f"ai.trading-agent.future-session.kr.{target.isoformat()}.supervisor",
                run_epoch=0,
                expires_epoch=4102444800,
                receipt=receipt,
                command=("/bin/zsh", "-c", "exit 78"),
                persistent_plist=plist,
                target_session=target,
                incident_receipt=incident,
                manifest=manifest,
                request_sha256=request_sha256,
                plan_sha256=plan_sha256,
                scheduler_main_sha=scheduler_main_sha,
                runtime_commit_sha=runtime_commit_sha,
            )
        )
    wrapper.write_text(wrapper_text, encoding="utf-8")
    wrapper.chmod(0o700)

    completed = subprocess.run((str(wrapper),), check=False, capture_output=True, text=True)
    original_incident = incident.read_bytes()
    receipt.unlink()
    replayed = subprocess.run((str(wrapper),), check=False, capture_output=True, text=True)
    execution_incident = FutureSessionExecutionIncidentReceipt.model_validate_json(incident.read_bytes())
    delivery = tmp_path / "outputs" / "hermes" / "delivery.sqlite3"
    project_execution_incident(execution_incident, delivery)
    delivery.parent.chmod(0o700)
    snapshot = collect_dashboard_snapshot_v2(
        tmp_path / "outputs",
        now=dashboard_now,
    )

    assert completed.returncode == 78
    assert replayed.returncode == 78
    assert incident.read_bytes() == original_incident
    assert canonical_execution_incident_json(execution_incident).encode() == incident.read_bytes()
    terminal = next(
        item
        for item in snapshot.workspaces.markets.items
        if item.item_id == f"session_terminal.{market}_equities.{target_compact}"
    )
    assert terminal.state == "blocked"
    assert terminal.value == "incident"
    assert snapshot.workspaces.markets.state == "blocked"
    assert json.loads(receipt.read_text(encoding="utf-8"))["result"] == "blocked"
