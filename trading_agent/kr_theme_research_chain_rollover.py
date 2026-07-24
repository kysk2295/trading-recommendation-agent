from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, override

from pydantic import BaseModel, ConfigDict, ValidationError

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_same_cycle_opportunity_models import (
    KrSameCycleOpportunityPolicy,
)
from trading_agent.kr_theme_lane import (
    KR_THEME_LEADER_VWAP_RECLAIM_LANE,
    KR_THEME_OPPORTUNITY_LANE,
)
from trading_agent.kr_theme_research_registration import (
    kr_theme_day_strategy_version,
    kr_theme_strategy_version,
)
from trading_agent.kr_theme_research_rollover import (
    POLICY_NAME,
    KrThemeResearchRolloverResult,
)
from trading_agent.multi_market_experiment_models import (
    MultiMarketStrategyVersionRegistration,
)
from trading_agent.private_immutable_file import (
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.research_identity_models import AgentOperatingMode

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class InvalidKrThemeResearchChainRolloverError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme research chain rollover is invalid"


class KrThemeResearchRolloverBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    opportunity_version: MultiMarketStrategyVersionRegistration
    day_version: MultiMarketStrategyVersionRegistration
    opportunity_policy: KrSameCycleOpportunityPolicy


@dataclass(frozen=True, slots=True)
class KrThemeResearchChainRolloverRequest:
    experiment_ledger: ExperimentLedgerStore
    previous_bundle_path: Path
    output_dir: Path
    code_version: str
    recorded_at: dt.datetime


def prepare_kr_theme_research_chain_rollover(
    request: KrThemeResearchChainRolloverRequest,
) -> KrThemeResearchRolloverResult:
    """Roll an exact persisted KR research bundle to one new code version."""
    try:
        if _COMMIT_SHA.fullmatch(request.code_version) is None or not _aware(request.recorded_at):
            raise InvalidKrThemeResearchChainRolloverError
        previous = _load_bundle(request.previous_bundle_path)
        _require_previous_bundle(request.experiment_ledger, previous)
        recorded_at = _effective_recorded_at(request, previous)
        opportunity = _rolled_version(
            previous.opportunity_version,
            request.code_version,
            recorded_at,
        )
        day = _rolled_version(
            previous.day_version,
            request.code_version,
            recorded_at,
        )
        _require_existing_rollover(request.experiment_ledger, opportunity, day)
        policy = KrSameCycleOpportunityPolicy.model_validate(
            previous.opportunity_policy.model_copy(
                update={
                    "producer_strategy_version": opportunity.strategy_version,
                    "runtime_code_version": request.code_version,
                }
            ).model_dump(mode="python")
        )
        bundle = KrThemeResearchRolloverBundle(
            opportunity_version=opportunity,
            day_version=day,
            opportunity_policy=policy,
        )
        payload = _canonical_json(bundle)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        root = request.output_dir.expanduser().absolute()
        policy_path = root / POLICY_NAME
        bundle_path = root / f"kr_theme_research_rollover_{digest}.json"
        _ = publish_private_immutable_text(
            policy_path,
            _canonical_json(policy),
        )
        _ = publish_private_immutable_text(bundle_path, payload)
        with request.experiment_ledger.writer() as writer:
            created = int(writer.register_multi_market_strategy_version(opportunity))
            created += int(writer.register_multi_market_strategy_version(day))
        return KrThemeResearchRolloverResult(
            versions_created=created,
            opportunity_strategy_version=opportunity.strategy_version,
            day_strategy_version=day.strategy_version,
            recorded_at=recorded_at,
            bundle_path=bundle_path,
            policy_path=policy_path,
        )
    except InvalidKrThemeResearchChainRolloverError:
        raise
    except (OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeResearchChainRolloverError from None


def _load_bundle(path: Path) -> KrThemeResearchRolloverBundle:
    payload = read_private_text(path)
    bundle = KrThemeResearchRolloverBundle.model_validate_json(payload)
    if (
        _canonical_json(bundle) != payload
        or bundle.opportunity_version.strategy_lane != KR_THEME_OPPORTUNITY_LANE
        or bundle.day_version.strategy_lane != KR_THEME_LEADER_VWAP_RECLAIM_LANE
        or bundle.opportunity_version.operating_mode is not AgentOperatingMode.SHADOW
        or bundle.day_version.operating_mode is not AgentOperatingMode.SHADOW
        or bundle.opportunity_version.code_version != bundle.day_version.code_version
        or bundle.opportunity_version.ledger_recorded_at != bundle.day_version.ledger_recorded_at
        or bundle.opportunity_policy.runtime_code_version != bundle.opportunity_version.code_version
        or bundle.opportunity_policy.producer_strategy_version != bundle.opportunity_version.strategy_version
    ):
        raise InvalidKrThemeResearchChainRolloverError
    return bundle


def _require_previous_bundle(
    ledger: ExperimentLedgerStore,
    bundle: KrThemeResearchRolloverBundle,
) -> None:
    hypotheses = tuple(item.registration for item in ledger.multi_market_hypotheses())
    versions = tuple(item.registration for item in ledger.multi_market_strategy_versions())
    expected_hypotheses = {
        bundle.opportunity_version.hypothesis_id,
        bundle.day_version.hypothesis_id,
    }
    if (
        sum(item == bundle.opportunity_version for item in versions) != 1
        or sum(item == bundle.day_version for item in versions) != 1
        or any(
            sum(item.hypothesis_id == hypothesis_id for item in hypotheses) != 1
            for hypothesis_id in expected_hypotheses
        )
    ):
        raise InvalidKrThemeResearchChainRolloverError


def _effective_recorded_at(
    request: KrThemeResearchChainRolloverRequest,
    previous: KrThemeResearchRolloverBundle,
) -> dt.datetime:
    expected_ids = {
        kr_theme_strategy_version(request.code_version),
        kr_theme_day_strategy_version(request.code_version),
    }
    existing = tuple(
        item.registration
        for item in request.experiment_ledger.multi_market_strategy_versions()
        if item.registration.strategy_version in expected_ids
    )
    if not existing:
        if request.recorded_at <= previous.opportunity_version.ledger_recorded_at:
            raise InvalidKrThemeResearchChainRolloverError
        return request.recorded_at
    if (
        len(existing) != 2
        or {item.strategy_version for item in existing} != expected_ids
        or len({item.ledger_recorded_at for item in existing}) != 1
    ):
        raise InvalidKrThemeResearchChainRolloverError
    return existing[0].ledger_recorded_at


def _rolled_version(
    previous: MultiMarketStrategyVersionRegistration,
    code_version: str,
    recorded_at: dt.datetime,
) -> MultiMarketStrategyVersionRegistration:
    strategy_version = (
        kr_theme_strategy_version(code_version)
        if previous.strategy_lane == KR_THEME_OPPORTUNITY_LANE
        else kr_theme_day_strategy_version(code_version)
    )
    return MultiMarketStrategyVersionRegistration.model_validate(
        previous.model_copy(
            update={
                "strategy_version": strategy_version,
                "code_version": code_version,
                "ledger_recorded_at": recorded_at,
            }
        ).model_dump(mode="python")
    )


def _require_existing_rollover(
    ledger: ExperimentLedgerStore,
    opportunity: MultiMarketStrategyVersionRegistration,
    day: MultiMarketStrategyVersionRegistration,
) -> None:
    expected = {
        opportunity.strategy_version: opportunity,
        day.strategy_version: day,
    }
    existing = {
        item.registration.strategy_version: item.registration
        for item in ledger.multi_market_strategy_versions()
        if item.registration.strategy_version in expected
    }
    if existing and existing != expected:
        raise InvalidKrThemeResearchChainRolloverError


def _canonical_json(value: BaseModel) -> str:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidKrThemeResearchChainRolloverError",
    "KrThemeResearchChainRolloverRequest",
    "KrThemeResearchRolloverBundle",
    "prepare_kr_theme_research_chain_rollover",
)
