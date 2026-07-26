from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.dashboard_models_v2_validation import (
    InvalidSnapshotMetadataError,
    validate_snapshot,
)

SourceStateName = Literal["loading", "empty", "error", "blocked", "unavailable", "corrupt", "stale", "populated"]
PublicAgentId = Literal[
    "opportunity_manager",
    "day_trading",
    "swing_trading",
    "systematic_quant",
    "derivatives_research",
    "market_context",
]


class StrictDashboardModelV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CountMetadataV2(StrictDashboardModelV2):
    total_count: int = Field(ge=0, le=100_000)
    projected_count: int = Field(ge=0, le=1_000)
    truncated: bool

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.projected_count > self.total_count:
            raise InvalidSnapshotMetadataError(reason="projected_count_exceeds_total")
        if self.truncated != (self.total_count > self.projected_count):
            raise InvalidSnapshotMetadataError(reason="truncation_flag_inconsistent")
        return self


class FreshnessV2(StrictDashboardModelV2):
    policy_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    age_seconds: int | None = Field(ge=0, le=31_536_000)
    as_of: AwareDatetime


class WorkspaceItemV2(StrictDashboardModelV2):
    item_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    kind: Literal["metric", "research", "strategy", "derivative", "paper", "system"]
    label: str = Field(min_length=1, max_length=80)
    state: SourceStateName
    value: str | None = Field(max_length=160)
    observed_at: AwareDatetime | None
    trace_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")


class SourceStateV2(CountMetadataV2):
    state: SourceStateName
    observed_at: AwareDatetime | None
    freshness: FreshnessV2
    blocker_code: str | None = Field(pattern=r"^[a-z0-9_]{3,80}$")
    summary: str = Field(min_length=1, max_length=160)
    trace_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    items: tuple[WorkspaceItemV2, ...] = Field(max_length=24)

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        requires_blocker = self.state in {"error", "blocked", "unavailable", "corrupt"}
        if requires_blocker != (self.blocker_code is not None):
            raise InvalidSnapshotMetadataError(reason="blocker_metadata_inconsistent")
        if self.projected_count != len(self.items):
            raise InvalidSnapshotMetadataError(reason="projected_count_mismatch")
        return self


class PublicAgentViewV2(StrictDashboardModelV2):
    agent_id: PublicAgentId
    label: str = Field(min_length=1, max_length=40)
    role: str = Field(min_length=1, max_length=80)
    capabilities: tuple[
        Literal["conversation"],
        Literal["directed_tool"],
        Literal["autonomous_research"],
    ]
    runtime_state: Literal["running", "armed", "idle", "failed", "unavailable"]
    trace_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")


class CommandCenterV2(SourceStateV2):
    agents: tuple[PublicAgentViewV2, ...] = Field(max_length=12)


class SourceCapabilityV2(StrictDashboardModelV2):
    capability_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    provider: Literal["fred", "alfred", "treasury", "cftc", "opendart", "kis", "ls", "alpaca"]
    label: str = Field(min_length=1, max_length=80)
    state: SourceStateName
    entitlement: Literal["realtime", "delayed", "research_only", "unavailable"]
    observed_at: AwareDatetime | None
    trace_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")


class DataSourcesV2(SourceStateV2):
    capabilities: tuple[SourceCapabilityV2, ...] = Field(max_length=30)


class WorkspacesV2(StrictDashboardModelV2):
    command_center: CommandCenterV2
    overview: SourceStateV2
    markets: SourceStateV2
    data_sources: DataSourcesV2
    research: SourceStateV2
    strategies: SourceStateV2
    derivatives: SourceStateV2
    paper: SourceStateV2
    system: SourceStateV2


class TraceNodeV2(StrictDashboardModelV2):
    node_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    kind: Literal[
        "source_receipt",
        "observation",
        "dataset",
        "code_revision",
        "hypothesis",
        "trial",
        "reviewer_decision",
        "lifecycle_decision",
        "paper_receipt",
        "process_receipt",
        "deployment_receipt",
        "blocker_terminal",
    ]
    label: str = Field(min_length=1, max_length=100)
    observed_at: AwareDatetime
    safe_ref: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["accepted", "blocked", "unavailable", "failed"]
    source_namespace: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")


class TraceEdgeV2(StrictDashboardModelV2):
    from_node_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    to_node_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    kind: Literal[
        "derived_from",
        "observed_by",
        "bound_to",
        "evaluated_in",
        "reviewed_by",
        "decided_by",
        "executed_as",
        "reconciled_by",
        "deployed_as",
        "blocked_by",
    ]


class TraceGraphV2(StrictDashboardModelV2):
    nodes: tuple[TraceNodeV2, ...] = Field(min_length=1, max_length=512)
    edges: tuple[TraceEdgeV2, ...] = Field(max_length=768)


class ProjectionMetadataV2(CountMetadataV2):
    redaction_policy_version: Literal["dashboard-redaction-v2"]
    reader_versions: tuple[str, ...] = Field(min_length=1, max_length=40)
    source_schema_version: Literal[1, 2]


class DashboardSnapshotV2(StrictDashboardModelV2):
    schema_version: Literal[2] = 2
    snapshot_id: UUID
    generated_at: AwareDatetime
    source: Literal["local-redacted-projector"] = "local-redacted-projector"
    workspaces: WorkspacesV2
    traces: TraceGraphV2
    projection: ProjectionMetadataV2

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        validate_snapshot(self)
        return self
