from __future__ import annotations

import json
from pathlib import Path

from tests.test_run_us_day_operating_session import CapturingRunnerFactory, StaticRunner
from tests.test_us_day_acceptance_evidence import _clean_repository
from tests.us_day_operating_fixtures import AT, admission, readiness
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.us_day_acceptance_evidence import UsDaySessionTerminal, UsDayTerminalStatus
from trading_agent.us_day_operating_cli import UsDayCliDependencies, main
from trading_agent.us_day_operating_models import (
    UsDayOperatingResult,
    UsDayOperatingStatus,
    UsDayOperatingTransition,
)
from trading_agent.us_day_session_inspection import UsDayPreflightInspection, UsDaySessionInspection


class StaticReadOnlyOperations:
    __slots__ = ("inspection",)

    def __init__(self, inspection: UsDaySessionInspection) -> None:
        self.inspection = inspection

    def preflight(
        self,
        execution_store: Path,
        request: PaperOrderAdmissionRequest,
    ) -> UsDayPreflightInspection:
        return UsDayPreflightInspection(self.inspection, True, ())

    def recover(self, execution_store: Path) -> UsDaySessionInspection:
        return self.inspection


def test_run_writes_reconciled_blocked_terminal_from_read_only_recovery(tmp_path: Path, capsys) -> None:
    repository = _clean_repository(tmp_path)
    source_path = Path("outputs/source/blocked-session.json")
    source = repository / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("blocked-session", encoding="utf-8")
    order_admission = admission()
    inspection = UsDaySessionInspection(readiness(order_admission, 3).broker_state, AT, True, True, True, ())
    blocked = UsDayOperatingResult(
        UsDayOperatingStatus.BLOCKED,
        (UsDayOperatingTransition.HERMES_RESULT_PROJECTED,),
        ("stale_quote",),
        "XNYS-2026-07-14",
        order_admission.candidate_intent.strategy_version,
        order_admission.candidate_intent.intent_id,
        None,
        None,
        "b" * 64,
    )
    terminal_path = repository / "outputs/acceptance/us_day/sessions/blocked.json"
    dependencies = UsDayCliDependencies(
        lambda: AT,
        CapturingRunnerFactory(StaticRunner(blocked)),
        lambda _path, _at: order_admission,
        StaticReadOnlyOperations(inspection),
    )

    exit_code = main(
        (*_run_arguments(repository), "--source-artifact", str(source_path), "--terminal-output", str(terminal_path)),
        dependencies,
    )

    terminal = UsDaySessionTerminal.model_validate_json(terminal_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["result"] == "blocked"
    assert terminal.status is UsDayTerminalStatus.BLOCKED
    assert terminal.reasons == ("stale_quote",)
    assert terminal.is_finally_reconciled is True


def test_run_rejects_terminal_output_without_immutable_source(tmp_path: Path, capsys) -> None:
    repository = _clean_repository(tmp_path)
    order_admission = admission()
    inspection = UsDaySessionInspection(readiness(order_admission, 3).broker_state, AT, True, True, True, ())
    dependencies = UsDayCliDependencies(
        lambda: AT,
        CapturingRunnerFactory(StaticRunner(_blocked_result())),
        lambda _path, _at: order_admission,
        StaticReadOnlyOperations(inspection),
    )

    exit_code = main(
        (*_run_arguments(repository), "--terminal-output", str(repository / "outputs/terminal.json")),
        dependencies,
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "InvalidUsDayCliCommandError"
    assert not (repository / "outputs/terminal.json").exists()


def _blocked_result() -> UsDayOperatingResult:
    order_admission = admission()
    return UsDayOperatingResult(
        UsDayOperatingStatus.BLOCKED,
        (UsDayOperatingTransition.HERMES_RESULT_PROJECTED,),
        ("blocked",),
        "XNYS-2026-07-14",
        order_admission.candidate_intent.strategy_version,
        order_admission.candidate_intent.intent_id,
        None,
        None,
        "b" * 64,
    )


def _run_arguments(repository: Path) -> tuple[str, ...]:
    return (
        "run",
        "--arm-database",
        str(repository / "outputs/arm.sqlite3"),
        "--arm-request-id",
        "a" * 64,
        "--delivery-database",
        str(repository / "outputs/delivery.sqlite3"),
        "--execution-database",
        str(repository / "outputs/execution.sqlite3"),
        "--experiment-ledger",
        str(repository / "outputs/experiment.sqlite3"),
        "--lane-registry",
        str(repository / "outputs/lane.sqlite3"),
        "--repository",
        str(repository),
        "--session-id",
        "XNYS-2026-07-14",
        "--signing-key",
        str(repository / "outputs/arm.env"),
        "--watch-database",
        str(repository / "outputs/watch.sqlite3"),
    )
