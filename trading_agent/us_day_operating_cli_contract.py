from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never, override

from trading_agent.hermes_arm_signing import DEFAULT_HERMES_ARM_SIGNING_KEY_PATH


class InvalidUsDayCliCommandError(ValueError):
    @override
    def __str__(self) -> str:
        return "US Day CLI command is invalid"


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
    source_artifact_paths: tuple[Path, ...]
    terminal_output: Path | None


@dataclass(frozen=True, slots=True)
class PreflightUsDayCommand:
    execution_store: Path
    watch_store: Path


@dataclass(frozen=True, slots=True)
class RecoverUsDayCommand:
    execution_store: Path


@dataclass(frozen=True, slots=True)
class UsDayFinalizePaths:
    delivery_store: Path
    execution_store: Path
    repository: Path
    terminal_output: Path


@dataclass(frozen=True, slots=True)
class FinalizeUsDayCommand:
    paths: UsDayFinalizePaths
    session_id: str | None
    source_artifact_paths: tuple[Path, ...]
    strategy_version: str | None
    terminal_input: Path | None


@dataclass(frozen=True, slots=True)
class EvidenceUsDayCommand:
    repository: Path
    terminal_paths: tuple[Path, ...]


type UsDayCliCommand = (
    RunUsDayCommand
    | PreflightUsDayCommand
    | RecoverUsDayCommand
    | FinalizeUsDayCommand
    | EvidenceUsDayCommand
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Operate and attest causally current US Day Alpaca Paper sessions")
    commands = root.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight", help="run GET/WSS recovery, readiness, and admission only")
    preflight.add_argument("--execution-database", type=Path, required=True)
    preflight.add_argument("--watch-database", type=Path, required=True)
    run = commands.add_parser("run", help="consume one Hermes arm and drive the existing Paper session to terminal")
    _run_arguments(run)
    recover = commands.add_parser("recover", help="run targeted recovery and print redacted current state")
    recover.add_argument("--execution-database", type=Path, required=True)
    finalize = commands.add_parser("finalize", help="attest a flat terminal or refresh an existing terminal")
    _finalize_arguments(finalize)
    evidence = commands.add_parser("evidence", help="build the US Day three-session acceptance bundle")
    evidence.add_argument("--repository", type=Path, default=Path.cwd())
    evidence.add_argument("--terminal", type=Path, action="append", required=True)
    return root


def parse_command(argv: Sequence[str] | None = None) -> UsDayCliCommand:
    args = parser().parse_args(argv)
    match args.command:
        case "preflight":
            return PreflightUsDayCommand(args.execution_database, args.watch_database)
        case "run":
            return _run_command(args)
        case "recover":
            return RecoverUsDayCommand(args.execution_database)
        case "finalize":
            return _finalize_command(args)
        case "evidence":
            return EvidenceUsDayCommand(args.repository, tuple(args.terminal))
        case unreachable:
            assert_never(unreachable)


def _run_arguments(run: argparse.ArgumentParser) -> None:
    run.add_argument("--arm-database", type=Path, required=True)
    run.add_argument("--arm-request-id", required=True)
    run.add_argument("--delivery-database", type=Path, required=True)
    run.add_argument("--execution-database", type=Path, required=True)
    run.add_argument("--experiment-ledger", type=Path, required=True)
    run.add_argument("--lane-registry", type=Path, required=True)
    run.add_argument("--repository", type=Path, default=Path.cwd())
    run.add_argument("--session-id", required=True)
    run.add_argument("--signing-key", type=Path, default=DEFAULT_HERMES_ARM_SIGNING_KEY_PATH)
    run.add_argument("--source-artifact", type=Path, action="append", default=[])
    run.add_argument("--terminal-output", type=Path)
    run.add_argument("--watch-database", type=Path, required=True)


def _finalize_arguments(finalize: argparse.ArgumentParser) -> None:
    finalize.add_argument("--delivery-database", type=Path, required=True)
    finalize.add_argument("--execution-database", type=Path, required=True)
    finalize.add_argument("--repository", type=Path, default=Path.cwd())
    finalize.add_argument("--session-id")
    finalize.add_argument("--source-artifact", type=Path, action="append", default=[])
    finalize.add_argument("--strategy-version")
    finalize.add_argument("--terminal-input", type=Path)
    finalize.add_argument("--terminal-output", type=Path, required=True)


def _run_command(args: argparse.Namespace) -> RunUsDayCommand:
    source_paths = tuple(args.source_artifact)
    if (args.terminal_output is None and source_paths) or (args.terminal_output is not None and not source_paths):
        raise InvalidUsDayCliCommandError
    return RunUsDayCommand(
        arm_request_id=args.arm_request_id,
        session_id=args.session_id,
        stores=UsDayStorePaths(
            arm=args.arm_database,
            delivery=args.delivery_database,
            execution=args.execution_database,
            watch=args.watch_database,
        ),
        authority=UsDayAuthorityPaths(
            experiment_ledger=args.experiment_ledger,
            lane_registry=args.lane_registry,
            repository=args.repository,
            signing_key=args.signing_key,
        ),
        source_artifact_paths=source_paths,
        terminal_output=args.terminal_output,
    )


def _finalize_command(args: argparse.Namespace) -> FinalizeUsDayCommand:
    source_paths = tuple(args.source_artifact)
    existing = args.terminal_input is not None
    no_setup = args.session_id is not None and args.strategy_version is not None and bool(source_paths)
    if existing == no_setup:
        raise InvalidUsDayCliCommandError
    return FinalizeUsDayCommand(
        paths=UsDayFinalizePaths(
            args.delivery_database,
            args.execution_database,
            args.repository,
            args.terminal_output,
        ),
        session_id=args.session_id,
        source_artifact_paths=source_paths,
        strategy_version=args.strategy_version,
        terminal_input=args.terminal_input,
    )
