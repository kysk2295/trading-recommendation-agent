from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import assert_never

from trading_agent.research_agent_soak_models import (
    ResearchAgentSoakStatus,
    SoakCheckpoint,
    SoakCheckpointKind,
    SoakEvidenceMode,
    SoakState,
)

_REQUIRED_SECONDS = 24 * 60 * 60
_EXPIRATION_SECONDS = 72 * 60 * 60


@dataclass(frozen=True, slots=True)
class _ActualConditions:
    elapsed: bool
    restarted: bool
    rebooted: bool
    outage_recovered: bool


def build_research_agent_soak_status(records: tuple[SoakCheckpoint, ...], now: dt.datetime) -> ResearchAgentSoakStatus:
    first = records[0].payload
    actual = first.evidence_mode is SoakEvidenceMode.ACTUAL
    elapsed = max(0, int((now.astimezone(dt.UTC) - first.recorded_at.astimezone(dt.UTC)).total_seconds()))
    restarted = actual and any(
        item.payload.kind is SoakCheckpointKind.PROCESS_RESTART
        and item.payload.invocation_sha256 != first.invocation_sha256
        for item in records[1:]
    )
    rebooted = actual and any(
        item.payload.kind is SoakCheckpointKind.REBOOT_RECOVERED and item.payload.boot_sha256 != first.boot_sha256
        for item in records[1:]
    )
    outage = actual and _ordered_provider_outage(records)
    conditions = _ActualConditions(
        elapsed=actual and elapsed >= _REQUIRED_SECONDS,
        restarted=restarted,
        rebooted=rebooted,
        outage_recovered=outage,
    )
    window_exceeded = elapsed > _EXPIRATION_SECONDS
    blockers = _blockers(actual, conditions, window_exceeded)
    complete = not blockers
    state = SoakState.EXPIRED if window_exceeded else SoakState.COMPLETE if complete else SoakState.COLLECTING
    return ResearchAgentSoakStatus(
        status=state,
        evidence_mode=first.evidence_mode,
        elapsed_seconds=elapsed,
        checkpoint_count=len(records),
        head_sha256=records[-1].checkpoint_sha256,
        actual_restart_observed=restarted,
        actual_reboot_observed=rebooted,
        actual_provider_outage_observed=outage,
        blockers=blockers,
    )


def _blockers(actual: bool, conditions: _ActualConditions, window_exceeded: bool) -> tuple[str, ...]:
    blockers: list[str] = []
    if window_exceeded:
        blockers.append("actual_collection_window_exceeded")
    if not actual:
        blockers.append("controlled_fixture_ineligible_for_actual_completion")
    if not conditions.elapsed:
        blockers.append("actual_24_hours_missing")
    if not conditions.restarted:
        blockers.append("actual_process_restart_missing")
    if not conditions.rebooted:
        blockers.append("actual_system_reboot_missing")
    if not conditions.outage_recovered:
        blockers.append("actual_provider_outage_missing")
    return tuple(blockers)


def _ordered_provider_outage(records: tuple[SoakCheckpoint, ...]) -> bool:
    observed = False
    for item in records[1:]:
        match item.payload.kind:
            case SoakCheckpointKind.PROVIDER_OUTAGE_OBSERVED:
                observed = True
            case SoakCheckpointKind.PROVIDER_OUTAGE_RECOVERED:
                if observed:
                    return True
            case (
                SoakCheckpointKind.PREPARED
                | SoakCheckpointKind.HEARTBEAT
                | SoakCheckpointKind.PROCESS_RESTART
                | SoakCheckpointKind.REBOOT_RECOVERED
            ):
                continue
            case unreachable:
                assert_never(unreachable)
    return False


__all__ = ("build_research_agent_soak_status",)
