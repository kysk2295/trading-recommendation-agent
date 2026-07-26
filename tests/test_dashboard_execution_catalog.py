from __future__ import annotations

import copy
import datetime as dt
import inspect
import pickle
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from dashboard_execution_support import worktree_executor

import trading_agent.dashboard_execution_catalog as catalog_module
import trading_agent.dashboard_production_execution_boundary as boundary_module
from trading_agent.dashboard_agent_control_plane import AutonomousControlPlane, AutonomousPolicy
from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1, trigger_fixture
from trading_agent.dashboard_executable_binding import (
    InvalidExecutableBindingError,
    capture_native_executable,
)
from trading_agent.dashboard_execution_catalog import ProductionExecutionId
from trading_agent.dashboard_execution_identity import _build_native_identity
from trading_agent.dashboard_production_execution_boundary import (
    create_production_execution_boundary,
)


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


def _boundary(
    repository: Path,
    source: Path,
    execution_id: ProductionExecutionId,
):
    return create_production_execution_boundary(
        repository=repository,
        source_evidence_root=source,
        execution_id=execution_id,
    )


def test_production_closures_contain_no_catalog_entry_or_identity_collection(tmp_path: Path) -> None:
    # Given: the production factory and one returned fixed-ID boundary
    repository = Path(__file__).resolve().parents[1]
    _, _, _, source = _roots(tmp_path)
    boundary = _boundary(repository, source, ProductionExecutionId.HEALTH_BROKER)

    # When: closure cells on the factory and methods are inspected directly
    functions = (create_production_execution_boundary, boundary.blocker, boundary.run_model)
    cells = tuple(cell.cell_contents for function in functions for cell in (function.__closure__ or ()))

    # Then: authority retains no mutable catalog, entry collection, identity, or descriptor
    assert not any(isinstance(value, (dict, list, set, tuple)) for value in cells)
    assert not any("Catalog" in type(value).__name__ or "Selection" in type(value).__name__ for value in cells)
    assert not hasattr(catalog_module, "_SealedProductionCatalog")
    assert not hasattr(catalog_module, "_SealedProductionSelection")


def test_former_binders_reconstruction_and_unsealed_injection_are_impossible(tmp_path: Path) -> None:
    # Given: hostile native identities reconstructed through every ordinary copy path
    repository = Path(__file__).resolve().parents[1]
    _, _, _, source = _roots(tmp_path)
    whoami = _build_native_identity(capture_native_executable(Path("/usr/bin/whoami")))
    trusted = catalog_module._build_expected_execution(
        repository,
        ProductionExecutionId.HEALTH_BROKER,
    )
    mutated_request = trusted.request()
    object.__setattr__(mutated_request, "argv", ("/usr/bin/whoami",))
    forged = (
        whoami,
        copy.copy(whoami),
        copy.deepcopy(whoami),
        pickle.loads(pickle.dumps(whoami)),
        replace(whoami, identity_digest="0" * 64),
    )

    # When/Then: production has no binder, identity/request input, or caller catalog parameter
    assert not hasattr(catalog_module, "bind_native_identity")
    assert not hasattr(catalog_module, "bind_python_identity")
    signature = inspect.signature(create_production_execution_boundary)
    for identity in forged:
        with pytest.raises(TypeError):
            signature.bind(
                repository=repository,
                source_evidence_root=source,
                execution_id=ProductionExecutionId.HEALTH_BROKER,
                execution_identity=identity,
            )
    boundary = _boundary(repository, source, ProductionExecutionId.HEALTH_BROKER)
    assert not hasattr(boundary, "execution_identity")
    assert not hasattr(boundary, "argv")
    with pytest.raises(TypeError):
        inspect.signature(boundary.run_model).bind(
            _trigger(repository),
            tmp_path,
            tmp_path,
            tmp_path,
            "",
            30,
            request=mutated_request,
        )
    with pytest.raises(InvalidExecutableBindingError, match="production_execution_id_forbidden"):
        create_production_execution_boundary(
            repository=repository,
            source_evidence_root=source,
            execution_id=cast(ProductionExecutionId, "health-broker"),
        )


