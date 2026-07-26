from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import anyio
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

type AgentId = Literal[
    "kr-theme",
    "us-intraday",
    "us-systematic",
    "us-swing",
    "research",
    "delivery",
]

AGENT_ROLES: dict[AgentId, str] = {
    "kr-theme": "한국 테마·장중 관측 에이전트",
    "us-intraday": "미국 장중 관측 에이전트",
    "us-systematic": "미국 시스템 전략 에이전트",
    "us-swing": "미국 스윙 연구 에이전트",
    "research": "인과적 데이터·실험 연구 에이전트",
    "delivery": "추천 전달·운영 상태 에이전트",
}
MAX_RESPONSE_CHARS = 8_000


class InteractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    agent_id: AgentId
    command: str = Field(min_length=1, max_length=2_000)
    state: Literal["queued", "running"]
    response: None
    created_at: datetime
    updated_at: datetime


class DashboardInteractionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["interaction"]
    interaction: InteractionPayload


class PairingTicketMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["pairing_ticket"]
    path: str = Field(pattern=r"^/operator/pair/[A-Za-z0-9_-]{40,}$")


type DashboardEvent = Annotated[
    DashboardInteractionMessage | PairingTicketMessage,
    Field(discriminator="type"),
]


class InteractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["interaction_result"] = "interaction_result"
    interaction_id: str
    state: Literal["running", "completed", "failed"]
    response: str | None = Field(max_length=MAX_RESPONSE_CHARS)


_event_adapter = TypeAdapter(DashboardEvent)


def parse_dashboard_event(raw: str) -> DashboardEvent:
    return _event_adapter.validate_json(raw)


async def execute_interaction(
    interaction: InteractionPayload,
    *,
    hermes_executable: Path,
    worktree: Path,
    timeout_seconds: float = 900,
) -> InteractionResult:
    try:
        with anyio.fail_after(timeout_seconds):
            completed = await anyio.run_process(
                _hermes_argv(hermes_executable, interaction),
                cwd=worktree,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
    except TimeoutError:
        return _failed(interaction.id, "명령 실행 제한 시간을 초과했습니다.")
    except OSError:
        return _failed(interaction.id, "Hermes 실행기를 시작하지 못했습니다.")
    if completed.returncode != 0:
        return _failed(interaction.id, f"Hermes 실행이 종료 코드 {completed.returncode}로 실패했습니다.")
    response = completed.stdout.decode("utf-8", errors="replace").strip()
    if not response:
        return _failed(interaction.id, "Hermes가 응답 없이 종료되었습니다.")
    return InteractionResult(
        interaction_id=interaction.id,
        state="completed",
        response=response[:MAX_RESPONSE_CHARS],
    )


def _hermes_argv(executable: Path, interaction: InteractionPayload) -> tuple[str, ...]:
    return (str(executable), "-z", _prompt(interaction))


def _prompt(interaction: InteractionPayload) -> str:
    return (
        f"당신은 {AGENT_ROLES[interaction.agent_id]}입니다. "
        "현재 trading-recommendation-agent integration worktree에서 AGENTS.md와 저장소 정책을 먼저 따르십시오. "
        "사용자가 대시보드에서 명시적으로 보낸 다음 목표를 처리하고, 관측한 근거와 결과를 한국어로 간결하게 "
        "답하십시오. 실제 자금 거래를 실행하지 마십시오. 주문 관련 작업은 명시적으로 arm된 Paper 계좌의 "
        "기존 안전 게이트를 통과하는 경우에만 허용됩니다. 자격증명·계좌 식별정보·로컬 비밀을 응답에 포함하지 "
        f"마십시오.\n\n사용자 명령:\n{interaction.command}"
    )


def _failed(interaction_id: str, response: str) -> InteractionResult:
    return InteractionResult(
        interaction_id=interaction_id,
        state="failed",
        response=response,
    )
