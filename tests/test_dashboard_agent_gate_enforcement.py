from __future__ import annotations

import datetime as dt
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from dashboard_execution_support import worktree_executor

from trading_agent.dashboard_agent_control_plane import (
    AutonomousControlPlane,
    AutonomousPolicy,
    FaultSeam,
)
from trading_agent.dashboard_autonomous_executor_contract import ExecutionResult
from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1, trigger_fixture


class _CountingExecutor:
    def __init__(self, *, release: threading.Event | None = None) -> None:
        self.launches = 0
        self._release = release
        self.started = threading.Event()

    def preflight(self, trigger: AutonomousTriggerV1) -> str | None:
        del trigger
        return None

    def execute(self, trigger: AutonomousTriggerV1, task_id: str) -> ExecutionResult:
        del trigger, task_id
        self.launches += 1
        self.started.set()
        if self._release is not None:
            assert self._release.wait(timeout=5)
        return ExecutionResult(
            state="completed",
            result_summary="candidate",
            result_sha256="a" * 64,
            evidence_sha256=("b" * 64,),
            cleanup_completed=True,
            process_started=True,
            worktree_clean=True,
        )


class _MissingAuthority:
    def blocker(self, trigger: AutonomousTriggerV1, now: dt.datetime) -> str | None:
        del trigger, now
        return "source_authority_missing"


class _AllowAuthority:
    def blocker(self, trigger: AutonomousTriggerV1, now: dt.datetime) -> str | None:
        del trigger, now
        return None


class _ExplodingExecutor(_CountingExecutor):
    def execute(self, trigger: AutonomousTriggerV1, task_id: str) -> ExecutionResult:
        del trigger, task_id
        self.launches += 1
        raise OSError("injected process launch fault")


def _trigger(now: dt.datetime, suffix: int = 1) -> AutonomousTriggerV1:
    payload = trigger_fixture(now=now)
    payload["trigger_id"] = f"trigger-new-data-{suffix:03d}"
    payload["dedupe_key"] = f"new-data-source-receipt-{suffix:03d}"
    return AutonomousTriggerV1.model_validate(payload)


def test_missing_persisted_authority_appends_blocker_and_launches_zero(tmp_path: Path) -> None:
    # Given: a structurally valid trigger with no persisted source authority
    now = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    executor = _CountingExecutor()
    plane = AutonomousControlPlane(
        state_root=tmp_path / "state",
        executor=executor,
        policy=AutonomousPolicy.permissive_for_tests(),
        authority_resolver=_MissingAuthority(),
    )

    # When: the trigger reaches the production authorization boundary
    outcome = plane.handle(_trigger(now), now=now)

    # Then: one durable typed blocker exists and no model process launches
    assert outcome.state == "blocked"
    assert outcome.reason == "source_authority_missing"
    assert executor.launches == 0
    assert len(tuple((tmp_path / "state" / "receipts").glob("*.json"))) == 1


@pytest.mark.parametrize(
    ("gate", "expected_reason"),
    [
        ("global", "global_concurrency_exhausted"),
        ("family", "family_concurrency_exhausted"),
        ("tokens", "family_token_budget_exhausted"),
        ("cost", "family_cost_budget_exhausted"),
    ],
)
def test_distinct_dedupe_keys_cannot_oversubscribe_atomic_caps(
    tmp_path: Path,
    gate: str,
    expected_reason: str,
) -> None:
    # Given: sixteen simultaneous distinct keys and one atomic gate with capacity one
    now = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    start = threading.Barrier(16)
    release = threading.Event()
    executor = _CountingExecutor(release=release)
    policy = AutonomousPolicy(
        max_trigger_age_seconds=3_600,
        max_daily_tokens_per_family=10_000 if gate == "tokens" else 1_000_000,
        max_daily_cost_microusd_per_family=1_000_000 if gate == "cost" else 100_000_000,
        cooldown_seconds=0,
        max_global_concurrency=1 if gate == "global" else 16,
        max_family_concurrency=1 if gate == "family" else 16,
        rolling_failure_window_seconds=3_600,
        max_rolling_failures=8,
    )

    def run(index: int) -> str:
        start.wait(timeout=5)
        outcome = AutonomousControlPlane(
            state_root=tmp_path / "state",
            executor=executor,
            policy=policy,
            authority_resolver=_AllowAuthority(),
        ).handle(_trigger(now, index + 1), now=now)
        return outcome.state if outcome.state == "completed" else str(outcome.reason)

    # When: every distinct key evaluates and claims concurrently
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(run, index) for index in range(16)]
        assert executor.started.wait(timeout=5)
        release.set()
        outcomes = tuple(future.result(timeout=10) for future in futures)

    # Then: the durable admission lock permits one process and one reservation
    assert executor.launches == 1
    assert outcomes.count("completed") == 1
    assert outcomes.count(expected_reason) == 15


