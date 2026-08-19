from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Literal, Self

from pydantic import Field, model_validator

from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_results import ResearchAttempt, TerminalResearchResult
from trading_agent.strategy_research_types import CanonicalModel, ResearchAgentId, aware

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class StrategyResearchLedgerError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ExactHoldoutMetric(CanonicalModel):
    name: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    lower: float = Field(allow_inf_nan=False)
    upper: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if not self.lower <= self.value <= self.upper:
            raise StrategyResearchLedgerError("holdout_metric_interval_invalid")
        return self


class HoldoutReveal(CanonicalModel):
    reveal_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    seal_id: str = Field(min_length=1)
    commitment_sha256: str = Field(pattern=_SHA256_PATTERN)
    reviewer_id: str = Field(min_length=1)
    exact_metrics: tuple[ExactHoldoutMetric, ...] = Field(min_length=1)
    sanitized_result: TerminalResearchResult
    revealed_at: dt.datetime

    @model_validator(mode="after")
    def validate_reveal(self) -> Self:
        if not aware(self.revealed_at) or self.sanitized_result.hypothesis_id != self.hypothesis_id:
            raise StrategyResearchLedgerError("holdout_reveal_invalid")
        return self


class AgentResearchStateEvent(CanonicalModel):
    event_id: str = Field(min_length=1)
    agent_id: ResearchAgentId
    sequence: int = Field(ge=1)
    last_event_id: str = Field(min_length=1)
    last_available_at: dt.datetime
    version: int = Field(ge=1)
    hypothesis_id: str | None
    attempt_id: str | None
    state: str = Field(min_length=1)
    lease_until: dt.datetime | None
    checkpoint_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    retry_count: int = Field(ge=0)
    next_retry_at: dt.datetime | None
    next_due_at: dt.datetime | None = None
    next_maturity_at: dt.datetime | None = None
    reason: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    shadow_observation_id: str | None = None
    shadow_observation_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    shadow_result_id: str | None = None
    shadow_sample_count: int = Field(default=0, ge=0)
    shadow_sample_target: int = Field(default=0, ge=0)
    shadow_information_sufficient: bool = False
    owner_approval_required: bool = False
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        timestamps = tuple(
            value
            for value in (
                self.last_available_at,
                self.lease_until,
                self.next_retry_at,
                self.next_due_at,
                self.next_maturity_at,
            )
            if value
        )
        if not all(aware(value) for value in timestamps):
            raise StrategyResearchLedgerError("agent_state_time_invalid")
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise StrategyResearchLedgerError("agent_state_evidence_invalid")
        if self.trading_authority or self.profitability_claim:
            raise StrategyResearchLedgerError("agent_state_authority_forbidden")
        return self


def register_preregistration(connection: sqlite3.Connection, manifest: PreregistrationManifest) -> bool:
    try:
        manifest = PreregistrationManifest.model_validate(manifest.model_dump(mode="python"))
    except ValueError:
        raise StrategyResearchLedgerError("preregistration_invalid") from None
    payload = manifest.model_dump_json()
    row = connection.execute(
        "SELECT payload_json FROM strategy_research_preregistrations WHERE hypothesis_id=?",
        (manifest.hypothesis.hypothesis_id,),
    ).fetchone()
    if row is not None:
        if row == (payload,):
            return False
        raise StrategyResearchLedgerError("preregistration_conflict")
    parent_id = manifest.hypothesis.parent_hypothesis_id
    if parent_id is not None:
        parent = connection.execute(
            "SELECT search_family_id FROM strategy_research_preregistrations WHERE hypothesis_id=?",
            (parent_id,),
        ).fetchone()
        if parent != (manifest.hypothesis.search_family_id,):
            raise StrategyResearchLedgerError("lineage_parent_mismatch")
    sealed = manifest.hypothesis.holdout_period_sealed_ref
    try:
        _ = connection.execute(
            "INSERT INTO strategy_research_preregistrations VALUES (?,?,?,?,?,?,?)",
            (
                manifest.content_sha256,
                manifest.hypothesis.hypothesis_id,
                parent_id,
                manifest.hypothesis.search_family_id,
                manifest.hypothesis.agent_id.value,
                manifest.hypothesis.protocol_version,
                payload,
            ),
        )
        _ = connection.execute(
            "INSERT INTO strategy_research_holdout_seals VALUES (?,?,?,?)",
            (sealed.seal_id, manifest.hypothesis.hypothesis_id, sealed.commitment_sha256, sealed.model_dump_json()),
        )
    except sqlite3.IntegrityError as error:
        raise StrategyResearchLedgerError("preregistration_conflict") from error
    return True


