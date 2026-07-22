from __future__ import annotations

import json
from pathlib import Path

from tests.us_day_operating_fixtures import AT, admission
from trading_agent.us_day_operating_cli import (
    RunUsDayCommand,
    UsDayCliDependencies,
    build_runner,
    main,
)
from trading_agent.us_day_operating_models import (
    UsDayOperatingRequest,
    UsDayOperatingResult,
    UsDayOperatingStatus,
    UsDayOperatingTransition,
)


class StaticRunner:
    __slots__ = ("result",)

    def __init__(self, result: UsDayOperatingResult) -> None:
        self.result = result

    def run(self, request: UsDayOperatingRequest) -> UsDayOperatingResult:
        assert request.session_id == self.result.session_id
        return self.result


class CapturingRunnerFactory:
    __slots__ = ("command", "request", "runner")

    def __init__(self, runner: StaticRunner) -> None:
        self.runner = runner
        self.command: RunUsDayCommand | None = None
        self.request: UsDayOperatingRequest | None = None

    def __call__(self, command: RunUsDayCommand, request: UsDayOperatingRequest) -> StaticRunner:
        self.command = command
        self.request = request
        return self.runner


def test_cli_run_projects_redacted_completed_result(tmp_path: Path, capsys) -> None:
    order_admission = admission()
    result = UsDayOperatingResult(
        UsDayOperatingStatus.COMPLETED,
        (
            UsDayOperatingTransition.ACTIONABLE,
            UsDayOperatingTransition.ENTRY_ACKNOWLEDGED,
            UsDayOperatingTransition.FLAT,
            UsDayOperatingTransition.RECONCILED,
            UsDayOperatingTransition.HERMES_RESULT_PROJECTED,
        ),
        (),
        "XNYS-2026-07-14",
        order_admission.candidate_intent.strategy_version,
        order_admission.candidate_intent.intent_id,
        None,
        "actionable-delivery",
        "outcome-delivery",
    )
    factory = CapturingRunnerFactory(StaticRunner(result))
    dependencies = UsDayCliDependencies(lambda: AT, factory, lambda _path, _at: order_admission)

    exit_code = main(_arguments(tmp_path), dependencies)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["result"] == "completed"
    assert payload["session_id"] == "XNYS-2026-07-14"
    assert "account" not in payload
    assert factory.request is not None
    assert factory.request.quote_observed_at == order_admission.candidate_intent.created_at


def test_cli_blocks_uninitialized_execution_store_before_credentials(tmp_path: Path, capsys) -> None:
    dependencies = UsDayCliDependencies(lambda: AT, build_runner, lambda _path, _at: admission())

    exit_code = main(_arguments(tmp_path), dependencies)

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "reason": "uninitialized_execution_store",
        "result": "blocked",
    }


def _arguments(tmp_path: Path) -> list[str]:
    return [
        "run",
        "--arm-database",
        str(tmp_path / "arm.sqlite3"),
        "--arm-request-id",
        "a" * 64,
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
        str(tmp_path / "arm.env"),
        "--watch-database",
        str(tmp_path / "watch.sqlite3"),
    ]
