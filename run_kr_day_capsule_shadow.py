from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_adapter import (
    SEOUL,
    adapt_kr_day_capsule_evaluation,
    adapt_kr_day_capsule_management_evaluation,
)
from trading_agent.kr_day_capsule_models import (
    KrDayCapsuleEvaluation,
    KrDayCapsuleEvaluationRequest,
)
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowStatus
from trading_agent.kr_day_capsule_shadow_service import run_kr_day_capsule_shadow_tick
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.private_immutable_file import publish_private_immutable_text, read_private_text


class _EventResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    capsule_id: str
    session_date: str
    attempted_bar_cursor: str
    accepted_bar_cursor: str | None
    status: str
    reason: str
    created: bool
    decision_event_id: str | None
    decision_reason_codes: tuple[str, ...]
    market_gate_reasons: tuple[str, ...]


class _CliResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: Literal["processed", "partial", "blocked"]
    created_count: int
    reused_count: int
    invalid_request_count: int
    events: tuple[_EventResult, ...]
    mutation: Literal[0] = 0
    provider_read_only: Literal[True] = True
    research_only: Literal[True] = True
    trading_authority: Literal[False] = False
    receipt_id: str | None = None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run up to three local research-only KR capsule Shadow evaluations.")
    parser.add_argument("--request", action="append", type=Path, default=[])
    parser.add_argument("--store", type=Path)
    parser.add_argument("--decision-store", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    request_paths = tuple(args.request)
    if not request_paths or args.store is None or args.decision_store is None or len(request_paths) > 3:
        _emit(_blocked_result(len(request_paths)))
        return 2
    store = KrDayCapsuleShadowStore(args.store)
    valid, invalid_count = _adapt_requests(request_paths, store)
    if not valid:
        _emit(_blocked_result(invalid_count))
        return 2
    try:
        batch = run_kr_day_capsule_shadow_tick(
            store,
            valid,
            KrDayDecisionStore(args.decision_store),
        )
    except (OSError, TypeError, ValidationError, ValueError):
        _emit(_blocked_result(invalid_count))
        return 2
    events = tuple(
        _EventResult(
            event_id=item.event.event_id,
            capsule_id=item.event.capsule_id,
            session_date=item.event.session_date.isoformat(),
            attempted_bar_cursor=item.event.attempted_bar_cursor.isoformat(),
            accepted_bar_cursor=(
                None if item.event.accepted_bar_cursor is None else item.event.accepted_bar_cursor.isoformat()
            ),
            status=item.event.status.value,
            reason=item.event.reason.value,
            created=item.created,
            decision_event_id=item.decision_event_id,
            decision_reason_codes=tuple(reason.value for reason in item.decision_reason_codes),
            market_gate_reasons=tuple(reason.value for reason in item.market_gate_reasons),
        )
        for item in batch.results
    )
    result = _CliResult(
        result="partial" if invalid_count else "processed",
        created_count=sum(item.created for item in events),
        reused_count=sum(not item.created for item in events),
        invalid_request_count=invalid_count,
        events=events,
    )
    try:
        result = _publish_receipt(args.output, result)
    except (OSError, TypeError, ValueError):
        _emit(_blocked_result(invalid_count))
        return 2
    _emit(result)
    return 2 if invalid_count else 0


def _adapt_requests(
    paths: tuple[Path, ...],
    store: KrDayCapsuleShadowStore,
) -> tuple[tuple[KrDayCapsuleEvaluation, ...], int]:
    evaluations: list[KrDayCapsuleEvaluation] = []
    invalid_count = 0
    for path in paths:
        try:
            if not path.is_absolute():
                raise ValueError
            payload = read_private_text(path)
            request = KrDayCapsuleEvaluationRequest.model_validate_json(payload)
            if canonical_experiment_ledger_json(request) + "\n" != payload:
                raise ValueError
            session_date = request.evaluated_at.astimezone(SEOUL).date().isoformat()
            latest = store.latest(request.capsule.capsule_id, session_date)
            try:
                evaluation = adapt_kr_day_capsule_evaluation(request, allow_market_blocked=True)
            except ValueError:
                if latest is None or latest.status is not KrDayCapsuleShadowStatus.ACTIVE:
                    raise
                evaluation = adapt_kr_day_capsule_management_evaluation(
                    request,
                    allow_market_blocked=True,
                )
            if latest is not None and latest.status is KrDayCapsuleShadowStatus.ACTIVE and (
                latest.capsule_id,
                latest.session_date,
                latest.symbol,
                latest.collection_cycle_id,
                latest.calendar_snapshot_id,
            ) != (
                evaluation.capsule_id,
                evaluation.session_date,
                evaluation.symbol,
                evaluation.collection_cycle_id,
                evaluation.calendar_snapshot_id,
            ):
                raise ValueError
            evaluations.append(evaluation)
        except (OSError, TypeError, ValidationError, ValueError):
            invalid_count += 1
    return tuple(evaluations), invalid_count


def _publish_receipt(output: Path | None, result: _CliResult) -> _CliResult:
    if output is None:
        return result
    _require_private_output_root(output)
    payload = canonical_experiment_ledger_json(result)
    receipt_id = hashlib.sha256(payload.encode()).hexdigest()
    publish_private_immutable_text(output / f"kr_day_capsule_shadow_{receipt_id}.json", payload + "\n")
    return result.model_copy(update={"receipt_id": receipt_id})


def _require_private_output_root(output: Path) -> None:
    if not output.is_absolute() or output.is_symlink():
        raise ValueError
    if not output.exists():
        parent = output.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError
        output.mkdir(mode=0o700)
    metadata = output.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError


def _blocked_result(invalid_count: int) -> _CliResult:
    return _CliResult(
        result="blocked",
        created_count=0,
        reused_count=0,
        invalid_request_count=invalid_count,
        events=(),
    )


def _emit(result: _CliResult) -> None:
    sys.stdout.write(canonical_experiment_ledger_json(result) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
