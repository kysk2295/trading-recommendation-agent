from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.private_stable_report import write_private_stable_report

type CloseStatus = Literal["completed", "no_action", "blocked"]
type CloseStage = Literal[
    "binding",
    "calendar",
    "request",
    "report",
    "policy",
    "loop",
    "summary",
    "completion",
]


class KrDayCloseServiceResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: CloseStatus
    reason: str
    stage: CloseStage
    session_date: dt.date | None
    complete: bool
    report_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    metrics_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    policy_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    challenger_count: Literal[0, 1] = 0
    summary_inserted: int
    mutation_count: Literal[0] = 0
    provider_read_only: Literal[True] = True


class KrDayCloseServiceHealth(KrDayCloseServiceResult):
    schema_version: Literal[1] = 1
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: AwareDatetime


class KrDayCloseCompletionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    session_date: dt.date
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    calendar_snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    challenger_count: Literal[0, 1] = 0
    summary_source_event_id: str
    completed_at: AwareDatetime
    mutation_count: Literal[0] = 0
    provider_read_only: Literal[True] = True


def write_kr_day_close_health(
    root: Path,
    health: KrDayCloseServiceHealth,
) -> Path:
    path = root / "kr-day-close-health.json"
    write_private_stable_report(path, canonical_experiment_ledger_json(health) + "\n")
    return path


def publish_kr_day_close_completion(
    root: Path,
    receipt: KrDayCloseCompletionReceipt,
) -> tuple[Path, bool]:
    path = root / f"kr-day-close-{receipt.session_date.isoformat()}.json"
    created = publish_private_immutable_text(
        path,
        canonical_experiment_ledger_json(receipt) + "\n",
    )
    return path, created


__all__ = (
    "CloseStage",
    "KrDayCloseCompletionReceipt",
    "KrDayCloseServiceHealth",
    "KrDayCloseServiceResult",
    "publish_kr_day_close_completion",
    "write_kr_day_close_health",
)
