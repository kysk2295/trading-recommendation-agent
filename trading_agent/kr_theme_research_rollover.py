from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_same_cycle_opportunity_models import (
    KrSameCycleOpportunityPolicy,
    load_kr_same_cycle_opportunity_policy,
)
from trading_agent.kr_theme_lane import (
    KR_THEME_LEADER_VWAP_RECLAIM_LANE,
    KR_THEME_OPPORTUNITY_LANE,
)
from trading_agent.kr_theme_research_registration import (
    kr_theme_day_strategy_version,
    kr_theme_research_registrations,
    kr_theme_strategy_version,
    load_kr_theme_research_manifest,
)
from trading_agent.multi_market_experiment_models import (
    MultiMarketStrategyVersionRegistration,
)
from trading_agent.private_immutable_file import publish_private_immutable_text

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
POLICY_NAME = "opportunity-policy.json"


class InvalidKrThemeResearchRolloverError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme research code-version rollover is invalid"


@dataclass(frozen=True, slots=True)
class KrThemeResearchRolloverResult:
    versions_created: int
    opportunity_strategy_version: str
    day_strategy_version: str
    recorded_at: dt.datetime
    bundle_path: Path
    policy_path: Path


def prepare_kr_theme_research_rollover(
    *,
    experiment_ledger: ExperimentLedgerStore,
    opportunity_manifest_path: Path,
    day_manifest_path: Path,
    policy_path: Path,
    output_dir: Path,
    code_version: str,
    recorded_at: dt.datetime,
) -> KrThemeResearchRolloverResult:
    try:
        if _COMMIT_SHA.fullmatch(code_version) is None or not _aware(recorded_at):
            raise InvalidKrThemeResearchRolloverError
        opportunity_manifest = load_kr_theme_research_manifest(
            opportunity_manifest_path
        )
        day_manifest = load_kr_theme_research_manifest(day_manifest_path)
        policy_template = load_kr_same_cycle_opportunity_policy(policy_path)
        opportunity_parent, opportunity_template = kr_theme_research_registrations(
            opportunity_manifest
        )
        day_parent, day_template = kr_theme_research_registrations(day_manifest)
        if (
            opportunity_template.strategy_lane != KR_THEME_OPPORTUNITY_LANE
            or day_template.strategy_lane != KR_THEME_LEADER_VWAP_RECLAIM_LANE
            or policy_template.runtime_code_version != opportunity_template.code_version
            or policy_template.producer_strategy_version
            != opportunity_template.strategy_version
        ):
            raise InvalidKrThemeResearchRolloverError
        _require_exact_templates(
            experiment_ledger,
            opportunity_parent,
            day_parent,
            opportunity_template,
            day_template,
        )
        effective_at = _effective_recorded_at(
            experiment_ledger,
            code_version,
            recorded_at,
        )
        opportunity_version = _rollover_version(
            opportunity_template,
            code_version,
            effective_at,
        )
        day_version = _rollover_version(day_template, code_version, effective_at)
        _require_existing_rollover(
            experiment_ledger,
            opportunity_version,
            day_version,
        )
        policy = KrSameCycleOpportunityPolicy.model_validate(
            policy_template.model_copy(
                update={
                    "producer_strategy_version": opportunity_version.strategy_version,
                    "runtime_code_version": code_version,
                }
            ).model_dump(mode="python")
        )
        payload = _bundle_payload(opportunity_version, day_version, policy)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        root = output_dir.expanduser().absolute()
        policy_target = root / POLICY_NAME
        bundle_target = root / f"kr_theme_research_rollover_{digest}.json"
        _ = publish_private_immutable_text(
            policy_target,
            _canonical_json(policy.model_dump(mode="json")),
        )
        _ = publish_private_immutable_text(bundle_target, payload)
        with experiment_ledger.writer() as writer:
            created = int(
                writer.register_multi_market_strategy_version(opportunity_version)
            )
            created += int(writer.register_multi_market_strategy_version(day_version))
        return KrThemeResearchRolloverResult(
            versions_created=created,
            opportunity_strategy_version=opportunity_version.strategy_version,
            day_strategy_version=day_version.strategy_version,
            recorded_at=effective_at,
            bundle_path=bundle_target,
            policy_path=policy_target,
        )
    except InvalidKrThemeResearchRolloverError:
        raise
    except (OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeResearchRolloverError from None


def _require_exact_templates(
    ledger: ExperimentLedgerStore,
    opportunity_parent: object,
    day_parent: object,
    opportunity_template: MultiMarketStrategyVersionRegistration,
    day_template: MultiMarketStrategyVersionRegistration,
) -> None:
    hypotheses = tuple(item.registration for item in ledger.multi_market_hypotheses())
    versions = tuple(
        item.registration for item in ledger.multi_market_strategy_versions()
    )
    if (
        sum(item == opportunity_parent for item in hypotheses) != 1
        or sum(item == day_parent for item in hypotheses) != 1
        or sum(item == opportunity_template for item in versions) != 1
        or sum(item == day_template for item in versions) != 1
    ):
        raise InvalidKrThemeResearchRolloverError


def _effective_recorded_at(
    ledger: ExperimentLedgerStore,
    code_version: str,
    requested_at: dt.datetime,
) -> dt.datetime:
    expected_ids = {
        kr_theme_strategy_version(code_version),
        kr_theme_day_strategy_version(code_version),
    }
    existing = tuple(
        item.registration
        for item in ledger.multi_market_strategy_versions()
        if item.registration.strategy_version in expected_ids
    )
    if not existing:
        return requested_at
    if len(existing) != 2 or {item.strategy_version for item in existing} != expected_ids:
        raise InvalidKrThemeResearchRolloverError
    recorded = {item.ledger_recorded_at for item in existing}
    if len(recorded) != 1:
        raise InvalidKrThemeResearchRolloverError
    return next(iter(recorded))


def _rollover_version(
    template: MultiMarketStrategyVersionRegistration,
    code_version: str,
    recorded_at: dt.datetime,
) -> MultiMarketStrategyVersionRegistration:
    version = (
        kr_theme_strategy_version(code_version)
        if template.strategy_lane == KR_THEME_OPPORTUNITY_LANE
        else kr_theme_day_strategy_version(code_version)
    )
    return MultiMarketStrategyVersionRegistration.model_validate(
        template.model_copy(
            update={
                "strategy_version": version,
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
        raise InvalidKrThemeResearchRolloverError


def _bundle_payload(
    opportunity: MultiMarketStrategyVersionRegistration,
    day: MultiMarketStrategyVersionRegistration,
    policy: KrSameCycleOpportunityPolicy,
) -> str:
    return _canonical_json(
        {
            "schema_version": 1,
            "opportunity_version": opportunity.model_dump(mode="json"),
            "day_version": day.model_dump(mode="json"),
            "opportunity_policy": policy.model_dump(mode="json"),
        }
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "POLICY_NAME",
    "InvalidKrThemeResearchRolloverError",
    "KrThemeResearchRolloverResult",
    "prepare_kr_theme_research_rollover",
)
