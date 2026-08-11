from __future__ import annotations

import argparse
import datetime as dt
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, assert_never

from trading_agent.hermes_arm_authority import LedgerHermesArmAuthorityConfig, LedgerHermesArmAuthorityResolver
from trading_agent.hermes_arm_request import HermesArmAuthority, HermesArmScope, InvalidHermesArmRequestError
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_auto_arm_authority import require_current_clean_main
from trading_agent.paper_auto_arm_policy import (
    InvalidPaperAutoArmPolicyError,
    PaperAutoArmPolicy,
    load_paper_auto_arm_policy,
    write_paper_auto_arm_policy,
)
from trading_agent.paper_auto_arm_runtime import verify_paper_auto_arm_session


class PaperAutoArmPolicyAction(StrEnum):
    PROVISION = "provision"
    VERIFY = "verify"
    STATUS = "status"


@dataclass(frozen=True, slots=True)
class PaperAutoArmPolicyCommand:
    action: PaperAutoArmPolicyAction
    policy_path: Path
    repository: Path
    lane_registry: Path
    experiment_ledger: Path
    session_id: str


class PaperAutoArmAuthorityLoader(Protocol):
    def __call__(self, command: PaperAutoArmPolicyCommand, scope: HermesArmScope, /) -> HermesArmAuthority: ...


@dataclass(frozen=True, slots=True)
class PaperAutoArmCliDependencies:
    clock: Callable[[], dt.datetime]
    authority_loader: PaperAutoArmAuthorityLoader


def _load_authority(command: PaperAutoArmPolicyCommand, scope: HermesArmScope) -> HermesArmAuthority:
    authority = LedgerHermesArmAuthorityResolver(
        LedgerHermesArmAuthorityConfig(
            repository=command.repository,
            lane_registry=command.lane_registry,
            experiment_ledger=command.experiment_ledger,
        )
    ).resolve(scope)
    require_current_clean_main(command.repository, authority.commit_sha)
    return authority


DEFAULT_DEPENDENCIES: Final = PaperAutoArmCliDependencies(
    clock=lambda: dt.datetime.now(dt.UTC),
    authority_loader=_load_authority,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Provision and verify a redacted Alpaca Paper auto-arm policy")
    commands = root.add_subparsers(dest="action", required=True)
    for action in PaperAutoArmPolicyAction:
        child = commands.add_parser(action.value)
        child.add_argument("--policy", type=_absolute_path, required=True)
        child.add_argument("--repository", type=_absolute_path, required=True)
        child.add_argument("--lane-registry", type=_absolute_path, required=True)
        child.add_argument("--experiment-ledger", type=_absolute_path, required=True)
        child.add_argument("--session-id", required=True)
    return root


def main(
    argv: Sequence[str] | None = None,
    dependencies: PaperAutoArmCliDependencies = DEFAULT_DEPENDENCIES,
) -> int:
    try:
        command = _parse_command(argv)
        scope = HermesArmScope(session_id=command.session_id, lane_id=LaneId.INTRADAY_MOMENTUM)
        authority = dependencies.authority_loader(command, scope)
        match command.action:
            case PaperAutoArmPolicyAction.PROVISION:
                policy = PaperAutoArmPolicy.from_authority(authority)
                _ = verify_paper_auto_arm_session(
                    policy,
                    authority,
                    command.session_id,
                    dependencies.clock(),
                )
                write_paper_auto_arm_policy(command.policy_path, policy)
                result = "provisioned"
            case PaperAutoArmPolicyAction.VERIFY | PaperAutoArmPolicyAction.STATUS:
                policy = load_paper_auto_arm_policy(command.policy_path)
                _ = verify_paper_auto_arm_session(
                    policy,
                    authority,
                    command.session_id,
                    dependencies.clock(),
                )
                result = "valid"
            case unreachable:
                assert_never(unreachable)
        print(json.dumps({"enabled": policy.enabled, "result": result}, separators=(",", ":"), sort_keys=True))
        return 0
    except (InvalidHermesArmRequestError, InvalidPaperAutoArmPolicyError) as error:
        reason = error.reason.value
        print(json.dumps({"reason": reason, "result": "blocked"}, separators=(",", ":"), sort_keys=True))
        return 1
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 1


def _parse_command(argv: Sequence[str] | None) -> PaperAutoArmPolicyCommand:
    args = parser().parse_args(argv)
    return PaperAutoArmPolicyCommand(
        action=PaperAutoArmPolicyAction(args.action),
        policy_path=args.policy,
        repository=args.repository,
        lane_registry=args.lane_registry,
        experiment_ledger=args.experiment_ledger,
        session_id=args.session_id,
    )


def _absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("path must be absolute")
    return path
