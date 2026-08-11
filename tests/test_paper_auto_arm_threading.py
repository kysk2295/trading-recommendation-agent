from __future__ import annotations

from pathlib import Path

import pytest

import trading_agent.us_day_operating_runner as operating_runner
from tests.test_future_session_plan_compiler import _us_request
from tests.test_paper_auto_arm_policy import _authority
from tests.test_us_day_armed_entry import CapturingOperatingMain, _args, _current_source, _deps
from trading_agent.execution_store import ExecutionStore
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    FutureSessionPlanRequest,
    FutureSessionUsRole,
    ReadyToPrepareSessionPlan,
)
from trading_agent.hermes_arm_authority import LedgerHermesArmAuthorityConfig
from trading_agent.hermes_arm_request import (
    HermesArmAuthority,
    HermesArmFailure,
    HermesArmScope,
    InvalidHermesArmRequestError,
)
from trading_agent.paper_auto_arm_policy import PaperAutoArmPolicy, write_paper_auto_arm_policy
from trading_agent.us_day_armed_entry import main as armed_entry_main
from trading_agent.us_day_operating_cli_contract import RunUsDayCommand, parse_command


def test_explicit_auto_policy_dispatches_without_reading_manual_arm_store(tmp_path: Path) -> None:
    # Given: a current setup and an explicitly supplied standing-policy path.
    policy_path = tmp_path / "paper-auto-arm.json"
    write_paper_auto_arm_policy(policy_path, PaperAutoArmPolicy.from_authority(_authority()))
    operating = CapturingOperatingMain()

    # When: the armed observer runs once in auto-policy mode.
    exit_code = armed_entry_main(
        _args(tmp_path, "--once", "--paper-auto-arm-policy", str(policy_path)),
        _deps(_current_source, lambda _: (_ for _ in ()).throw(AssertionError), operating),
    )

    # Then: it dispatches the existing operating CLI without a reusable manual request ID.
    assert exit_code == 0
    assert len(operating.calls) == 1
    assert _option_values(tuple(operating.calls[0]), "--paper-auto-arm-policy") == (str(policy_path),)
    assert "--arm-request-id" not in operating.calls[0]


def test_operating_run_requires_exactly_one_manual_or_auto_arm(tmp_path: Path) -> None:
    # Given: the common operating run arguments.
    common = _operating_args(tmp_path)

    # When: manual and auto authorization are parsed independently.
    manual = parse_command((*common, "--arm-request-id", "a" * 64))
    automatic = parse_command((*common, "--paper-auto-arm-policy", str(tmp_path / "policy.json")))

    # Then: each mode remains distinguishable in the strict command contract.
    assert isinstance(manual, RunUsDayCommand)
    assert manual.arm_request_id == "a" * 64
    assert manual.paper_auto_arm_policy is None
    assert isinstance(automatic, RunUsDayCommand)
    assert automatic.arm_request_id is None
    assert automatic.paper_auto_arm_policy == tmp_path / "policy.json"


def test_future_us_payload_threads_policy_only_when_explicitly_requested(tmp_path: Path) -> None:
    # Given: otherwise identical future-session requests with and without a policy.
    manual_request = _compiled_request(tmp_path / "manual")
    policy_path = (tmp_path / "auto" / "policy.json").absolute()
    automatic_request = _compiled_request(tmp_path / "auto").model_copy(update={"paper_auto_arm_policy": policy_path})

    # When: both plans are compiled.
    manual = compile_future_session_plan(manual_request)
    automatic = compile_future_session_plan(automatic_request)
    assert isinstance(manual, ReadyToPrepareSessionPlan)
    assert isinstance(automatic, ReadyToPrepareSessionPlan)
    manual_arm = next(job for job in manual.jobs if job.role is FutureSessionUsRole.US_DAY_ARM_OBSERVER)
    automatic_arm = next(job for job in automatic.jobs if job.role is FutureSessionUsRole.US_DAY_ARM_OBSERVER)

    # Then: only the explicit request gains the auto-policy observer option and source binding.
    assert "--paper-auto-arm-policy" not in manual_arm.command
    assert _option_values(automatic_arm.command, "--paper-auto-arm-policy") == (str(policy_path),)
    assert _option_values(automatic_arm.command, "--authority-repository") == (
        str(automatic_request.authority_repository),
    )
    assert policy_path in automatic_arm.source_paths


@pytest.mark.parametrize("failure", (HermesArmFailure.COMMIT_MISMATCH, HermesArmFailure.DIRTY_COMMIT))
def test_frozen_authority_failure_blocks_before_credentials_or_operating(
    tmp_path: Path,
    monkeypatch,
    failure: HermesArmFailure,
) -> None:
    # Given: an initialized local ledger and auto command whose frozen runtime is stale.
    with ExecutionStore(tmp_path / "execution.sqlite3").writer():
        pass
    command = parse_command((*_operating_args(tmp_path), "--paper-auto-arm-policy", str(tmp_path / "policy.json")))
    assert isinstance(command, RunUsDayCommand)
    credentials_accessed = False

    class Resolver:
        def __init__(self, _config: LedgerHermesArmAuthorityConfig) -> None:
            pass

        def resolve(self, scope: HermesArmScope) -> HermesArmAuthority:
            return _authority().model_copy(update={"scope": scope})

    def credentials():
        nonlocal credentials_accessed
        credentials_accessed = True
        raise AssertionError

    def mismatch(_repository: Path, _commit: str) -> None:
        raise InvalidHermesArmRequestError(failure)

    monkeypatch.setattr(operating_runner, "LedgerHermesArmAuthorityResolver", Resolver)
    monkeypatch.setattr(operating_runner, "require_current_clean_main", lambda _repository, _commit: None)
    monkeypatch.setattr(operating_runner, "require_frozen_commit", mismatch)
    monkeypatch.setattr(operating_runner, "load_alpaca_paper_credentials", credentials)

    # When / Then: commit verification fails before credentials or coordinator construction.
    with pytest.raises(InvalidHermesArmRequestError) as blocked:
        operating_runner.build_runner(command)
    assert blocked.value.reason is failure
    assert not credentials_accessed


def _compiled_request(tmp_path: Path) -> FutureSessionPlanRequest:
    from tests.test_forward_runtime_readiness_cli import _runtime, _stores

    tmp_path.mkdir(mode=0o700)
    runtime, required, head = _runtime(tmp_path)
    lane, experiment, execution = _stores(tmp_path, code_version=head)
    return _us_request(
        tmp_path,
        runtime=runtime,
        head=head,
        required=required,
        lane=lane,
        experiment=experiment,
        execution=execution,
    )


def _operating_args(tmp_path: Path) -> tuple[str, ...]:
    return (
        "run",
        "--arm-database",
        str(tmp_path / "arm.sqlite3"),
        "--delivery-database",
        str(tmp_path / "delivery.sqlite3"),
        "--execution-database",
        str(tmp_path / "execution.sqlite3"),
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--lane-registry",
        str(tmp_path / "lane.sqlite3"),
        "--repository",
        str(tmp_path),
        "--session-id",
        "XNYS-2026-07-14",
        "--signing-key",
        str(tmp_path / "signing.env"),
        "--watch-database",
        str(tmp_path / "watch.sqlite3"),
    )


def _option_values(command: tuple[str, ...], option: str) -> tuple[str, ...]:
    return tuple(command[index + 1] for index, value in enumerate(command[:-1]) if value == option)
