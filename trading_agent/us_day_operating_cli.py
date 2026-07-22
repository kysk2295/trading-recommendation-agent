from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, assert_never

from trading_agent.paper_entry_source import load_current_orb_paper_entry
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.us_day_operating_attestation import (
    finalize_us_day_terminal,
    publish_us_day_run_terminal,
    write_us_day_evidence,
)
from trading_agent.us_day_operating_cli_contract import (
    EvidenceUsDayCommand,
    FinalizeUsDayCommand,
    PreflightUsDayCommand,
    RecoverUsDayCommand,
    parse_command,
)
from trading_agent.us_day_operating_cli_contract import (
    RunUsDayCommand as RunUsDayCommand,
)
from trading_agent.us_day_operating_cli_contract import (
    parser as parser,
)
from trading_agent.us_day_operating_cli_errors import US_DAY_OPERATIONAL_ERRORS, safe_operational_reason
from trading_agent.us_day_operating_cli_output import print_inspection, print_operating_result, print_payload
from trading_agent.us_day_operating_models import (
    UsDayOperatingRequest,
    UsDayOperatingResult,
    UsDayOperatingStatus,
)
from trading_agent.us_day_operating_runner import build_runner as build_runner
from trading_agent.us_day_operating_runner import now_utc
from trading_agent.us_day_session_inspection import (
    DefaultUsDayReadOnlyOperations,
    UsDayReadOnlyOperations,
)

type SourceLoader = Callable[[Path, dt.datetime], PaperOrderAdmissionRequest]
type Clock = Callable[[], dt.datetime]


class UsDayRunner(Protocol):
    def run(self, request: UsDayOperatingRequest) -> UsDayOperatingResult: ...


class UsDayRunnerFactory(Protocol):
    def __call__(self, command: RunUsDayCommand) -> UsDayRunner: ...


@dataclass(frozen=True, slots=True)
class UsDayCliDependencies:
    clock: Clock
    runner_factory: UsDayRunnerFactory
    source_loader: SourceLoader
    read_only_operations: UsDayReadOnlyOperations = field(default_factory=DefaultUsDayReadOnlyOperations)


DEFAULT_DEPENDENCIES = UsDayCliDependencies(now_utc, build_runner, load_current_orb_paper_entry)


def main(
    argv: Sequence[str] | None = None,
    dependencies: UsDayCliDependencies = DEFAULT_DEPENDENCIES,
) -> int:
    try:
        command = parse_command(argv)
        match command:
            case RunUsDayCommand():
                return _run(command, dependencies)
            case PreflightUsDayCommand():
                return _preflight(command, dependencies)
            case RecoverUsDayCommand():
                return _recover(command, dependencies)
            case FinalizeUsDayCommand():
                return _finalize(command, dependencies)
            case EvidenceUsDayCommand():
                return _evidence(command, dependencies.clock())
            case unreachable:
                assert_never(unreachable)
    except US_DAY_OPERATIONAL_ERRORS as error:
        print_payload({"reason": safe_operational_reason(error), "result": "blocked"})
        return 1


def _run(command: RunUsDayCommand, dependencies: UsDayCliDependencies) -> int:
    evaluated_at = dependencies.clock()
    admission = dependencies.source_loader(command.stores.watch, evaluated_at)
    request = _request(command, admission, evaluated_at)
    result = dependencies.runner_factory(command).run(request)
    if command.terminal_output is not None:
        inspection = dependencies.read_only_operations.recover(command.stores.execution)
        _ = publish_us_day_run_terminal(command, request, result, inspection)
    print_operating_result(result)
    match result.status:
        case UsDayOperatingStatus.COMPLETED:
            return 0
        case UsDayOperatingStatus.BLOCKED:
            return 1
        case UsDayOperatingStatus.INCIDENT:
            return 2
        case unreachable:
            assert_never(unreachable)


def _preflight(command: PreflightUsDayCommand, dependencies: UsDayCliDependencies) -> int:
    admission = dependencies.source_loader(command.watch_store, dependencies.clock())
    result = dependencies.read_only_operations.preflight(command.execution_store, admission)
    print_inspection(result.session, "ready" if result.admission_approved else "blocked", result.reasons)
    return 0 if result.admission_approved else 1


def _recover(command: RecoverUsDayCommand, dependencies: UsDayCliDependencies) -> int:
    inspection = dependencies.read_only_operations.recover(command.execution_store)
    print_inspection(inspection, "recovered" if not inspection.reasons else "blocked", inspection.reasons)
    return 0 if not inspection.reasons else 1


def _finalize(command: FinalizeUsDayCommand, dependencies: UsDayCliDependencies) -> int:
    inspection = dependencies.read_only_operations.recover(command.paths.execution_store)
    terminal = finalize_us_day_terminal(command, inspection)
    print_payload({"hermes_acknowledged": terminal.hermes_acknowledged, "result": terminal.status.value})
    return 0 if terminal.is_finally_reconciled else 1


def _evidence(command: EvidenceUsDayCommand, generated_at: dt.datetime) -> int:
    bundle = write_us_day_evidence(command, generated_at)
    print_payload(
        {
            "criterion_id": bundle.manifest.criterion_id,
            "operating_product_complete": bundle.report.operating_product_complete,
            "result": "built",
        }
    )
    return 0


def _request(
    command: RunUsDayCommand,
    admission: PaperOrderAdmissionRequest,
    evaluated_at: dt.datetime,
) -> UsDayOperatingRequest:
    intent = admission.candidate_intent
    material = json.dumps(
        (intent.intent_id, intent.strategy_version, intent.symbol, intent.entry_limit, evaluated_at.isoformat()),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return UsDayOperatingRequest(
        arm_request_id=command.arm_request_id,
        session_id=command.session_id,
        strategy_version=intent.strategy_version,
        order_admission=admission,
        quote_observed_at=intent.created_at,
        evaluated_at=evaluated_at,
        actionable_payload_sha256=hashlib.sha256(material.encode()).hexdigest(),
    )
