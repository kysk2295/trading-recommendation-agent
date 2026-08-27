#!/usr/bin/env -S uv run --offline --script
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths
from trading_agent.kr_autonomous_outcome_models import InvalidKrAutonomousOutcomeError
from trading_agent.kr_loop_engineer_controller import (
    InvalidKrLoopEngineerControllerError,
    KrLoopEngineerController,
)
from trading_agent.kr_loop_engineer_models import (
    InvalidKrLoopEngineerModelError,
    KrLoopHealthReceipt,
    KrLoopShadowReceipt,
)
from trading_agent.kr_loop_engineer_mutation import GrokKrLoopMutationWorker, KrLoopMutationExecutor
from trading_agent.kr_loop_engineer_store import InvalidKrLoopEngineerStoreError, KrLoopEngineerStore
from trading_agent.kr_loop_engineer_sync import (
    InvalidKrLoopBundleSyncError,
    find_kr_loop_bundle,
    sync_kr_loop_bundles,
)
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError, read_private_text
from trading_agent.repository_current_main import CurrentMainAuthorityError, current_main_commit


class _Command(StrEnum):
    STATUS = "status"
    HEALTH = "health"
    SHADOW = "shadow"
    SYNC = "sync"
    TICK = "tick"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled KR Loop Engineer challenger lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--database", type=Path, required=True)
    health = subparsers.add_parser("health")
    health.add_argument("--database", type=Path, required=True)
    health.add_argument("--receipt", type=Path, required=True)
    shadow = subparsers.add_parser("shadow")
    shadow.add_argument("--database", type=Path, required=True)
    shadow.add_argument("--candidate-id", required=True)
    shadow.add_argument("--receipt", type=Path, required=True)
    for name in ("sync", "tick"):
        action = subparsers.add_parser(name)
        action.add_argument("--output-root", type=Path, required=True)
        action.add_argument("--repository", type=Path, required=True)
        if name == "tick":
            action.add_argument("--grok-binary", default="grok")
    return parser


def main(argv: tuple[str, ...] | None = None) -> int:
    try:
        try:
            args = _parser().parse_args(argv)
        except SystemExit as error:
            match error.code:
                case int() as code:
                    return code
                case str():
                    return 1
                case None:
                    return 0
                case unreachable:
                    assert_never(unreachable)
        command = _Command(args.command)
        match command:
            case _Command.STATUS:
                _print_status(KrLoopEngineerStore(args.database))
            case _Command.HEALTH:
                receipt = KrLoopHealthReceipt.model_validate_json(read_private_text(args.receipt))
                store = KrLoopEngineerStore(args.database)
                controller = _read_only_controller(store)
                _ = controller.record_health(receipt)
                _print_status(store)
            case _Command.SHADOW:
                receipt = KrLoopShadowReceipt.model_validate_json(read_private_text(args.receipt))
                store = KrLoopEngineerStore(args.database)
                controller = _read_only_controller(store)
                _ = controller.record_shadow(args.candidate_id, receipt)
                _print_status(store)
            case _Command.SYNC:
                paths = _paths(args.output_root)
                commit = current_main_commit(args.repository)
                _ = sync_kr_loop_bundles(paths, base_commit=commit, now=dt.datetime.now(dt.UTC))
                _print_status(KrLoopEngineerStore(paths.loop_database))
            case _Command.TICK:
                paths = _paths(args.output_root)
                commit = current_main_commit(args.repository)
                _ = sync_kr_loop_bundles(paths, base_commit=commit, now=dt.datetime.now(dt.UTC))
                store = KrLoopEngineerStore(paths.loop_database)
                pending = next(
                    (item for item in store.snapshots() if item.state.value == "detected"),
                    None,
                )
                if pending is not None:
                    bundle = find_kr_loop_bundle(paths, pending.bundle_id)
                    if bundle is None:
                        raise InvalidKrLoopEngineerControllerError
                    mutation = KrLoopMutationExecutor(
                        repository=args.repository,
                        task_root=paths.loop_task_root,
                        artifact_root=paths.loop_artifact_root,
                        worker=GrokKrLoopMutationWorker(args.grok_binary),
                    )
                    _ = KrLoopEngineerController(store, mutation).mutate(
                        bundle,
                        now=dt.datetime.now(dt.UTC),
                    )
                _print_status(store)
            case unreachable:
                assert_never(unreachable)
        return 0
    except (
        CurrentMainAuthorityError,
        InvalidKrAutonomousOutcomeError,
        InvalidKrLoopEngineerControllerError,
        InvalidKrLoopEngineerModelError,
        InvalidKrLoopEngineerStoreError,
        InvalidKrLoopBundleSyncError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        print("invalid Loop Engineer request", file=sys.stderr)
        return 2


def _read_only_controller(store: KrLoopEngineerStore) -> KrLoopEngineerController:
    root = store.path.parent
    return KrLoopEngineerController(
        store,
        KrLoopMutationExecutor(
            repository=root,
            task_root=root / "unused-tasks",
            artifact_root=root / "unused-artifacts",
        ),
    )


def _paths(output_root: Path) -> KrAutonomousOperatorPaths:
    supervisor = output_root.absolute() / "autonomous-supervisor"
    kr = supervisor / "kr-v1"
    return KrAutonomousOperatorPaths(
        task_database=supervisor / "tasks.sqlite3",
        memory_database=supervisor / "memory.sqlite3",
        social_signal_database=kr / "social-signals.sqlite3",
        trade_database=kr / "kr-autonomous-trades.sqlite3",
        position_database=kr / "kr-virtual-positions.sqlite3",
        market_receipt_root=kr / "market-receipts",
    )


def _print_status(store: KrLoopEngineerStore) -> None:
    latest = {item.candidate_id: item for item in store.snapshots()}
    releases = store.releases()
    payload = {
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "state": item.state.value,
                "updated_at": item.updated_at.isoformat(),
            }
            for item in sorted(latest.values(), key=lambda value: value.candidate_id)
        ],
        "release": None
        if not releases
        else {
            "release_id": releases[-1].release_id,
            "generation": releases[-1].generation,
            "action": releases[-1].action.value,
        },
        "paper_only": True,
        "trading_authority": False,
    }
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
