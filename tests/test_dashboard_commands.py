from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.dashboard_commands import (
    DashboardInteractionMessage,
    execute_interaction,
    parse_dashboard_event,
)


def _message() -> DashboardInteractionMessage:
    return DashboardInteractionMessage.model_validate(
        {
            "type": "interaction",
            "interaction": {
                "id": "019c0014-f0f5-7000-8000-000000000001",
                "agent_id": "research",
                "command": "현재 실제 데이터 결손을 한 문장으로 설명해줘",
                "state": "queued",
                "response": None,
                "created_at": "2026-07-26T04:00:00Z",
                "updated_at": "2026-07-26T04:00:00Z",
            },
        }
    )


def test_dashboard_command_parser_rejects_unknown_agents() -> None:
    raw = _message().model_dump_json().replace('"research"', '"live-order"')

    with pytest.raises(ValidationError):
        parse_dashboard_event(raw)


@pytest.mark.anyio
async def test_dashboard_command_executes_one_bounded_oneshot_without_a_shell(
    tmp_path: Path,
) -> None:
    message = _message()

    result = await execute_interaction(
        message.interaction,
        hermes_executable=Path("/bin/echo"),
        worktree=tmp_path,
        timeout_seconds=5,
    )

    assert result.type == "interaction_result"
    assert result.interaction_id == message.interaction.id
    assert result.state == "completed"
    assert result.response is not None
    assert "-z" in result.response
    assert "실제 자금 거래를 실행하지 마십시오" in result.response
