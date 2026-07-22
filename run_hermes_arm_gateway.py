#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import json
import secrets
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.hermes_arm_authority import (
    LedgerHermesArmAuthorityConfig,
    LedgerHermesArmAuthorityResolver,
)
from trading_agent.hermes_arm_gateway import HermesArmGateway, HermesArmGatewayConfig
from trading_agent.hermes_arm_request import (
    HermesArmConfirmCommand,
    HermesArmConsumeCommand,
    HermesArmPrepareCommand,
    HermesArmRevokeCommand,
    HermesArmScope,
    InvalidHermesArmRequestError,
)
from trading_agent.hermes_arm_signing import (
    DEFAULT_HERMES_ARM_SIGNING_KEY_PATH,
    HermesArmSigner,
    load_hermes_arm_signing_key,
)
from trading_agent.hermes_arm_store import HermesArmStore
from trading_agent.lane_identity_models import LaneId

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Issue signed one-use Alpaca Paper arm requests")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="prepare an expiring owner-bound request")
    _common_arguments(prepare)
    prepare.add_argument("--owner-id-hash", required=True)
    prepare.add_argument("--session-id", required=True)
    prepare.add_argument("--lane-id", type=LaneId, required=True)
    confirm = commands.add_parser("confirm", help="confirm a prepared request")
    _common_arguments(confirm)
    confirm.add_argument("--owner-id-hash", required=True)
    confirm.add_argument("--request-id", required=True)
    confirm.add_argument("--confirmation", required=True)
    status = commands.add_parser("status", help="read a redacted request status")
    _common_arguments(status)
    status.add_argument("--request-id", required=True)
    revoke = commands.add_parser("revoke", help="revoke an unconsumed request")
    _common_arguments(revoke)
    revoke.add_argument("--owner-id-hash", required=True)
    revoke.add_argument("--request-id", required=True)
    consume = commands.add_parser("consume", help="consume one confirmed request")
    _common_arguments(consume)
    consume.add_argument("--request-id", required=True)
    consume.add_argument("--session-id", required=True)
    consume.add_argument("--lane-id", type=LaneId, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        gateway = _gateway(args)
        match args.command:
            case "prepare":
                prepared = gateway.prepare(
                    HermesArmPrepareCommand(
                        owner_id_hash=args.owner_id_hash,
                        scope=HermesArmScope(session_id=args.session_id, lane_id=args.lane_id),
                    )
                )
                _print(
                    {
                        "confirmation": prepared.confirmation,
                        "expires_at": prepared.expires_at.isoformat(),
                        "request_id": prepared.request_id,
                        "result": "prepared",
                    }
                )
            case "confirm":
                status = gateway.confirm(
                    HermesArmConfirmCommand(
                        owner_id_hash=args.owner_id_hash,
                        request_id=args.request_id,
                        confirmation=args.confirmation,
                    )
                )
                _print_status(status.status.value if status.status is not None else "prepared", status.request_id)
            case "status":
                status = gateway.status(args.request_id)
                _print_status(status.status.value if status.status is not None else "prepared", status.request_id)
            case "revoke":
                status = gateway.revoke(
                    HermesArmRevokeCommand(owner_id_hash=args.owner_id_hash, request_id=args.request_id)
                )
                _print_status(status.status.value if status.status is not None else "prepared", status.request_id)
            case "consume":
                _ = gateway.consume(
                    HermesArmConsumeCommand(
                        request_id=args.request_id,
                        expected_scope=HermesArmScope(session_id=args.session_id, lane_id=args.lane_id),
                    )
                )
                _print({"request_id": args.request_id, "result": "consumed"})
            case unreachable:
                assert_never(unreachable)
    except (InvalidHermesArmRequestError, ValidationError, OSError, sqlite3.DatabaseError) as error:
        reason = error.reason.value if isinstance(error, InvalidHermesArmRequestError) else "invalid_request"
        _print({"reason": reason, "result": "blocked"})
        return 1
    return 0


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--lane-registry", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, default=DEFAULT_HERMES_ARM_SIGNING_KEY_PATH)


def _gateway(args: argparse.Namespace) -> HermesArmGateway:
    signer = HermesArmSigner(load_hermes_arm_signing_key(args.signing_key))
    resolver = LedgerHermesArmAuthorityResolver(
        LedgerHermesArmAuthorityConfig(
            repository=args.repository,
            lane_registry=args.lane_registry,
            experiment_ledger=args.experiment_ledger,
        )
    )
    return HermesArmGateway(
        HermesArmGatewayConfig(
            store=HermesArmStore(args.database, signer),
            authority_resolver=resolver,
            signer=signer,
            clock=lambda: dt.datetime.now(dt.UTC),
            nonce_factory=lambda: secrets.token_bytes(32),
            ttl_seconds=300,
        )
    )


def _print_status(status: str, request_id: str) -> None:
    _print({"request_id": request_id, "result": status})


def _print(payload: Mapping[str, JsonValue]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
