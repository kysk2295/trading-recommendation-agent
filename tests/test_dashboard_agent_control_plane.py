from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path

from dashboard_execution_support import worktree_executor

from trading_agent.dashboard_agent_control_plane import (
    AutonomousControlPlane,
    AutonomousPolicy,
)
from trading_agent.dashboard_autonomous_executor_contract import ExecutionResult
from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1, trigger_fixture


class _FakeExecutor:
    def __init__(self, state: str = "completed") -> None:
        self.launches = 0
        self.state = state

    def preflight(self, trigger: AutonomousTriggerV1) -> str | None:
        del trigger
        return None

    def execute(self, trigger: AutonomousTriggerV1, task_id: str) -> ExecutionResult:
        self.launches += 1
        return ExecutionResult(
            state="completed" if self.state == "completed" else "failed",
            result_summary="candidate evidence recorded",
            result_sha256="a" * 64,
            evidence_sha256=("b" * 64,),
            cleanup_completed=True,
            process_started=True,
            worktree_clean=True,
        )


class _AllowAuthority:
    def blocker(self, trigger: AutonomousTriggerV1, now: dt.datetime) -> str | None:
        del trigger, now
        return None


def test_authorized_trigger_and_duplicates_launch_one_process(tmp_path: Path) -> None:
    # Given: a durable local control plane and one typed trigger
    now = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    trigger = AutonomousTriggerV1.model_validate(trigger_fixture(now=now))
    executor = _FakeExecutor()
    plane = AutonomousControlPlane(
        state_root=tmp_path / "state",
        executor=executor,
        policy=AutonomousPolicy.permissive_for_tests(),
        authority_resolver=_AllowAuthority(),
    )

    # When: the authorized event is delivered three times
    first = plane.handle(trigger, now=now)
    duplicate_one = plane.handle(trigger, now=now)
    duplicate_two = plane.handle(trigger, now=now)

    # Then: CAS preserves one paid-process claim and immutable receipts
    assert first.state == "completed"
    assert duplicate_one.state == "completed"
    assert duplicate_two.state == "completed"
    assert executor.launches == 1
    assert first.claim_created
    assert not duplicate_one.claim_created
    assert len(tuple((tmp_path / "state" / "receipts").glob("*.json"))) >= 5
    assert all(
        os.stat(path, follow_symlinks=False).st_mode & 0o777 == 0o600
        for path in (tmp_path / "state").rglob("*.json")
    )


