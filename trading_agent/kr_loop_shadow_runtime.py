from __future__ import annotations

import datetime as dt
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, override

from pydantic import ValidationError

from trading_agent.autonomous_memory_models import AutonomousMemoryScope
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.kr_autonomous_outcome_models import (
    InvalidKrAutonomousOutcomeError,
    KrAutonomousOutcomeMemory,
    KrLoopFailureCode,
)
from trading_agent.kr_loop_engineer_receipts import KrLoopShadowReceipt
from trading_agent.kr_loop_evaluation import InvalidKrLoopEvaluationError, build_shadow_receipt
from trading_agent.research_agent_service_config import (
    InvalidResearchAgentServiceConfigError,
    ResearchAgentServiceConfig,
    write_research_agent_service_config,
)

ShadowRunner = Callable[[tuple[str, ...], dict[str, str]], int]


class InvalidKrLoopShadowRuntimeError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop shadow runtime failed"


@dataclass(frozen=True, slots=True)
class KrLoopShadowSessionResult:
    status: Literal["recorded", "evidence_pending"]
    receipt: KrLoopShadowReceipt | None


def run_shadow_session(
    *,
    base_config: ResearchAgentServiceConfig,
    champion_root: Path,
    challenger_root: Path,
    shadow_root: Path,
    candidate_id: str,
    failure_code: KrLoopFailureCode,
    session_date: dt.date,
    observed_at: dt.datetime,
    runner: ShadowRunner | None = None,
) -> KrLoopShadowSessionResult:
    try:
        lane_root = shadow_root.expanduser().absolute() / candidate_id / session_date.isoformat()
        active = _run if runner is None else runner
        champion = _run_lane(base_config, champion_root, lane_root / "champion", active)
        challenger = _run_lane(base_config, challenger_root, lane_root / "challenger", active)
        try:
            receipt = build_shadow_receipt(
                failure_code=failure_code,
                session_date=session_date,
                champion=champion,
                challenger=challenger,
                observed_at=observed_at,
            )
        except InvalidKrLoopEvaluationError:
            return KrLoopShadowSessionResult("evidence_pending", None)
        return KrLoopShadowSessionResult("recorded", receipt)
    except (
        InvalidKrAutonomousOutcomeError,
        InvalidKrLoopShadowRuntimeError,
        InvalidResearchAgentServiceConfigError,
        OSError,
        subprocess.SubprocessError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrLoopShadowRuntimeError from None


def _run_lane(
    base: ResearchAgentServiceConfig,
    source_root: Path,
    lane_root: Path,
    runner: ShadowRunner,
) -> tuple[KrAutonomousOutcomeMemory, ...]:
    source = source_root.expanduser().absolute()
    script = source / "run_research_agent_runtime.py"
    if source.is_symlink() or not source.is_dir() or script.is_symlink() or not script.is_file():
        raise InvalidKrLoopShadowRuntimeError
    config = _lane_config(base, source, lane_root)
    config_path = lane_root / "research-agent.json"
    _ = write_research_agent_service_config(config_path, config)
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(source) if not existing else str(source) + os.pathsep + existing
    command = (
        str(base.systematic.python_executable),
        str(script),
        "cycle",
        "--config",
        str(config_path),
    )
    if runner(command, environment) != 0:
        raise InvalidKrLoopShadowRuntimeError
    memory_path = config.output_root / "autonomous-supervisor" / "memory.sqlite3"
    records = AutonomousMemoryStore(memory_path).reader().recent(AutonomousMemoryScope.MARKET, limit=128)
    outcomes: list[KrAutonomousOutcomeMemory] = []
    for record in records:
        try:
            outcomes.append(KrAutonomousOutcomeMemory.model_validate_json(record.summary))
        except (InvalidKrAutonomousOutcomeError, ValidationError, ValueError):
            continue
    return tuple(outcomes)


def _lane_config(base: ResearchAgentServiceConfig, source: Path, lane: Path) -> ResearchAgentServiceConfig:
    systematic_root = lane / "systematic"
    systematic = base.systematic.model_copy(
        update={
            "project_root": source,
            "experiment_ledger": systematic_root / "experiments.sqlite3",
            "receipt_root": systematic_root / "receipts",
            "strategy_root": systematic_root / "strategies",
            "manifest_root": systematic_root / "manifests",
            "queue_root": systematic_root / "queue",
            "artifact_root": systematic_root / "artifacts",
            "review_root": systematic_root / "reviews",
            "runs_root": systematic_root / "runs",
        }
    )
    updates: dict[str, object] = {
        "project_root": source,
        "cycle_database": lane / "cycles.sqlite3",
        "output_root": lane / "output",
        "hermes_database": lane / "hermes.sqlite3",
        "systematic": systematic,
    }
    if base.schema_version == 4:
        updates["kr_social_signal_database"] = lane / "kr-social-signals.sqlite3"
    return ResearchAgentServiceConfig.model_validate(base.model_copy(update=updates).model_dump(mode="python"))


def _run(command: tuple[str, ...], environment: dict[str, str]) -> int:
    return subprocess.run(
        command,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        timeout=3_600,
    ).returncode


__all__ = (
    "InvalidKrLoopShadowRuntimeError",
    "KrLoopShadowSessionResult",
    "ShadowRunner",
    "run_shadow_session",
)