def test_object_mutation_and_visible_function_monkeypatch_cannot_change_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a slots-only boundary prevalidated before hostile visible-global mutation
    repository = Path(__file__).resolve().parents[1]
    task, experiment, worktree, source = _roots(tmp_path)
    trigger = _trigger(repository)
    boundary = _boundary(repository, source, ProductionExecutionId.HEALTH_BROKER)
    assert boundary.blocker(trigger) is None
    hostile = _build_native_identity(capture_native_executable(Path("/usr/bin/whoami")))
    with pytest.raises(AttributeError):
        object.__setattr__(boundary, "execution_identity", hostile)
    monkeypatch.setattr(catalog_module, "_build_expected_execution", lambda *_: hostile)
    monkeypatch.setattr(boundary_module, "_build_expected_execution", lambda *_: hostile)
    monkeypatch.setattr(catalog_module, "capture_native_executable", lambda *_: hostile)
    monkeypatch.setattr(
        boundary_module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail(f"visible subprocess.run used: {args!r} {kwargs!r}"),
    )

    # When: the final process boundary independently rederives after prevalidation
    completed = boundary.run_model(trigger, task, experiment, worktree, "", 30)

    # Then: captured helpers still execute only the literal fixed health broker
    assert completed.returncode == 0
    assert completed.stdout == b""


def test_real_hermes_probe_health_and_structured_research_brokers(tmp_path: Path) -> None:
    # Given: fixed roots and three separately rederived production boundaries
    repository = Path(__file__).resolve().parents[1]
    task, experiment, worktree, source = _roots(tmp_path)
    (source / "authority.json").write_text('{"authority":"verified"}')
    trigger = _trigger(repository)
    probe = _boundary(repository, source, ProductionExecutionId.HERMES_PROBE)
    health = _boundary(repository, source, ProductionExecutionId.HEALTH_BROKER)
    broker = _boundary(repository, source, ProductionExecutionId.RESEARCH_BROKER)

    # When: real probe/health and three code-owned structured operations execute
    probe_result = probe.run_model(trigger, task, experiment, worktree, "", 30)
    health_result = health.run_model(trigger, task, experiment, worktree, "", 30)
    broker_results = (
        broker.run_broker(
            trigger,
            task,
            experiment,
            worktree,
            "evidence-query",
            trigger.evidence_refs,
            30,
        ),
        broker.run_broker(
            trigger,
            task,
            experiment,
            worktree,
            "hypothesis-register",
            (trigger.trigger_id, trigger.agent_family_id, trigger.payload_sha256),
            30,
        ),
        broker.run_broker(
            trigger,
            task,
            experiment,
            worktree,
            "experiment-run",
            (trigger.trigger_id,),
            30,
        ),
    )

    # Then: only isolated append-only artifacts change and no shell or caller path executes
    assert probe_result.returncode == 0 and b"Hermes Agent" in probe_result.stdout
    assert health_result.returncode == 0 and health_result.stdout == b""
    assert all(result.returncode == 0 for result in broker_results)
    assert tuple(sorted(path.name for path in experiment.iterdir())) == (
        "evidence-query.json",
        "experiment-ledger.jsonl",
        "experiment-result.json",
    )
    assert len((experiment / "experiment-ledger.jsonl").read_text().splitlines()) == 1
    assert tuple(worktree.iterdir()) == ()
    assert (source / "authority.json").read_text() == '{"authority":"verified"}'


def test_authorized_end_to_end_receipts_and_forbidden_args_launch_zero(tmp_path: Path) -> None:
    # Given: an authorized trigger and separate test-model/production-broker boundaries
    repository = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "authority.json").write_text('{"authority":"verified"}')
    trigger = _trigger(repository)
    plane = AutonomousControlPlane(
        state_root=tmp_path / "state",
        executor=worktree_executor(
            repository=repository,
            environment_root=tmp_path / "environments",
            source_evidence_root=source,
        ),
        policy=AutonomousPolicy.permissive_for_tests(),
        authority_resolver=_AllowAuthority(),
    )

    # When: one broker-backed task completes and one argv-bearing task reaches preflight
    completed = plane.handle(trigger)
    forbidden_payload = trigger_fixture(now=dt.datetime.now(dt.UTC))
    forbidden_environment = forbidden_payload["environment_spec"]
    assert isinstance(forbidden_environment, dict)
    forbidden_environment["pinned_code_sha"] = trigger.environment_spec.pinned_code_sha
    forbidden_environment["requested_tool_argv"] = ("/usr/bin/whoami",)
    forbidden_payload["trigger_id"] = "trigger-forbidden-broker-001"
    forbidden_payload["dedupe_key"] = "forbidden-broker-argv-001"
    forbidden = plane.handle(AutonomousTriggerV1.model_validate(forbidden_payload))

    # Then: progress/evidence/result are durable and forbidden argv launches no replacement
    bodies = tuple(path.read_text() for path in (tmp_path / "state" / "receipts").glob("*.json"))
    assert completed.state == "completed"
    assert any('"kind":"progress"' in body for body in bodies)
    assert any('"kind":"evidence"' in body for body in bodies)
    assert any('"kind":"result"' in body for body in bodies)
    assert forbidden.state == "blocked"
    assert forbidden.reason == "tool_argv_forbidden"
    assert forbidden.model_processes == 0
