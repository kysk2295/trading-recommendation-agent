from __future__ import annotations

import datetime as dt
import json
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, override

import typer
from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter, ValidationError

from trading_agent.day_agent_task_store import DayAgentTaskStore
from trading_agent.day_agent_tool_models import DayAgentReasoningRequest, DayAgentReasoningResponse
from trading_agent.day_agent_tool_runtime import DayAgentToolRuntime
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError, read_private_text
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.store import PaperStore
from trading_agent.us_day_agent_service import (
    CanonicalUsDaySource,
    LocalUsDaySourceReader,
    UsDayAgentServiceConfig,
    UsDayAgentServiceError,
    UsDayLocalStores,
    UsDayModelBindings,
    UsDayProductionConfig,
    UsDayStrategyBinding,
    build_us_day_agent_service,
    session_phase_at,
)
from trading_agent.us_day_thesis_models import UsDayPlaybook
from trading_agent.us_day_thesis_store import UsDayThesisStore
from trading_agent.us_equity_calendar import NEW_YORK

_APP = typer.Typer(add_completion=False, pretty_exceptions_enable=False)
_DAY_RESPONSES = TypeAdapter(tuple[DayAgentReasoningResponse, ...])


class _SourceHeader(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    session_id: str
    evaluated_at: AwareDatetime


class _CliError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @override
    def __str__(self) -> str:
        return self.reason


class _BoundDayResponses:
    role: Literal["reasoning", "coding"] = "reasoning"

    def __init__(self, responses: tuple[DayAgentReasoningResponse, ...]) -> None:
        self._responses = responses
        self._index = 0

    def next_step(self, request: DayAgentReasoningRequest) -> DayAgentReasoningResponse:
        if self._index >= len(self._responses):
            raise _CliError("day_model_responses_exhausted")
        response = self._responses[self._index]
        self._index += 1
        return response


class _BoundThesisResponse:
    def __init__(self, response: Mapping[str, object]) -> None:
        self._response = dict(response)

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        return self._response


def _now(value: str | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.UTC)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _CliError("now_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _CliError("now_invalid")
    return parsed.astimezone(dt.UTC)


def _source(path: Path, now: dt.datetime) -> CanonicalUsDaySource:
    try:
        metadata = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
        ):
            raise _CliError("situation_metadata_invalid")
        payload = path.read_bytes()
        decoded = TypeAdapter(dict[str, object]).validate_json(payload)
        header = _SourceHeader.model_validate(decoded.get("situation", decoded))
    except FileNotFoundError:
        raise _CliError("situation_missing") from None
    except OSError:
        raise _CliError("situation_read_failed") from None
    except ValidationError:
        raise _CliError("situation_invalid") from None
    observed_at = header.evaluated_at.astimezone(dt.UTC)
    if (
        header.session_id != f"XNYS-{now.astimezone(NEW_YORK).date().isoformat()}"
        or observed_at > now
        or now - observed_at > dt.timedelta(minutes=15)
    ):
        raise _CliError("situation_stale")
    try:
        return CanonicalUsDaySource.model_validate_json(payload)
    except (ValidationError, ValueError):
        raise _CliError("situation_invalid") from None


def _model_bindings(day_path: Path, thesis_path: Path, clock: dt.datetime) -> UsDayModelBindings:
    try:
        day = _DAY_RESPONSES.validate_json(read_private_text(day_path))
        thesis = TypeAdapter(dict[str, object]).validate_json(read_private_text(thesis_path))
    except (InvalidPrivateImmutableFileError, ValidationError, ValueError):
        raise _CliError("model_bindings_invalid") from None
    if not day:
        raise _CliError("model_bindings_invalid")
    return UsDayModelBindings(
        _BoundDayResponses(day),
        _BoundThesisResponse(thesis),
        DayAgentToolRuntime((), lambda: clock),
    )


def _service(
    outputs: Path,
    day_model_responses: Path,
    thesis_model_response: Path,
    evaluated_at: dt.datetime,
    entry_cutoff_minutes: int,
    eod_minutes: int,
):
    root = outputs.expanduser().absolute()
    private = root / "us_day"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    private.chmod(0o700)
    playbook = UsDayPlaybook(playbook_id="leader_breakout", title="대장주 돌파", entry_type="stop_trigger")
    lane = StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id=playbook.playbook_id,
    )
    thesis_store = UsDayThesisStore(private / "theses")
    return build_us_day_agent_service(
        UsDayProductionConfig(
            stores=UsDayLocalStores(
                root,
                DayAgentTaskStore(private / "day_agent.sqlite3"),
                thesis_store,
                PaperStore(private / "paper.sqlite3"),
                DayAgentVersionStore(private / "versions.sqlite3"),
            ),
            models=_model_bindings(day_model_responses, thesis_model_response, evaluated_at),
            strategy=UsDayStrategyBinding(playbook.playbook_id, lane, (playbook,)),
            source_reader=LocalUsDaySourceReader(),
        ),
        UsDayAgentServiceConfig(
            private / "tick_receipts",
            dt.timedelta(minutes=entry_cutoff_minutes),
            dt.timedelta(minutes=eod_minutes),
        ),
        lambda: evaluated_at,
    )


def _emit(payload: dict[str, str]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


@_APP.command()
def main(
    situation: Annotated[Path, typer.Option(exists=False, dir_okay=False, readable=True)],
    outputs: Annotated[Path, typer.Option(file_okay=False)] = Path(".private/us-day-agent"),
    day_model_responses: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    thesis_model_response: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    now: Annotated[str | None, typer.Option()] = None,
    entry_cutoff_minutes: Annotated[int, typer.Option(min=6, max=59)] = 15,
    eod_minutes: Annotated[int, typer.Option(min=1, max=5)] = 5,
) -> None:
    try:
        evaluated_at = _now(now)
    except _CliError as error:
        _emit({"phase": "closed", "reason": str(error), "status": "blocked"})
        raise typer.Exit(2) from None
    try:
        _ = _source(situation, evaluated_at)
        if day_model_responses is None or thesis_model_response is None:
            raise _CliError("model_bindings_required")
        result = _service(
            outputs,
            day_model_responses,
            thesis_model_response,
            evaluated_at,
            entry_cutoff_minutes,
            eod_minutes,
        ).tick_from_source(situation.expanduser().absolute())
        _emit(result.compact())
        if result.status == "blocked":
            raise typer.Exit(2)
    except (_CliError, UsDayAgentServiceError) as error:
        _emit({"phase": session_phase_at(evaluated_at).value, "reason": str(error), "status": "blocked"})
        raise typer.Exit(2) from None


if __name__ == "__main__":
    _APP()
