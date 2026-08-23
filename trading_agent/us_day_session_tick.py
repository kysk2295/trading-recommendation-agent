from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.us_day_source_models import CanonicalUsDaySource
from trading_agent.us_day_thesis_models import situation_id_for
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_STRING_PAYLOAD = TypeAdapter(dict[str, str])
_ROOT = Path(__file__).parents[1]


class UsDaySessionTickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scanner: Path
    articles: Path
    news_evidence: Path
    market_context: Path
    quotes: tuple[Path, ...] = Field(min_length=1)
    completed_ticks: tuple[Path, ...] = Field(min_length=1)
    outputs: Path
    evaluated_at: AwareDatetime
    version_store: Path | None = None
    production_manifest: Path | None = None
    strategy_manifest: Path | None = None
    experiment_ledger: Path | None = None
    day_model_responses: Path | None = None
    thesis_model_response: Path | None = None
    live_model_provider: str | None = Field(default=None, min_length=1)
    entry_cutoff_minutes: int = Field(default=15, ge=6, le=59)
    eod_minutes: int = Field(default=5, ge=1, le=5)


class UsDaySessionTickResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["accepted", "blocked"]
    stage: Literal["projection", "tick"]
    mutation: Literal["0"] = "0"
    session_id: str
    source: str | None = None
    situation_id: str | None = None
    tick_status: Literal["accepted", "blocked"] | None = None
    tick_phase: str | None = None
    reason: str | None = None
    receipt: str | None = None


def run_us_day_session_tick(request: UsDaySessionTickRequest) -> tuple[int, UsDaySessionTickResult]:
    """Compose immutable US session evidence projection with one existing Day Agent tick."""
    session_id = f"XNYS-{request.evaluated_at.astimezone(NEW_YORK).date().isoformat()}"
    source_root = request.outputs.expanduser().absolute() / "us_day" / "session_sources"
    projection = _run(
        (
            sys.executable,
            str(_ROOT / "run_us_day_source_projection.py"),
            "--scanner",
            str(request.scanner),
            "--articles",
            str(request.articles),
            "--news-evidence",
            str(request.news_evidence),
            "--market-context",
            str(request.market_context),
            *(value for path in request.quotes for value in ("--quote", str(path))),
            *(value for path in request.completed_ticks for value in ("--completed-tick", str(path))),
            "--output-root",
            str(source_root),
            "--now",
            request.evaluated_at.isoformat(),
        )
    )
    projection_payload = _payload(projection)
    if projection.returncode != 0 or projection_payload.get("status") != "ready":
        selected = _latest_post_close_source(source_root, request.evaluated_at, session_id)
        if selected is None:
            result = UsDaySessionTickResult(
                status="blocked",
                stage="projection",
                session_id=session_id,
                reason=projection_payload.get("reason", "source_projection_blocked"),
            )
            return _finalize(request.outputs, result)
        source_name, situation_id = selected
    elif projection_payload.get("session_id") != session_id:
        result = UsDaySessionTickResult(
            status="blocked",
            stage="projection",
            session_id=session_id,
            reason="source_session_mismatch",
        )
        return _finalize(request.outputs, result)
    else:
        source_name = projection_payload.get("source")
        situation_id = projection_payload.get("situation_id")
        if source_name is None or situation_id is None or Path(source_name).name != source_name:
            result = UsDaySessionTickResult(
                status="blocked",
                stage="projection",
                session_id=session_id,
                reason="source_projection_receipt_invalid",
            )
            return _finalize(request.outputs, result)
    tick = _run(
        (
            sys.executable,
            str(_ROOT / "run_us_day_agent_tick.py"),
            "--situation",
            str(source_root / source_name),
            "--outputs",
            str(request.outputs),
            *_option("--version-store", request.version_store),
            *_option("--production-manifest", request.production_manifest),
            *_option("--strategy-manifest", request.strategy_manifest),
            *_option("--experiment-ledger", request.experiment_ledger),
            *_option("--day-model-responses", request.day_model_responses),
            *_option("--thesis-model-response", request.thesis_model_response),
            *_option("--live-model-provider", request.live_model_provider),
            "--now",
            request.evaluated_at.isoformat(),
            "--entry-cutoff-minutes",
            str(request.entry_cutoff_minutes),
            "--eod-minutes",
            str(request.eod_minutes),
        )
    )
    tick_payload = _payload(tick)
    tick_status = tick_payload.get("status")
    status: Literal["accepted", "blocked"] = (
        "accepted" if tick.returncode == 0 and tick_status == "accepted" else "blocked"
    )
    result = UsDaySessionTickResult(
        status=status,
        stage="tick",
        session_id=session_id,
        source=source_name,
        situation_id=situation_id,
        tick_status=status,
        tick_phase=tick_payload.get("phase"),
        reason=None if status == "accepted" else tick_payload.get("reason", "day_agent_tick_blocked"),
    )
    return _finalize(request.outputs, result)


def _run(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(command, 2, "", "")


def _payload(completed: subprocess.CompletedProcess[str]) -> dict[str, str]:
    try:
        return _STRING_PAYLOAD.validate_json(completed.stdout)
    except ValidationError:
        return {"reason": "composition_child_output_invalid", "status": "blocked"}


def _option(flag: str, value: Path | str | None) -> tuple[str, ...]:
    return () if value is None else (flag, str(value))


def _latest_post_close_source(
    root: Path,
    evaluated_at: dt.datetime,
    session_id: str,
) -> tuple[str, str] | None:
    bounds = regular_session_bounds(evaluated_at.astimezone(NEW_YORK).date())
    if bounds is None or evaluated_at < bounds[1]:
        return None
    candidates: list[tuple[dt.datetime, str, str]] = []
    for path in root.glob("us_day_source_*.json"):
        try:
            source = CanonicalUsDaySource.model_validate_json(read_private_text(path))
        except (InvalidPrivateImmutableFileError, ValidationError, ValueError):
            continue
        observed_at = source.situation.evaluated_at.astimezone(dt.UTC)
        identity = situation_id_for(source.situation)
        if (
            source.situation.session_id == session_id
            and observed_at <= evaluated_at
            and evaluated_at - observed_at <= dt.timedelta(minutes=15)
            and path.name == f"us_day_source_{identity}.json"
        ):
            candidates.append((observed_at, path.name, identity))
    if not candidates:
        return None
    latest = max(item[0] for item in candidates)
    selected = tuple(item for item in candidates if item[0] == latest)
    return None if len(selected) != 1 else selected[0][1:]


def _publish_receipt(outputs: Path, result: UsDaySessionTickResult) -> UsDaySessionTickResult:
    canonical = result.model_dump_json(exclude={"receipt"})
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    name = f"us_day_session_tick_{digest}.json"
    published = result.model_copy(update={"receipt": name})
    try:
        _ = publish_private_immutable_text(
            outputs.expanduser().absolute() / "us_day" / "session_tick_receipts" / name,
            published.model_dump_json() + "\n",
        )
    except InvalidPrivateImmutableFileError:
        return result.model_copy(
            update={"status": "blocked", "reason": "session_tick_receipt_write_failed"}
        )
    return published


def _finalize(outputs: Path, result: UsDaySessionTickResult) -> tuple[int, UsDaySessionTickResult]:
    published = _publish_receipt(outputs, result)
    return (0 if published.status == "accepted" else 2), published


__all__ = ("UsDaySessionTickRequest", "UsDaySessionTickResult", "run_us_day_session_tick")