def test_stale_and_budget_blockers_launch_zero_and_append_one_receipt(tmp_path: Path) -> None:
    # Given: a trigger outside freshness and a zero-token policy
    observed = dt.datetime(2026, 7, 26, 6, tzinfo=dt.UTC)
    trigger = AutonomousTriggerV1.model_validate(trigger_fixture(now=observed))
    executor = _FakeExecutor()
    plane = AutonomousControlPlane(
        state_root=tmp_path / "state",
        executor=executor,
        policy=AutonomousPolicy(
            max_trigger_age_seconds=60,
            max_daily_tokens_per_family=0,
            max_daily_cost_microusd_per_family=0,
            cooldown_seconds=0,
            max_global_concurrency=1,
            max_family_concurrency=1,
            rolling_failure_window_seconds=3_600,
            max_rolling_failures=1,
        ),
        authority_resolver=_AllowAuthority(),
    )

    # When: policy evaluates the stale trigger
    outcome = plane.handle(trigger, now=observed + dt.timedelta(minutes=2))
    replay = plane.handle(trigger, now=observed + dt.timedelta(minutes=3))

    # Then: it records exactly one typed blocker without a process
    assert outcome.state == "blocked"
    assert outcome.reason == "trigger_stale"
    assert replay.reason == "trigger_stale"
    assert executor.launches == 0
    receipts = tuple((tmp_path / "state" / "receipts").glob("*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["reason"] == "trigger_stale"


def test_fake_hermes_runs_in_clean_pinned_worktree_and_cleans_up(tmp_path: Path) -> None:
    # Given: the current pinned revision and an argv-only fake Hermes executable
    repository = Path(__file__).resolve().parents[1]
    code_sha = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    before = subprocess.run(
        ("git", "-C", str(repository), "status", "--porcelain=v1", "--untracked-files=all"),
        check=True,
        capture_output=True,
    ).stdout
    payload = trigger_fixture(now=dt.datetime.now(dt.UTC))
    environment = payload["environment_spec"]
    assert isinstance(environment, dict)
    environment["pinned_code_sha"] = code_sha
    trigger = AutonomousTriggerV1.model_validate(payload)
    executor = worktree_executor(
        repository=repository,
        environment_root=tmp_path / "environments",
        source_evidence_root=tmp_path / "authority",
    )
    (tmp_path / "authority").mkdir(mode=0o700)

    # When: one separate autonomous task session runs
    result = executor.execute(trigger, "f" * 32)

    # Then: it starts once, stays clean, removes its worktree, and leaves integration unchanged
    after = subprocess.run(
        ("git", "-C", str(repository), "status", "--porcelain=v1", "--untracked-files=all"),
        check=True,
        capture_output=True,
    ).stdout
    assert result.state == "completed"
    assert result.process_started
    assert result.worktree_clean
    assert result.cleanup_completed
    assert before == after
    assert not (tmp_path / "environments" / ("f" * 32)).exists()


def test_budget_cooldown_and_failure_gates_never_launch_replacement(tmp_path: Path) -> None:
    # Given: one completed claim reserves the family budget and cooldown
    now = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    first_payload = trigger_fixture(now=now)
    first = AutonomousTriggerV1.model_validate(first_payload)
    executor = _FakeExecutor()
    plane = AutonomousControlPlane(
        state_root=tmp_path / "budget",
        executor=executor,
        policy=AutonomousPolicy(
            max_trigger_age_seconds=3_600,
            max_daily_tokens_per_family=first.budget_envelope.max_tokens,
            max_daily_cost_microusd_per_family=first.budget_envelope.max_cost_microusd,
            cooldown_seconds=300,
            max_global_concurrency=1,
            max_family_concurrency=1,
            rolling_failure_window_seconds=3_600,
            max_rolling_failures=1,
        ),
        authority_resolver=_AllowAuthority(),
    )
    assert plane.handle(first, now=now).state == "completed"
    second_payload = trigger_fixture(now=now + dt.timedelta(seconds=1))
    second_payload["dedupe_key"] = "new-data-source-receipt-002"
    second_payload["trigger_id"] = "trigger-new-data-002"
    second = AutonomousTriggerV1.model_validate(second_payload)

    # When: a distinct paid trigger arrives after the reservation
    budget_outcome = plane.handle(second, now=now + dt.timedelta(seconds=1))

    # Then: budget wins before cooldown and no replacement launches
    assert budget_outcome.reason == "family_token_budget_exhausted"
    assert executor.launches == 1

    # Given: a failed task has consumed the one permitted rolling failure
    failed_executor = _FakeExecutor(state="failed")
    failure_plane = AutonomousControlPlane(
        state_root=tmp_path / "failures",
        executor=failed_executor,
        policy=AutonomousPolicy(
            max_trigger_age_seconds=3_600,
            max_daily_tokens_per_family=1_000_000,
            max_daily_cost_microusd_per_family=100_000_000,
            cooldown_seconds=0,
            max_global_concurrency=1,
            max_family_concurrency=1,
            rolling_failure_window_seconds=3_600,
            max_rolling_failures=1,
        ),
        authority_resolver=_AllowAuthority(),
    )
    assert failure_plane.handle(first, now=now).state == "failed"

    # When: a distinct trigger arrives inside that rolling window
    failure_outcome = failure_plane.handle(second, now=now + dt.timedelta(seconds=1))

    # Then: it is blocked with zero paid retry
    assert failure_outcome.reason == "rolling_failure_budget_exhausted"
    assert failed_executor.launches == 1


def test_failure_budget_isolated_per_agent_family(tmp_path: Path) -> None:
    # Given: one family has exhausted its rolling failure budget
    now = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    first = AutonomousTriggerV1.model_validate(trigger_fixture(now=now))
    second = first.model_copy(
        update={
            "agent_family_id": "swing_trading",
            "dedupe_key": "new-data-swing-source-receipt-001",
            "trigger_id": "trigger-swing-new-data-001",
        }
    )
    executor = _FakeExecutor(state="failed")
    plane = AutonomousControlPlane(
        state_root=tmp_path / "state",
        executor=executor,
        policy=AutonomousPolicy(
            max_trigger_age_seconds=3_600,
            max_daily_tokens_per_family=1_000_000,
            max_daily_cost_microusd_per_family=100_000_000,
            cooldown_seconds=0,
            max_global_concurrency=1,
            max_family_concurrency=1,
            rolling_failure_window_seconds=3_600,
            max_rolling_failures=1,
        ),
        authority_resolver=_AllowAuthority(),
    )
    assert plane.handle(first, now=now).state == "failed"

    # When: a different family receives an authorized trigger inside the same window
    outcome = plane.handle(second, now=now + dt.timedelta(seconds=1))

    # Then: the unrelated family retains its own execution and failure budget
    assert outcome.state == "failed"
    assert executor.launches == 2
