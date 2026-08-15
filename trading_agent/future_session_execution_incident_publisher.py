from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import time
from pathlib import Path

from pydantic import ValidationError

from trading_agent.future_session_execution_incident import (
    FutureSessionExecutionIncidentReceipt,
    InvalidFutureSessionExecutionIncidentError,
    canonical_execution_incident_json,
)
from trading_agent.future_session_execution_incident_queue import (
    FutureSessionExecutionIncidentQueuePointer,
    canonical_execution_incident_queue_json,
)
from trading_agent.future_session_plan_models import FutureSessionMarket
from trading_agent.future_session_us_activation_verifier import read_private_file
from trading_agent.private_immutable_file import publish_private_immutable_text_once


def publish_execution_incident(
    *,
    receipt_path: Path,
    queue_path: Path,
    manifest_path: Path,
    market: FutureSessionMarket,
    target_session: dt.date,
    role: str,
    request_sha256: str,
    plan_sha256: str,
    scheduler_main_sha: str,
    runtime_commit_sha: str,
) -> None:
    _validate_paths(receipt_path, queue_path, manifest_path, market, target_session, role)
    manifest_payload = read_private_file(manifest_path, 0o600)
    candidate = FutureSessionExecutionIncidentReceipt(
        completed_at_epoch=int(time.time()),
        market=market,
        target_session=target_session,
        role=role,
        reason="runtime_authority_invalid",
        manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
        request_sha256=request_sha256,
        plan_sha256=plan_sha256,
        scheduler_main_sha=scheduler_main_sha,
        runtime_commit_sha=runtime_commit_sha,
    )
    receipt_payload = _publish_or_read_receipt(receipt_path, candidate)
    pointer = FutureSessionExecutionIncidentQueuePointer(
        market=market,
        target_session=target_session,
        role=role,
        incident_sha256=hashlib.sha256(receipt_payload).hexdigest(),
    )
    _ = publish_private_immutable_text_once(
        queue_path,
        canonical_execution_incident_queue_json(pointer),
    )


def _publish_or_read_receipt(
    path: Path,
    candidate: FutureSessionExecutionIncidentReceipt,
) -> bytes:
    try:
        stored = read_private_file(path, 0o600)
    except FileNotFoundError:
        _ = publish_private_immutable_text_once(path, canonical_execution_incident_json(candidate))
        stored = read_private_file(path, 0o600)
    try:
        receipt = FutureSessionExecutionIncidentReceipt.model_validate_json(stored)
    except (TypeError, ValidationError, ValueError):
        raise InvalidFutureSessionExecutionIncidentError from None
    if canonical_execution_incident_json(receipt).encode() != stored or receipt.model_dump(
        exclude={"completed_at_epoch"}
    ) != candidate.model_dump(exclude={"completed_at_epoch"}):
        raise InvalidFutureSessionExecutionIncidentError
    return stored


def _validate_paths(
    receipt: Path,
    queue: Path,
    manifest: Path,
    market: FutureSessionMarket,
    target: dt.date,
    role: str,
) -> None:
    if (
        not receipt.is_absolute()
        or not queue.is_absolute()
        or not manifest.is_absolute()
        or receipt.name != f"{role}.json"
        or receipt.parent.name != "execution-incidents"
        or receipt.parent.parent.name != target.isoformat()
        or receipt.parent.parent.parent.name != market.value
        or receipt.parent.parent.parent.parent.name != "artifacts"
        or manifest != receipt.parent.parent / "preparation-manifest.json"
        or queue.parent.name != "pending-execution-incidents"
        or queue.parent.parent != receipt.parents[4]
        or queue.name != f"{market.value}--{target.isoformat()}--{role}.json"
    ):
        raise InvalidFutureSessionExecutionIncidentError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--market", required=True, choices=("us", "kr"))
    parser.add_argument("--target-session", required=True, type=dt.date.fromisoformat)
    parser.add_argument("--role", required=True)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--scheduler-main-sha", required=True)
    parser.add_argument("--runtime-commit-sha", required=True)
    return parser


def main(arguments: tuple[str, ...] | None = None) -> int:
    parsed = build_parser().parse_args(arguments)
    try:
        publish_execution_incident(
            receipt_path=parsed.receipt,
            queue_path=parsed.queue,
            manifest_path=parsed.manifest,
            market=FutureSessionMarket(parsed.market),
            target_session=parsed.target_session,
            role=parsed.role,
            request_sha256=parsed.request_sha256,
            plan_sha256=parsed.plan_sha256,
            scheduler_main_sha=parsed.scheduler_main_sha,
            runtime_commit_sha=parsed.runtime_commit_sha,
        )
    except (OSError, TypeError, ValueError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("build_parser", "main", "publish_execution_incident")
