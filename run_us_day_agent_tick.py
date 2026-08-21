from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, override

import typer
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from trading_agent.us_day_agent_service import (
    UsDayAgentService,
    UsDayAgentServiceConfig,
    UsDayAgentServiceError,
    UsDayAgentTickRequest,
    UsDayAgentTickResult,
    session_phase_at,
    tick_id_for,
)
from trading_agent.us_equity_calendar import NEW_YORK

_MAX_SITUATION_AGE: Final = dt.timedelta(minutes=15)
_APP = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


class _SituationHeader(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    session_id: str
    evaluated_at: AwareDatetime


class _PaperExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paper_status: str


@dataclass(frozen=True, slots=True)
class _CliError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class _ExecutableVertical:
    executable: Path
    reasoning_model: str

    def premarket(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        return self._run("premarket", request)

    def recover_paper(self, request: UsDayAgentTickRequest) -> None:
        completed = self._invoke("recover", request)
        if completed.returncode != 0:
            raise _CliError("paper_recovery_failed")

    def regular(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        return self._run("regular", request)

    def publish_regular(self, request: UsDayAgentTickRequest, result: UsDayAgentTickResult) -> None:
        if self._invoke("publish", request).returncode != 0:
            raise _CliError("regular_publication_failed")

    def execute_paper(self, request: UsDayAgentTickRequest, result: UsDayAgentTickResult) -> str:
        completed = self._invoke("paper", request)
        if completed.returncode != 0:
            raise _CliError("paper_execution_failed")
        try:
            return _PaperExecution.model_validate_json(completed.stdout).paper_status
        except ValidationError:
            raise _CliError("paper_output_invalid") from None

    def cutoff(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        return self._run("entry_cutoff", request)

    def eod(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        return self._run("eod", request)

    def post_close(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        return self._run("post_close", request)

    def _run(self, operation: str, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        completed = self._invoke(operation, request)
        if completed.returncode != 0:
            raise _CliError("vertical_executable_failed")
        try:
            return UsDayAgentTickResult.model_validate_json(completed.stdout)
        except ValidationError:
            raise _CliError("vertical_output_invalid") from None

    def _invoke(self, operation: str, request: UsDayAgentTickRequest) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                (
                    str(self.executable),
                    "--operation",
                    operation,
                    "--situation",
                    str(request.situation_path),
                    "--reasoning-model",
                    self.reasoning_model,
                    "--evaluated-at",
                    request.evaluated_at.isoformat(),
                    "--tick-id",
                    tick_id_for(request),
                ),
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            raise _CliError("vertical_executable_timeout") from None
        except OSError:
            raise _CliError("vertical_executable_unavailable") from None


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


def _read_situation(path: Path, now: dt.datetime) -> tuple[_SituationHeader, str]:
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
        header = _SituationHeader.model_validate_json(payload)
    except FileNotFoundError:
        raise _CliError("situation_missing") from None
    except OSError:
        raise _CliError("situation_read_failed") from None
    except ValidationError:
        raise _CliError("situation_invalid") from None
    observed_at = header.evaluated_at.astimezone(dt.UTC)
    expected_session = f"XNYS-{now.astimezone(NEW_YORK).date().isoformat()}"
    if now < observed_at or now - observed_at > _MAX_SITUATION_AGE or header.session_id != expected_session:
        raise _CliError("situation_stale")
    return header, hashlib.sha256(payload).hexdigest()


def _emit(payload: dict[str, str]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


@_APP.command()
def main(
    situation: Annotated[Path, typer.Option(exists=False, dir_okay=False, readable=True)],
    outputs: Annotated[Path, typer.Option(file_okay=False)] = Path(".private/us-day-agent"),
    agent_executable: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    reasoning_model: Annotated[str, typer.Option()] = "reasoning",
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
        _, source_sha256 = _read_situation(situation, evaluated_at)
        if agent_executable is None:
            raise _CliError("agent_executable_required")
        result = UsDayAgentService(
            UsDayAgentServiceConfig(
                receipt_root=outputs / "us_day" / "tick_receipts",
                entry_cutoff_before_close=dt.timedelta(minutes=entry_cutoff_minutes),
                eod_before_close=dt.timedelta(minutes=eod_minutes),
            ),
            _ExecutableVertical(agent_executable.expanduser().absolute(), reasoning_model),
            clock=lambda: evaluated_at,
        ).tick_from_source(situation.expanduser().absolute(), source_sha256)
        _emit(result.compact())
        if result.status == "blocked":
            raise typer.Exit(2)
    except (_CliError, UsDayAgentServiceError) as error:
        _emit({"phase": session_phase_at(evaluated_at).value, "reason": str(error), "status": "blocked"})
        raise typer.Exit(2) from None


if __name__ == "__main__":
    _APP()