def append_attempt(connection: sqlite3.Connection, attempt: ResearchAttempt) -> bool:
    try:
        attempt = ResearchAttempt.model_validate(attempt.model_dump(mode="python"))
    except ValueError:
        raise StrategyResearchLedgerError("attempt_invalid") from None
    parent = connection.execute(
        "SELECT payload_json FROM strategy_research_preregistrations WHERE hypothesis_id=?",
        (attempt.hypothesis_id,),
    ).fetchone()
    seal = connection.execute(
        "SELECT 1 FROM strategy_research_holdout_seals WHERE hypothesis_id=?",
        (attempt.hypothesis_id,),
    ).fetchone()
    if parent is None or seal is None:
        raise StrategyResearchLedgerError("preregistration_missing")
    try:
        manifest = PreregistrationManifest.model_validate_json(parent[0])
    except ValueError:
        raise StrategyResearchLedgerError("stored_preregistration_invalid") from None
    if (
        attempt.code_sha256 != manifest.hypothesis.code_sha256
        or attempt.data_manifest_sha256 != manifest.hypothesis.data_manifest_sha256
    ):
        raise StrategyResearchLedgerError("attempt_protocol_mismatch")
    payload = attempt.model_dump_json()
    row = connection.execute(
        "SELECT payload_json FROM strategy_research_attempts WHERE attempt_id=?",
        (attempt.attempt_id,),
    ).fetchone()
    if row is not None:
        if row == (payload,):
            return False
        raise StrategyResearchLedgerError("attempt_conflict")
    try:
        _ = connection.execute(
            "INSERT INTO strategy_research_attempts VALUES (?,?,?,?,?,?)",
            (
                attempt.content_sha256,
                attempt.attempt_id,
                attempt.hypothesis_id,
                attempt.branch_index,
                attempt.status.value,
                payload,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise StrategyResearchLedgerError("attempt_conflict") from error
    return True


def append_agent_state(connection: sqlite3.Connection, event: AgentResearchStateEvent) -> bool:
    try:
        event = AgentResearchStateEvent.model_validate(event.model_dump(mode="python"))
    except ValueError:
        raise StrategyResearchLedgerError("agent_state_invalid") from None
    payload = event.model_dump_json()
    row = connection.execute(
        "SELECT payload_json FROM strategy_research_agent_state_events WHERE event_id=?",
        (event.event_id,),
    ).fetchone()
    if row is not None:
        if row == (payload,):
            return False
        raise StrategyResearchLedgerError("agent_state_conflict")
    try:
        _ = connection.execute(
            "INSERT INTO strategy_research_agent_state_events VALUES (?,?,?,?,?)",
            (event.content_sha256, event.event_id, event.agent_id.value, event.sequence, payload),
        )
    except sqlite3.IntegrityError as error:
        raise StrategyResearchLedgerError("agent_state_conflict") from error
    return True


def reveal_holdout(connection: sqlite3.Connection, reveal: HoldoutReveal) -> bool:
    try:
        reveal = HoldoutReveal.model_validate(reveal.model_dump(mode="python"))
    except ValueError:
        raise StrategyResearchLedgerError("holdout_reveal_invalid") from None
    row = connection.execute(
        """SELECT s.seal_id,s.commitment_sha256,p.agent_id,p.search_family_id
        FROM strategy_research_holdout_seals s
        JOIN strategy_research_preregistrations p USING(hypothesis_id)
        WHERE s.hypothesis_id=?""",
        (reveal.hypothesis_id,),
    ).fetchone()
    if row is None:
        raise StrategyResearchLedgerError("holdout_seal_missing")
    if row[:3] != (
        reveal.seal_id,
        reveal.commitment_sha256,
        reveal.sanitized_result.owner_agent_id.value,
    ):
        raise StrategyResearchLedgerError("holdout_seal_conflict")
    if connection.execute(
        "SELECT 1 FROM strategy_research_holdout_reveals WHERE hypothesis_id=?", (reveal.hypothesis_id,)
    ).fetchone():
        raise StrategyResearchLedgerError("holdout_already_revealed")
    try:
        _ = connection.execute(
            "INSERT INTO strategy_research_holdout_reveals VALUES (?,?,?,?,?,?,?,?,?)",
            (
                reveal.content_sha256,
                reveal.reveal_id,
                reveal.hypothesis_id,
                row[3],
                reveal.seal_id,
                reveal.sanitized_result.owner_agent_id.value,
                reveal.sanitized_result.outcome.value,
                reveal.sanitized_result.model_dump_json(),
                reveal.model_dump_json(),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise StrategyResearchLedgerError("holdout_already_revealed") from error
    return True


__all__ = (
    "AgentResearchStateEvent",
    "ExactHoldoutMetric",
    "HoldoutReveal",
    "StrategyResearchLedgerError",
)
