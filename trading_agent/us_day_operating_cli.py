from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, assert_never

from trading_agent.alpaca_paper_config import load_alpaca_paper_credentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_arm_authority import LedgerHermesArmAuthorityConfig, LedgerHermesArmAuthorityResolver
from trading_agent.hermes_arm_gateway import HermesArmGateway, HermesArmGatewayConfig
from trading_agent.hermes_arm_signing import (
    DEFAULT_HERMES_ARM_SIGNING_KEY_PATH,
    HermesArmSigner,
    load_hermes_arm_signing_key,
)
from trading_agent.hermes_arm_store import HermesArmStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.paper_entry_source import load_current_orb_paper_entry
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.us_day_operating_arm import StrategyBoundHermesArmConsumer
from trading_agent.us_day_operating_cli_errors import (
    US_DAY_OPERATIONAL_ERRORS,
    UninitializedUsDayExecutionStoreError,
    safe_operational_reason,
)
from trading_agent.us_day_operating_coordinator import UsDayOperatingCoordinator, UsDayOperatingCoordinatorConfig
from trading_agent.us_day_operating_models import UsDayOperatingRequest, UsDayOperatingResult, UsDayOperatingStatus

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type SourceLoader = Callable[[Path, dt.datetime], PaperOrderAdmissionRequest]
type Clock = Callable[[], dt.datetime]


class UsDayRunner(Protocol):
    def run(self, request: UsDayOperatingRequest) -> UsDayOperatingResult: ...


class UsDayRunnerFactory(Protocol):
    def __call__(self, command: RunUsDayCommand, request: UsDayOperatingRequest) -> UsDayRunner: ...


@dataclass(frozen=True, slots=True)
class UsDayStorePaths:
    arm: Path
    delivery: Path
    execution: Path
    watch: Path


@dataclass(frozen=True, slots=True)
class UsDayAuthorityPaths:
    experiment_ledger: Path
    lane_registry: Path
    repository: Path
    signing_key: Path


@dataclass(frozen=True, slots=True)
class RunUsDayCommand:
    arm_request_id: str
    authority: UsDayAuthorityPaths
    session_id: str
    stores: UsDayStorePaths


@dataclass(frozen=True, slots=True)
class UsDayCliDependencies:
    clock: Clock
    runner_factory: UsDayRunnerFactory
    source_loader: SourceLoader


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def build_runner(command: RunUsDayCommand, request: UsDayOperatingRequest) -> UsDayRunner:
    execution_store = ExecutionStore(command.stores.execution)
    if not execution_store.is_initialized():
        raise UninitializedUsDayExecutionStoreError
    signer = HermesArmSigner(load_hermes_arm_signing_key(command.authority.signing_key))
    arm_store = HermesArmStore(command.stores.arm, signer)
    resolver = LedgerHermesArmAuthorityResolver(
        LedgerHermesArmAuthorityConfig(
            repository=command.authority.repository,
            lane_registry=command.authority.lane_registry,
            experiment_ledger=command.authority.experiment_ledger,
        )
    )
    gateway = HermesArmGateway(
        HermesArmGatewayConfig(
            store=arm_store,
            authority_resolver=resolver,
            signer=signer,
            clock=now_utc,
            nonce_factory=lambda: secrets.token_bytes(32),
            ttl_seconds=300,
        )
    )
    return UsDayOperatingCoordinator(
        UsDayOperatingCoordinatorConfig(
            arm_consumer=StrategyBoundHermesArmConsumer(gateway, arm_store),
            credentials=load_alpaca_paper_credentials(),
            execution_store=execution_store,
            delivery_store=HermesDeliveryStore(command.stores.delivery),
        )
    )


DEFAULT_DEPENDENCIES = UsDayCliDependencies(now_utc, build_runner, load_current_orb_paper_entry)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Run one causally current US Day Alpaca Paper operating session")
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="consume one Hermes arm and drive the existing Paper session to terminal")
    run.add_argument("--arm-database", type=Path, required=True)
    run.add_argument("--arm-request-id", required=True)
    run.add_argument("--delivery-database", type=Path, required=True)
    run.add_argument("--execution-database", type=Path, required=True)
    run.add_argument("--experiment-ledger", type=Path, required=True)
    run.add_argument("--lane-registry", type=Path, required=True)
    run.add_argument("--repository", type=Path, default=Path.cwd())
    run.add_argument("--session-id", required=True)
    run.add_argument("--signing-key", type=Path, default=DEFAULT_HERMES_ARM_SIGNING_KEY_PATH)
    run.add_argument("--watch-database", type=Path, required=True)
    return root


def main(
    argv: Sequence[str] | None = None,
    dependencies: UsDayCliDependencies = DEFAULT_DEPENDENCIES,
) -> int:
    namespace = parser().parse_args(argv)
    command = _command(namespace)
    evaluated_at = dependencies.clock()
    try:
        admission = dependencies.source_loader(command.stores.watch, evaluated_at)
        request = _request(command, admission, evaluated_at)
        result = dependencies.runner_factory(command, request).run(request)
    except US_DAY_OPERATIONAL_ERRORS as error:
        _print({"reason": safe_operational_reason(error), "result": "blocked"})
        return 1
    _print(
        {
            "reasons": list(result.reasons),
            "result": result.status.value,
            "session_id": result.session_id,
            "transitions": [transition.value for transition in result.transitions],
        }
    )
    match result.status:
        case UsDayOperatingStatus.COMPLETED:
            return 0
        case UsDayOperatingStatus.BLOCKED:
            return 1
        case UsDayOperatingStatus.INCIDENT:
            return 2
        case unreachable:
            assert_never(unreachable)


def _command(namespace: argparse.Namespace) -> RunUsDayCommand:
    return RunUsDayCommand(
        arm_request_id=namespace.arm_request_id,
        session_id=namespace.session_id,
        stores=UsDayStorePaths(
            arm=namespace.arm_database,
            delivery=namespace.delivery_database,
            execution=namespace.execution_database,
            watch=namespace.watch_database,
        ),
        authority=UsDayAuthorityPaths(
            experiment_ledger=namespace.experiment_ledger,
            lane_registry=namespace.lane_registry,
            repository=namespace.repository,
            signing_key=namespace.signing_key,
        ),
    )


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


def _print(payload: Mapping[str, JsonValue]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
