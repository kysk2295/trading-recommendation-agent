from __future__ import annotations

import argparse
import datetime as dt
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, override

from trading_agent.hermes_arm_request import (
    HermesArmRequest,
    HermesArmTransitionKind,
    InvalidHermesArmRequestError,
)
from trading_agent.hermes_arm_signing import (
    DEFAULT_HERMES_ARM_SIGNING_KEY_PATH,
    HermesArmSigner,
    load_hermes_arm_signing_key,
)
from trading_agent.hermes_arm_store import HermesArmStore
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_entry_source import InvalidCurrentOrbPaperEntrySourceError, load_current_orb_paper_entry
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.us_day_operating_cli import main as operating_main
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


class ArmedEntryResult(StrEnum):
    WAITING_SETUP = "waiting_setup"
    WAITING_OWNER_ARM = "waiting_owner_arm"
    BLOCKED_AMBIGUOUS_CONFIRMED_ARM = "blocked_ambiguous_confirmed_arm"
    CENSORED = "censored"


class InvalidUsDayArmedEntryCommandError(ValueError):
    @override
    def __str__(self) -> str:
        return "US Day armed entry command is invalid"


@dataclass(frozen=True, slots=True)
class UsDayArmedEntryCommand:
    arm_database: Path
    delivery_database: Path
    execution_database: Path
    watch_database: Path
    experiment_ledger: Path
    lane_registry: Path
    repository: Path
    signing_key: Path
    session_id: str
    entry_cutoff: dt.datetime
    poll_interval_seconds: float
    source_artifacts: tuple[Path, ...]
    terminal_output: Path | None
    once: bool


type SourceLoader = Callable[[Path, dt.datetime], PaperOrderAdmissionRequest]
type SignerLoader = Callable[[Path], HermesArmSigner]
type OperatingMain = Callable[[Sequence[str]], int]
type StoreFactory = Callable[[Path, HermesArmSigner], HermesArmStore]
type Clock = Callable[[], dt.datetime]


@dataclass(frozen=True, slots=True)
class UsDayArmedEntryDependencies:
    clock: Clock
    source_loader: SourceLoader
    signer_loader: SignerLoader
    operating_main: OperatingMain
    store_factory: StoreFactory
    sleeper: Callable[[float], None]


def _load_signer(path: Path) -> HermesArmSigner:
    return HermesArmSigner(load_hermes_arm_signing_key(path))


DEFAULT_DEPENDENCIES: Final = UsDayArmedEntryDependencies(
    clock=lambda: dt.datetime.now(dt.UTC),
    source_loader=load_current_orb_paper_entry,
    signer_loader=_load_signer,
    operating_main=operating_main,
    store_factory=HermesArmStore,
    sleeper=time.sleep,
)


def main(argv: Sequence[str] | None = None, dependencies: UsDayArmedEntryDependencies = DEFAULT_DEPENDENCIES) -> int:
    try:
        command = parse_command(argv)
        _validate_session(command, dependencies.clock())
        return _observe(command, dependencies)
    except InvalidUsDayArmedEntryCommandError:
        _print_blocked("invalid_command")
        return 1
    except InvalidHermesArmRequestError as error:
        _print_blocked(error.reason.value)
        return 1
    except SystemExit as error:
        return _parser_exit(error)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Observe one current XNYS ORB setup for a confirmed Hermes owner arm")
    paths = (
        ("--arm-database", "arm_database"),
        ("--delivery-database", "delivery_database"),
        ("--execution-database", "execution_database"),
        ("--watch-database", "watch_database"),
        ("--experiment-ledger", "experiment_ledger"),
        ("--lane-registry", "lane_registry"),
        ("--repository", "repository"),
    )
    for flag, _ in paths:
        root.add_argument(flag, type=_absolute_path, required=True)
    root.add_argument("--signing-key", type=_absolute_path, default=DEFAULT_HERMES_ARM_SIGNING_KEY_PATH.expanduser())
    root.add_argument("--session-id", required=True)
    root.add_argument("--entry-cutoff", type=_aware_datetime, required=True)
    root.add_argument("--poll-interval-seconds", type=_poll_interval, required=True)
    root.add_argument("--source-artifact", type=_absolute_path, action="append", default=[])
    root.add_argument("--terminal-output", type=_absolute_path)
    root.add_argument("--once", action="store_true")
    return root


