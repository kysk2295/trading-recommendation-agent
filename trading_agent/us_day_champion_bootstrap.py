from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Literal, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from trading_agent.day_agent_version_models import (
    AgentDeploymentState,
    AgentModelRoleBinding,
    AgentVersion,
    DayAgentVersionStoreError,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.us_day_champion_bootstrap_lease import (
    UsDayChampionBootstrapLeaseError,
    us_day_champion_bootstrap_lease,
)
from trading_agent.us_day_strategy_review_models import ReviewedUsDayStrategyManifest
from trading_agent.us_equity_calendar import NEW_YORK, PUBLISHED_CALENDAR_YEARS, regular_session_bounds


class UsDayChampionBootstrapError(ValueError):
    reason: str

    def __init__(self, reason: str = "champion_bootstrap_invalid") -> None:
        self.reason = reason
        super().__init__(reason)

    @override
    def __str__(self) -> str:
        return self.reason


class UsDayChampionBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_manifest: Path
    experiment_ledger: Path
    version_store: Path
    reasoning_model_id: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    prompt_policy: Path
    tool_policy: Path
    memory_policy: Path
    review_evidence: Path
    receipt_root: Path
    created_at: AwareDatetime
    created_session_date: dt.date


class UsDayChampionBootstrapReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: AgentVersion
    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    order_authority: Literal[False] = False
    paper_trading_enabled: Literal[False] = False


class UsDayChampionBootstrapPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: AgentVersion
    receipt: UsDayChampionBootstrapReceipt


class UsDayChampionBootstrapResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt: UsDayChampionBootstrapReceipt
    version_created: bool
    receipt_created: bool


def plan_us_day_champion_bootstrap(
    request: UsDayChampionBootstrapRequest,
) -> UsDayChampionBootstrapPlan:
    try:
        manifest_text = read_private_text(request.strategy_manifest)
        manifest = ReviewedUsDayStrategyManifest.model_validate_json(manifest_text)
        prompt_sha256 = _private_sha256(request.prompt_policy)
        tool_sha256 = _private_sha256(request.tool_policy)
        memory_sha256 = _private_sha256(request.memory_policy)
        review_sha256 = _private_sha256(request.review_evidence)
        manifest_sha256 = hashlib.sha256(manifest_text.encode()).hexdigest()
        stored = ExperimentLedgerStore(request.experiment_ledger).reader().day_strategy_capsule(
            manifest.capsule_id
        )
        if (
            stored is None
            or stored.capsule.capsule_id != manifest.capsule_id
            or stored.capsule.market_id is not MarketId.US_EQUITIES
            or request.created_session_date != _latest_completed_session(request.created_at)
        ):
            raise UsDayChampionBootstrapError
        version = build_agent_version(
            model_role_bindings=(
                AgentModelRoleBinding(role="reasoning", model_id=request.reasoning_model_id),
            ),
            prompt_sha256=prompt_sha256,
            tool_policy_sha256=tool_sha256,
            memory_retrieval_policy_sha256=memory_sha256,
            playbook_ids=(manifest.capsule_id,),
            parent_version_id=None,
            creation_evidence_ids=tuple(sorted((manifest.capsule_id, manifest_sha256, review_sha256))),
            deployment_state=AgentDeploymentState.CHAMPION,
            task_id=f"bootstrap-{request.created_session_date.isoformat()}-{manifest.capsule_id[:16]}",
            created_at=request.created_at.astimezone(dt.UTC),
            created_session_date=request.created_session_date,
        )
        existing = DayAgentVersionStore(request.version_store).reader().champion()
        if existing is not None and existing != version:
            raise UsDayChampionBootstrapError
        receipt = UsDayChampionBootstrapReceipt(
            version=version,
            capsule_id=manifest.capsule_id,
            strategy_manifest_sha256=manifest_sha256,
            review_evidence_sha256=review_sha256,
        )
        return UsDayChampionBootstrapPlan(version=version, receipt=receipt)
    except UsDayChampionBootstrapError:
        raise
    except (
        DayAgentVersionStoreError,
        InvalidPrivateImmutableFileError,
        OSError,
        RuntimeError,
        ValidationError,
        ValueError,
    ):
        raise UsDayChampionBootstrapError from None


def bootstrap_us_day_champion(
    request: UsDayChampionBootstrapRequest,
) -> UsDayChampionBootstrapResult:
    plan = plan_us_day_champion_bootstrap(request)
    try:
        receipt_path = request.receipt_root / f"champion_bootstrap_{plan.version.version_id}.json"
        with us_day_champion_bootstrap_lease(request.receipt_root) as lease:
            if lease.receipt_names() not in ((), (receipt_path.name,)):
                raise UsDayChampionBootstrapError
            receipt_created = publish_private_immutable_text(
                receipt_path,
                canonical_experiment_ledger_json(plan.receipt) + "\n",
            )
            lease.require_bound()
            with DayAgentVersionStore(request.version_store).writer() as writer:
                version_created = writer.register_initial_champion(plan.version)
        return UsDayChampionBootstrapResult(
            receipt=plan.receipt,
            version_created=version_created,
            receipt_created=receipt_created,
        )
    except (
        DayAgentVersionStoreError,
        InvalidPrivateImmutableFileError,
        UsDayChampionBootstrapLeaseError,
        OSError,
        ValueError,
    ):
        raise UsDayChampionBootstrapError from None


def _private_sha256(path: Path) -> str:
    return hashlib.sha256(read_private_text(path).encode()).hexdigest()


def _latest_completed_session(created_at: dt.datetime) -> dt.date | None:
    local_date = created_at.astimezone(NEW_YORK).date()
    if local_date.year not in PUBLISHED_CALENDAR_YEARS:
        return None
    candidate = local_date
    first_published = dt.date(min(PUBLISHED_CALENDAR_YEARS), 1, 1)
    while candidate >= first_published:
        bounds = regular_session_bounds(candidate)
        if bounds is not None and created_at.astimezone(dt.UTC) >= bounds[1].astimezone(dt.UTC):
            return candidate
        candidate -= dt.timedelta(days=1)
    return None


__all__ = (
    "UsDayChampionBootstrapError",
    "UsDayChampionBootstrapPlan",
    "UsDayChampionBootstrapReceipt",
    "UsDayChampionBootstrapRequest",
    "UsDayChampionBootstrapResult",
    "bootstrap_us_day_champion",
    "plan_us_day_champion_bootstrap",
)