def test_post_claim_process_exception_is_terminal_and_never_relaunched(tmp_path: Path) -> None:
    # Given: a claimed task whose process launch seam raises
    now = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    executor = _ExplodingExecutor()
    plane = AutonomousControlPlane(
        state_root=tmp_path / "state",
        executor=executor,
        policy=AutonomousPolicy.permissive_for_tests(),
        authority_resolver=_AllowAuthority(),
    )

    # When: the same task is delivered twice
    first = plane.handle(_trigger(now), now=now)
    replay = plane.handle(_trigger(now), now=now)

    # Then: the first attempt is terminal failed and replay launches nothing
    assert first.state == "failed"
    assert replay.state in {"failed", "uncertain"}
    assert executor.launches == 1
    receipts = tuple((tmp_path / "state" / "receipts").glob("*.json"))
    assert any('"state":"failed"' in path.read_text() for path in receipts)


@pytest.mark.parametrize(
    ("attempt", "reason"),
    [
        ("read", "read_path_forbidden"),
        ("write", "write_path_forbidden"),
        ("tool", "tool_argv_forbidden"),
        ("network", "network_policy_forbidden"),
    ],
)
def test_forbidden_isolation_attempt_blocks_before_process_or_mutation(
    tmp_path: Path,
    attempt: str,
    reason: str,
) -> None:
    # Given: a typed trigger requesting one forbidden host capability
    now = dt.datetime.now(dt.UTC)
    repository = Path(__file__).resolve().parents[1]
    payload = trigger_fixture(now=now)
    environment = payload["environment_spec"]
    assert isinstance(environment, dict)
    environment["pinned_code_sha"] = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    canary = tmp_path / "forbidden-canary"
    canary.write_text("unchanged")
    if attempt == "read":
        environment["requested_read_paths"] = (str(canary),)
    elif attempt == "write":
        environment["requested_write_paths"] = (str(canary),)
    elif attempt == "tool":
        environment["requested_tool_argv"] = ("/bin/sh", "-c", "touch forbidden")
    else:
        environment["network_policy"] = "public_read_only"
    trigger = AutonomousTriggerV1.model_validate(payload)
    source_root = tmp_path / "authorities"
    source_root.mkdir(mode=0o700)
    executor = worktree_executor(
        repository=repository,
        environment_root=tmp_path / "environments",
        source_evidence_root=source_root,
    )
    plane = AutonomousControlPlane(
        state_root=tmp_path / "state",
        executor=executor,
        policy=AutonomousPolicy.permissive_for_tests(),
        authority_resolver=_AllowAuthority(),
    )

    # When: the trigger reaches executor preflight
    outcome = plane.handle(trigger, now=now)

    # Then: one typed blocker is durable and no process or host mutation occurs
    assert outcome.state == "blocked"
    assert outcome.reason == reason
    assert outcome.model_processes == 0
    assert canary.read_text() == "unchanged"
    assert not (tmp_path / "environments").exists()


@pytest.mark.parametrize(
    ("seam", "expected"),
    [
        ("authorization", "blocked"),
        ("claim", "blocked"),
        ("process_launch", "failed"),
        ("tool_step", "failed"),
        ("result_persistence", "uncertain"),
        ("event_send", "uncertain"),
        ("cleanup", "uncertain"),
    ],
)
def test_each_fault_seam_persists_terminal_without_retry(
    tmp_path: Path,
    seam: FaultSeam,
    expected: str,
) -> None:
    # Given: one injected fault at a named control-plane seam
    now = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    executor = _CountingExecutor()
    plane = AutonomousControlPlane(
        state_root=tmp_path / seam,
        executor=executor,
        policy=AutonomousPolicy.permissive_for_tests(),
        authority_resolver=_AllowAuthority(),
        fault_seam=seam,
    )

    # When: the trigger and its duplicate are handled
    outcome = plane.handle(_trigger(now), now=now)
    replay = plane.handle(_trigger(now), now=now)

    # Then: the seam has one durable terminal and never relaunches
    assert outcome.state == expected
    assert replay.state in {"blocked", "failed", "uncertain"}
    assert executor.launches <= 1
