from __future__ import annotations

import copy
import datetime as dt
import inspect
import pickle
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from dashboard_execution_support import worktree_executor

import trading_agent.dashboard_execution_catalog as catalog_module
import trading_agent.dashboard_execution_identity as identity_module
from trading_agent.dashboard_agent_control_plane import AutonomousControlPlane, AutonomousPolicy
from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1, trigger_fixture
from trading_agent.dashboard_executable_binding import (
    InvalidExecutableBindingError,
    capture_native_executable,
)
from trading_agent.dashboard_execution_identity import _build_native_identity
from trading_agent.dashboard_execution_sandbox import create_production_execution_sandbox


class _AllowAuthority:
    def blocker(self, trigger: AutonomousTriggerV1, now: dt.datetime) -> str | None:
        del trigger, now
        return None


def _trigger(repository: Path) -> AutonomousTriggerV1:
    payload = trigger_fixture(now=dt.datetime.now(dt.UTC))
    environment = payload["environment_spec"]
    assert isinstance(environment, dict)
    environment["pinned_code_sha"] = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return AutonomousTriggerV1.model_validate(payload)


def _roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    task = tmp_path / "task"
    experiment = task / "experiment"
    worktree = task / "worktree"
    source = tmp_path / "source"
    for path in (experiment, worktree, source):
        path.mkdir(mode=0o700, parents=True)
    return task, experiment, worktree, source


def test_former_binders_and_production_catalog_injection_are_impossible(tmp_path: Path) -> None:
    # Given: production modules and a caller-controlled executable capture
    repository = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    captured = capture_native_executable(Path("/usr/bin/whoami"))

    # When/Then: capture confers no trust and production accepts no catalog or identity input
    assert not hasattr(identity_module, "bind_native_identity")
    assert not hasattr(identity_module, "bind_python_identity")
    assert not hasattr(identity_module, "_REGISTERED_IDENTITY_DIGESTS")
    signature = inspect.signature(create_production_execution_sandbox)
    with pytest.raises(TypeError):
        signature.bind(
            repository=repository,
            source_evidence_root=source,
            execution_id="health-broker",
            catalog=(captured,),
        )


def test_whoami_custom_copy_pickle_and_digest_forgery_fail_at_production_boundary(
    tmp_path: Path,
) -> None:
    # Given: one sealed broker sandbox and caller-created equivalent or hostile identities
    repository = Path(__file__).resolve().parents[1]
    task, _, worktree, source = _roots(tmp_path)
    sandbox = create_production_execution_sandbox(
        repository=repository,
        source_evidence_root=source,
        execution_id="health-broker",
    )
    canonical = sandbox.execution_identity
    forged = (
        _build_native_identity(capture_native_executable(Path("/usr/bin/whoami"))),
        copy.copy(canonical),
        pickle.loads(pickle.dumps(canonical)),
        replace(canonical, identity_digest="0" * 64),
    )

    # When/Then: only the exact sealed object and zero-argument template crosses the boundary
    assert subprocess.run(
        sandbox.argv(canonical.request(), task, worktree),
        cwd=worktree,
        check=False,
    ).returncode == 0
    for identity in forged:
        with pytest.raises(InvalidExecutableBindingError, match="execution_request_not_bound"):
            sandbox.argv(identity.request(), task, worktree)


