from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, assert_never

from pydantic import TypeAdapter, ValidationError

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.future_session_coordinator import coordinate_future_session
from trading_agent.future_session_coordinator_inspectors import (
    CoordinatorInspectionError,
)
from trading_agent.future_session_coordinator_models import (
    FutureSessionCoordinatorRequest,
    FutureSessionCoordinatorResult,
    canonical_coordinator_receipt_json,
)
from trading_agent.future_session_kr_activation import (
    activate_kr_future_session,
)
from trading_agent.future_session_kr_activation_verifier import (
    verify_kr_supervisor_preflight,
)
from trading_agent.future_session_kr_lifecycle_authority import (
    InvalidKrFutureSessionLifecycleAuthorityError,
    KrFutureSessionLifecycleRequest,
    bootstrap_kr_future_session_lifecycle,
)
from trading_agent.future_session_kr_materializer import (
    materialize_kr_future_session,
)
from trading_agent.future_session_kr_materializer_models import (
    KrFutureSessionMaterializationRequest,
)
from trading_agent.future_session_kr_supervisor import (
    run_kr_future_session_supervisor,
)
from trading_agent.future_session_materialize_cli_parser import (
    build_future_session_parser,
)
from trading_agent.future_session_us_activation import (
    FutureSessionActivationError,
    activate_us_future_session,
)
from trading_agent.future_session_us_materializer import (
    FutureSessionMaterializationError,
    materialize_us_future_session,
)
from trading_agent.future_session_us_materializer_models import (
    UsFutureSessionMaterializationRequest,
)

type Command = Literal[
    "prepare",
    "activate",
    "prepare-kr",
    "activate-kr",
    "supervise-kr-preflight",
    "supervise-kr",
    "bootstrap-kr-lifecycle",
    "coordinate",
]
_COMMAND_ADAPTER = TypeAdapter(Command)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_future_session_parser()
    arguments = parser.parse_args(argv)
    command = _COMMAND_ADAPTER.validate_python(arguments.command)
    match command:
        case "prepare":
            return _prepare(arguments)
        case "activate":
            return _activate(arguments)
        case "prepare-kr":
            return _prepare_kr(arguments)
        case "activate-kr":
            return _activate_kr(arguments)
        case "supervise-kr-preflight":
            return _supervise_kr_preflight(arguments)
        case "supervise-kr":
            return _supervise_kr(arguments)
        case "bootstrap-kr-lifecycle":
            return _bootstrap_kr_lifecycle(arguments)
        case "coordinate":
            return _coordinate(arguments)
        case unreachable:
            assert_never(unreachable)


def _prepare(arguments: argparse.Namespace) -> int:
    try:
        manifest = materialize_us_future_session(
            UsFutureSessionMaterializationRequest(
                request_path=Path(arguments.request),
                plan_path=Path(arguments.plan),
                output_dir=Path(arguments.output_dir),
            )
        )
    except (FutureSessionMaterializationError, OSError, TypeError, ValueError):
        _write({"result": "invalid_materialization_authority"})
        return 2
    _write({"manifest": str(manifest), "result": "prepared"})
    return 0


def _activate(arguments: argparse.Namespace) -> int:
    try:
        activation = activate_us_future_session(
            manifest_path=Path(arguments.manifest),
        )
    except FutureSessionActivationError as error:
        _write({"reason": error.reason, "result": "blocked"})
        return 2
    except (OSError, TypeError, ValueError):
        _write({"reason": "artifact_io_failed", "result": "blocked"})
        return 2
    _write(
        {
            "labels": [entry.label for entry in activation.entries],
            "receipt": str(activation.receipt_path),
            "result": "activated",
        }
    )
    return 0


def _prepare_kr(arguments: argparse.Namespace) -> int:
    try:
        manifest = materialize_kr_future_session(
            KrFutureSessionMaterializationRequest(
                request_path=Path(arguments.request),
                plan_path=Path(arguments.plan),
                output_dir=Path(arguments.output_dir),
            )
        )
    except (FutureSessionMaterializationError, OSError, TypeError, ValueError):
        _write({"result": "invalid_materialization_authority"})
        return 2
    _write({"manifest": str(manifest), "result": "prepared"})
    return 0


def _activate_kr(arguments: argparse.Namespace) -> int:
    try:
        activation = activate_kr_future_session(
            manifest_path=Path(arguments.manifest),
        )
    except FutureSessionActivationError as error:
        _write({"reason": error.reason, "result": "blocked"})
        return 2
    except (OSError, TypeError, ValueError):
        _write({"reason": "artifact_io_failed", "result": "blocked"})
        return 2
    _write(
        {
            "label": activation.label,
            "receipt": str(activation.receipt_path),
            "result": "activated",
        }
    )
    return 0


def _supervise_kr_preflight(arguments: argparse.Namespace) -> int:
    try:
        verify_kr_supervisor_preflight(Path(arguments.manifest))
    except FutureSessionActivationError as error:
        _write({"reason": error.reason, "result": "blocked"})
        return 78
    except (OSError, TypeError, ValueError):
        _write({"reason": "artifact_io_failed", "result": "blocked"})
        return 78
    _write(
        {
            "lifecycle_completion": False,
            "result": "ready_to_prepare",
            "session_execution": False,
        }
    )
    return 0


def _supervise_kr(arguments: argparse.Namespace) -> int:
    try:
        state = run_kr_future_session_supervisor(
            Path(arguments.manifest),
            runner=lambda command: subprocess.run(command, check=False).returncode,
        )
    except FutureSessionActivationError as error:
        _write({"reason": error.reason, "result": "blocked"})
        return 78
    except (OSError, TypeError, ValidationError, ValueError):
        _write({"reason": "supervisor_authority_invalid", "result": "blocked"})
        return 78
    _write(
        {
            "lifecycle_completion": state.result.startswith("terminal_"),
            "result": state.result,
            "session_execution": bool(state.completed_phases),
        }
    )
    return 0


def _bootstrap_kr_lifecycle(arguments: argparse.Namespace) -> int:
    try:
        result = bootstrap_kr_future_session_lifecycle(
            KrFutureSessionLifecycleRequest(
                experiment_ledger=ExperimentLedgerStore(Path(arguments.database)),
                calendar_store=Path(arguments.calendar_store),
                rollover_bundle=Path(arguments.rollover_bundle),
                code_version=arguments.code_version,
                strategy_version=arguments.strategy_version,
                target_session=dt.date.fromisoformat(arguments.target_session),
                decided_at=dt.datetime.fromisoformat(arguments.decided_at),
            )
        )
    except (
        InvalidKrFutureSessionLifecycleAuthorityError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        _write({"result": "blocked"})
        return 2
    _write({"created": result.created, "result": "ready"})
    return 0


def _coordinate(arguments: argparse.Namespace) -> int:
    try:
        receipt = coordinate_future_session(
            FutureSessionCoordinatorRequest(
                request_path=Path(arguments.request),
                plan_path=Path(arguments.plan),
                launch_agents_dir=Path(arguments.launch_agents_dir),
            )
        )
    except (
        CoordinatorInspectionError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        _write({"reason": "invalid_request", "result": "blocked"})
        return 2
    sys.stdout.write(canonical_coordinator_receipt_json(receipt))
    return 2 if receipt.result is FutureSessionCoordinatorResult.BLOCKED else 0


def _write(payload: dict[str, bool | str | list[str]]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
