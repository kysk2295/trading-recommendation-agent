from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from trading_agent.dashboard_autonomous_research import (
    AutonomousEnvironmentSpecV1,
    AutonomousTriggerV1,
    BudgetEnvelopeV1,
)
from trading_agent.dashboard_trigger_authority import (
    TriggerAuthorityStore,
    authority_record_for,
)
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeReader,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1, ResearchAgentTriggerKind
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_source_common import ResearchAgentEvidenceMaterial, canonical_model_json

SEOUL: Final = ZoneInfo("Asia/Seoul")
TRIGGER_TTL: Final = dt.timedelta(minutes=10)
MAX_SOURCE_AGE: Final = dt.timedelta(minutes=15)


class InvalidKrAutonomousBridgeError(RuntimeError):
    pass


def publish_kr_autonomous_triggers(
    outputs: Path,
    *,
    state_root: Path,
    pinned_code_sha: str,
    now: dt.datetime,
    cycle_store: ResearchAgentCycleStore | None = None,
) -> tuple[Path, ...]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidKrAutonomousBridgeError
    created: list[Path] = []
    for database in current_kr_databases(outputs, now.astimezone(SEOUL).date()):
        trigger = _latest_trigger(database, pinned_code_sha, now)
        if trigger is None:
            continue
        try:
            authority = TriggerAuthorityStore(state_root / "authorities")
            _ = authority.append(authority_record_for(trigger))
            evidence = (
                state_root
                / "authorities"
                / "evidence"
                / f"{trigger.trigger_id}.json"
            )
            _ = publish_private_immutable_text(
                evidence,
                _trigger_payload(database, trigger),
            )
            destination = (
                outputs
                / "kr_theme"
                / "autonomous_triggers"
                / f"{trigger.trigger_id}.autonomous-trigger.json"
            )
            if publish_private_immutable_text(destination, trigger.model_dump_json()):
                created.append(destination)
            if cycle_store is not None:
                _ = cycle_store.append_evidence(_trigger_evidence(trigger))
        except (InvalidPrivateImmutableFileError, OSError, RuntimeError) as error:
            raise InvalidKrAutonomousBridgeError from error
    return tuple(created)


def _trigger_evidence(trigger: AutonomousTriggerV1) -> ResearchAgentEvidenceV1:
    cycle_id = trigger.dedupe_key.removeprefix("kr-new-data-")
    return ResearchAgentEvidenceMaterial(
        family="opportunity_manager",
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"kr.authorized.{cycle_id}",
        observed_at=trigger.observed_at,
        available_at=trigger.authorized_at,
        market_id="kr_equities",
        canonical_payload=canonical_model_json(trigger),
    ).evidence()


def current_kr_databases(outputs: Path, session_date: dt.date) -> tuple[Path, ...]:
    root = outputs / "kr_theme" / "m3_live"
    return tuple(
        database
        for session in sorted(root.glob(f"{session_date.isoformat()}*"))
        if session.is_dir()
        and not session.is_symlink()
        and _private_regular(database := session / "kr_theme.sqlite3")
    )


def _latest_trigger(
    database: Path,
    pinned_code_sha: str,
    now: dt.datetime,
) -> AutonomousTriggerV1 | None:
    try:
        reader = KrThemeReader(database)
        cycles = tuple(
            cycle
            for cycle in reader.cycles()
            if cycle.completed_at <= now
            and now - cycle.completed_at <= MAX_SOURCE_AGE
        )
        if not cycles:
            return None
        cycle = max(cycles, key=lambda item: item.completed_at)
        runs = reader.source_runs(cycle.collection_cycle_id)
        receipt_ids = tuple(
            sorted(
                {
                    receipt_id
                    for run in runs
                    for receipt_id in run.receipt_ids
                }
            )
        )
        stored = {
            item.receipt.receipt_id: item.receipt
            for item in reader.source_receipts()
            if item.receipt.receipt_id in receipt_ids
        }
    except (InvalidKrThemeSourceError, OSError, ValueError) as error:
        raise InvalidKrAutonomousBridgeError from error
    if (
        not receipt_ids
        or len(receipt_ids) > 32
        or tuple(stored) != receipt_ids
    ):
        return None
    evidence_refs = tuple(
        sorted({stored[receipt_id].payload_sha256 for receipt_id in receipt_ids})
    )
    if not evidence_refs or len(evidence_refs) > 32:
        return None
    payload = json.dumps(
        {
            "cycle": cycle.model_dump(mode="json"),
            "runs": [run.model_dump(mode="json") for run in runs],
            "source_receipt_ids": receipt_ids,
            "evidence_refs": evidence_refs,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
    trigger_id = f"kr-new-data-{payload_sha256[:24]}"
    return AutonomousTriggerV1(
        trigger_id=trigger_id,
        trigger_type="new_data",
        authority="source_receipt",
        agent_family_id="opportunity_manager",
        source_receipt_ids=receipt_ids,
        evidence_refs=evidence_refs,
        observed_at=cycle.completed_at,
        authorized_at=now,
        expires_at=now + TRIGGER_TTL,
        policy_version="kr-regular-session-new-data-v1",
        dedupe_key=f"kr-new-data-{cycle.collection_cycle_id}",
        budget_envelope=BudgetEnvelopeV1(
            max_tokens=10_000,
            max_cost_microusd=1_000_000,
            max_runtime_seconds=300,
            max_model_processes=1,
        ),
        environment_spec=AutonomousEnvironmentSpecV1(
            pinned_code_sha=pinned_code_sha,
            allowed_read_roots=(
                "isolated_worktree",
                "source_evidence",
            ),
            allowed_write_roots=("experiment",),
            allowed_tools=(
                "read_evidence",
                "write_candidate",
                "run_tests",
            ),
            network_policy="model_provider_only",
            requested_read_paths=("source_evidence",),
            requested_write_paths=("experiment/candidate.json",),
            requested_tool_argv=(),
            requested_network_targets=(),
        ),
        payload_sha256=payload_sha256,
    )


def _trigger_payload(database: Path, trigger: AutonomousTriggerV1) -> str:
    reader = KrThemeReader(database)
    cycles = tuple(
        cycle
        for cycle in reader.cycles()
        if f"kr-new-data-{cycle.collection_cycle_id}" == trigger.dedupe_key
    )
    if len(cycles) != 1:
        raise InvalidKrAutonomousBridgeError
    cycle = cycles[0]
    runs = reader.source_runs(cycle.collection_cycle_id)
    payload = json.dumps(
        {
            "cycle": cycle.model_dump(mode="json"),
            "runs": [run.model_dump(mode="json") for run in runs],
            "source_receipt_ids": trigger.source_receipt_ids,
            "evidence_refs": trigger.evidence_refs,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if hashlib.sha256(payload.encode()).hexdigest() != trigger.payload_sha256:
        raise InvalidKrAutonomousBridgeError
    return payload


def _private_regular(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
    )


__all__ = (
    "InvalidKrAutonomousBridgeError",
    "current_kr_databases",
    "publish_kr_autonomous_triggers",
)