def test_monkeypatch_cannot_replace_selector_captured_by_production_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: hostile replacement of every visible selector reference
    repository = Path(__file__).resolve().parents[1]
    task, _, worktree, source = _roots(tmp_path)
    hostile = _build_native_identity(capture_native_executable(Path("/usr/bin/whoami")))
    monkeypatch.setattr(catalog_module, "_select_production_execution", lambda *_: hostile)

    # When: the already sealed production closure creates its broker
    sandbox = create_production_execution_sandbox(
        repository=repository,
        source_evidence_root=source,
        execution_id="health-broker",
    )

    # Then: the canonical fixed true descriptor still runs with no hostile output
    completed = subprocess.run(
        sandbox.argv(sandbox.execution_identity.request(), task, worktree),
        cwd=worktree,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout == b""
    assert sandbox.execution_identity.executable.path == Path("/usr/bin/true")


def test_real_structured_broker_writes_only_isolated_append_only_artifacts(tmp_path: Path) -> None:
    # Given: fixed evidence, experiment, and worktree roots with a sealed research broker
    repository = Path(__file__).resolve().parents[1]
    task, experiment, worktree, source = _roots(tmp_path)
    (source / "authority.json").write_text('{"authority":"verified"}')
    trigger = _trigger(repository)
    sandbox = create_production_execution_sandbox(
        repository=repository,
        source_evidence_root=source,
        execution_id="research-broker",
    )
    identity = sandbox.execution_identity
    environment = sandbox.environment(trigger, experiment)

    # When: all three code-owned structured operations cross the real sandbox
    requests = (
        identity.broker_request("evidence-query", trigger.evidence_refs),
        identity.broker_request(
            "hypothesis-register",
            (trigger.trigger_id, trigger.agent_family_id, trigger.payload_sha256),
        ),
        identity.broker_request("experiment-run", (trigger.trigger_id,)),
    )
    assert len({request.template_digest for request in requests}) == 3
    results = tuple(
        subprocess.run(
            sandbox.argv(request, task, worktree),
            cwd=worktree,
            env=environment,
            capture_output=True,
            check=False,
        )
        for request in requests
    )

    # Then: the append-only candidate ledger and evidence/result files are the only mutations
    assert all(result.returncode == 0 for result in results), tuple(
        (result.returncode, result.stderr.decode()) for result in results
    )
    assert tuple(sorted(path.name for path in experiment.iterdir())) == (
        "evidence-query.json",
        "experiment-ledger.jsonl",
        "experiment-result.json",
    )
    assert len((experiment / "experiment-ledger.jsonl").read_text().splitlines()) == 1
    assert tuple(worktree.iterdir()) == ()
    assert (source / "authority.json").read_text() == '{"authority":"verified"}'


def test_authorized_end_to_end_brokers_emit_evidence_and_forbidden_args_launch_zero(
    tmp_path: Path,
) -> None:
    # Given: an authorized trigger and the separated test model plus production broker executor
    repository = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "authority.json").write_text('{"authority":"verified"}')
    trigger = _trigger(repository)
    executor = worktree_executor(
        repository=repository,
        environment_root=tmp_path / "environments",
        source_evidence_root=source,
    )
    plane = AutonomousControlPlane(
        state_root=tmp_path / "state",
        executor=executor,
        policy=AutonomousPolicy.permissive_for_tests(),
        authority_resolver=_AllowAuthority(),
    )

    # When: one real broker-backed task completes and one argv-bearing task reaches preflight
    completed = plane.handle(trigger)
    forbidden_payload = trigger_fixture(now=dt.datetime.now(dt.UTC))
    forbidden_environment = forbidden_payload["environment_spec"]
    assert isinstance(forbidden_environment, dict)
    forbidden_environment["pinned_code_sha"] = trigger.environment_spec.pinned_code_sha
    forbidden_environment["requested_tool_argv"] = ("/usr/bin/whoami",)
    forbidden_payload["trigger_id"] = "trigger-forbidden-broker-001"
    forbidden_payload["dedupe_key"] = "forbidden-broker-argv-001"
    forbidden = plane.handle(AutonomousTriggerV1.model_validate(forbidden_payload))

    # Then: progress/evidence/result receipts exist and forbidden argv launches no replacement
    receipts = tuple((tmp_path / "state" / "receipts").glob("*.json"))
    bodies = tuple(path.read_text() for path in receipts)
    assert completed.state == "completed"
    assert any('"kind":"progress"' in body for body in bodies)
    assert any('"kind":"evidence"' in body for body in bodies)
    assert any('"kind":"result"' in body for body in bodies)
    assert forbidden.state == "blocked"
    assert forbidden.reason == "tool_argv_forbidden"
    assert forbidden.model_processes == 0