def parse_command(argv: Sequence[str] | None = None) -> UsDayArmedEntryCommand:
    args = parser().parse_args(argv)
    source_artifacts = tuple(args.source_artifact)
    if (args.terminal_output is None) is bool(source_artifacts):
        raise InvalidUsDayArmedEntryCommandError
    return UsDayArmedEntryCommand(
        args.arm_database,
        args.delivery_database,
        args.execution_database,
        args.watch_database,
        args.experiment_ledger,
        args.lane_registry,
        args.repository,
        args.signing_key,
        args.session_id,
        args.entry_cutoff,
        args.poll_interval_seconds,
        source_artifacts,
        args.terminal_output,
        args.once,
    )


def _observe(command: UsDayArmedEntryCommand, dependencies: UsDayArmedEntryDependencies) -> int:
    while True:
        now = _aware_now(dependencies.clock())
        if now >= command.entry_cutoff:
            _print_result(ArmedEntryResult.CENSORED)
            return 0
        try:
            _ = dependencies.source_loader(command.watch_database, now)
        except InvalidCurrentOrbPaperEntrySourceError:
            if command.once:
                _print_result(ArmedEntryResult.WAITING_SETUP)
                return 0
            dependencies.sleeper(command.poll_interval_seconds)
            continue
        store = dependencies.store_factory(command.arm_database, dependencies.signer_loader(command.signing_key))
        matching = _matching_arms(store, command.session_id, now)
        match len(matching):
            case 0:
                if command.once:
                    _print_result(ArmedEntryResult.WAITING_OWNER_ARM)
                    return 0
                dependencies.sleeper(command.poll_interval_seconds)
            case 1:
                return dependencies.operating_main(_operating_argv(command, matching[0]))
            case _:
                _print_result(ArmedEntryResult.BLOCKED_AMBIGUOUS_CONFIRMED_ARM)
                return 1


def _matching_arms(store: HermesArmStore, session_id: str, now: dt.datetime) -> tuple[HermesArmRequest, ...]:
    selected: list[HermesArmRequest] = []
    for request in store.requests():
        transitions = store.transitions(request.request_id)
        latest = None if not transitions else transitions[-1].kind
        if (
            request.authority.scope.session_id == session_id
            and request.authority.scope.lane_id is LaneId.INTRADAY_MOMENTUM
            and latest is HermesArmTransitionKind.CONFIRMED
            and request.prepared_at <= now <= request.expires_at
        ):
            selected.append(request)
    return tuple(selected)


def _operating_argv(command: UsDayArmedEntryCommand, request: HermesArmRequest) -> tuple[str, ...]:
    arguments = (
        "run",
        "--arm-database",
        str(command.arm_database),
        "--arm-request-id",
        request.request_id,
        "--delivery-database",
        str(command.delivery_database),
        "--execution-database",
        str(command.execution_database),
        "--experiment-ledger",
        str(command.experiment_ledger),
        "--lane-registry",
        str(command.lane_registry),
        "--repository",
        str(command.repository),
        "--session-id",
        command.session_id,
        "--signing-key",
        str(command.signing_key),
        "--watch-database",
        str(command.watch_database),
    )
    if command.terminal_output is None:
        return arguments
    return (
        arguments
        + tuple(item for path in command.source_artifacts for item in ("--source-artifact", str(path)))
        + (
            "--terminal-output",
            str(command.terminal_output),
        )
    )


def _validate_session(command: UsDayArmedEntryCommand, now: dt.datetime) -> None:
    current = _aware_now(now)
    try:
        session_date = dt.date.fromisoformat(command.session_id.removeprefix("XNYS-"))
    except ValueError:
        raise InvalidUsDayArmedEntryCommandError from None
    if (
        command.session_id != f"XNYS-{session_date.isoformat()}"
        or regular_session_bounds(session_date) is None
        or current.astimezone(NEW_YORK).date() != session_date
        or command.entry_cutoff.astimezone(NEW_YORK).date() != session_date
    ):
        raise InvalidUsDayArmedEntryCommandError


def _absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("path must be absolute")
    return path


def _aware_datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return parsed


def _poll_interval(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("poll interval must be numeric") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("poll interval must be positive")
    return parsed


def _aware_now(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidUsDayArmedEntryCommandError
    return value


def _parser_exit(error: SystemExit) -> int:
    if error.code == 0:
        return 0
    _print_blocked("invalid_command")
    return 1


def _print_result(result: ArmedEntryResult) -> None:
    print(f'{{"result":"{result.value}"}}')


def _print_blocked(reason: str) -> None:
    print(f'{{"reason":"{reason}","result":"blocked"}}')
