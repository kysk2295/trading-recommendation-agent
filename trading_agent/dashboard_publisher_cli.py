from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import TypeAdapter, ValidationError

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_autonomous_publisher import (
    DEFAULT_AUTONOMOUS_STATE,
    InvalidAutonomousTriggerFixtureError,
    execute_autonomous_fixture,
)
from trading_agent.dashboard_hermes_sessions import HermesSessionBindingStore

_family_adapter = TypeAdapter(AgentFamilyId)


def register_execution_commands(app: typer.Typer, interactive_state: Path) -> None:
    @app.command("autonomous-agent", help="typed trigger 한 건을 격리된 autonomous 연구 task로 실행합니다.")
    def autonomous_agent(
        trigger_fixture: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
        state_root: Annotated[Path, typer.Option()] = DEFAULT_AUTONOMOUS_STATE,
        dry_run: Annotated[bool, typer.Option(help="외부 relay 전송 없이 로컬 control plane만 검증")] = False,
        expect_cleanup: Annotated[bool, typer.Option(help="격리 환경 cleanup receipt 필수")] = False,
    ) -> None:
        del dry_run
        try:
            outcome = execute_autonomous_fixture(trigger_fixture, state_root=state_root)
        except InvalidAutonomousTriggerFixtureError as error:
            raise typer.BadParameter("invalid_autonomous_trigger", param_hint="--trigger-fixture") from error
        if expect_cleanup and not outcome.cleanup_completed:
            typer.echo("AUTONOMOUS_BLOCKED model_processes=0 receipt=1")
            raise typer.Exit(code=1)
        if outcome.state == "completed" and outcome.claim_created:
            typer.echo("AUTONOMOUS_OK claims=1 model_processes=1 duplicate_launches=0 evidence=append_only cleanup=1")
            return
        typer.echo(f"AUTONOMOUS_BLOCKED model_processes={outcome.model_processes} receipt=1")
        if outcome.claim_created or outcome.state not in {"blocked", "completed", "failed", "uncertain"}:
            raise typer.Exit(code=1)

    @app.command("reset-conversation", help="선택한 family의 로컬 Hermes 대화 binding만 초기화합니다.")
    def reset_conversation(
        family: Annotated[str, typer.Option()],
        state_root: Annotated[Path, typer.Option()] = interactive_state,
    ) -> None:
        try:
            family_id = _family_adapter.validate_python(family)
        except ValidationError as error:
            raise typer.BadParameter("invalid_agent_family", param_hint="--family") from error
        HermesSessionBindingStore(state_root / "hermes-sessions").reset(family_id)
        typer.echo(f"RESET_OK family={family_id}")


__all__ = ("register_execution_commands",)
