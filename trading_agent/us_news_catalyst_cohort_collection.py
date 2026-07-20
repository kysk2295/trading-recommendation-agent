from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.alpaca_security_master_models import AlpacaSecurityMasterSnapshot
from trading_agent.alpaca_sip_profile_materializer import (
    AlpacaSipProfileMaterializer,
    ProfileMaterializationRequest,
)
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.us_news_catalyst_cohort_runtime import (
    UsNewsCatalystCohortFeatureRequest,
    UsNewsCatalystCohortRuntimePaths,
    collect_us_news_catalyst_cohort_feature,
)
from trading_agent.us_news_catalyst_collection_artifact import (
    collection_plan_path,
    collection_receipt_path,
    load_us_news_catalyst_collection_plan,
    load_us_news_catalyst_collection_receipt,
    publish_us_news_catalyst_collection_plan,
    publish_us_news_catalyst_collection_receipt,
)
from trading_agent.us_news_catalyst_collection_models import (
    UsNewsCatalystCollectedFeatureRef,
    UsNewsCatalystCollectionPlan,
    UsNewsCatalystCollectionPlanContent,
    UsNewsCatalystCollectionProfileBinding,
    UsNewsCatalystCollectionReceipt,
    create_us_news_catalyst_collection_plan,
    create_us_news_catalyst_collection_receipt,
)
from trading_agent.us_news_catalyst_collection_profiles import (
    BoundUsNewsCatalystCollectionProfile,
    bind_new_collection_profiles,
    load_collection_profiles,
)
from trading_agent.us_news_catalyst_collection_replay import (
    validate_collection_receipt,
)
from trading_agent.us_news_catalyst_collection_scope import (
    cohort_subscriptions,
    validate_collection_plan,
    validate_collection_time,
)
from trading_agent.us_news_catalyst_feature_artifact import (
    publish_us_news_catalyst_feature_artifact,
)
from trading_agent.us_news_catalyst_feature_models import UsNewsCatalystFeatureArtifact
from trading_agent.us_news_catalyst_trial_models import UsNewsCatalystCohortArtifact
from trading_agent.us_runtime_policy_scope import completed_regular_minute
from trading_agent.us_subscription_models import DesiredMarketDataSubscription


class InvalidUsNewsCatalystCohortCollectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst cohort collection is blocked"


@dataclass(frozen=True, slots=True)
class UsNewsCatalystCohortCollectionPaths:
    plan_root: Path
    profile_root: Path
    runtime_root: Path
    canonical_root: Path
    feature_root: Path
    receipt_root: Path


@dataclass(frozen=True, slots=True)
class UsNewsCatalystCohortCollectionResult:
    plan_path: Path
    receipt_path: Path
    plan: UsNewsCatalystCollectionPlan
    receipt: UsNewsCatalystCollectionReceipt
    created: bool


class UsNewsCatalystCohortCollector:
    __slots__ = ("_page_client", "_paths")

    def __init__(
        self,
        page_client: AlpacaSipMinutePageClient,
        paths: UsNewsCatalystCohortCollectionPaths,
    ) -> None:
        if (
            type(page_client) is not AlpacaSipMinutePageClient
            or type(paths) is not UsNewsCatalystCohortCollectionPaths
        ):
            raise InvalidUsNewsCatalystCohortCollectionError
        self._page_client = page_client
        self._paths = paths

    def collect(
        self,
        cohort: UsNewsCatalystCohortArtifact,
        security_master: AlpacaSecurityMasterSnapshot,
        *,
        evaluated_at: dt.datetime,
    ) -> UsNewsCatalystCohortCollectionResult:
        try:
            subscriptions = cohort_subscriptions(cohort, security_master)
            plan_path = collection_plan_path(self._paths.plan_root, cohort.artifact_id)
            receipt_path = collection_receipt_path(
                self._paths.receipt_root,
                cohort.artifact_id,
            )
            plan = self._existing_plan(plan_path, cohort, security_master, subscriptions)
            if plan is None and receipt_path.exists():
                raise InvalidUsNewsCatalystCohortCollectionError
            if plan is not None and receipt_path.exists():
                receipt = load_us_news_catalyst_collection_receipt(receipt_path)
                validate_collection_receipt(receipt, plan, self._paths.feature_root)
                return UsNewsCatalystCohortCollectionResult(
                    plan_path,
                    receipt_path,
                    plan,
                    receipt,
                    False,
                )
            validate_collection_time(cohort, evaluated_at)
            if plan is None:
                plan, profiles = self._create_plan(
                    cohort,
                    security_master,
                    subscriptions,
                    evaluated_at,
                )
                plan_path, _created = publish_us_news_catalyst_collection_plan(
                    self._paths.plan_root,
                    plan,
                )
            else:
                profiles = load_collection_profiles(
                    plan,
                    subscriptions,
                    self._paths.profile_root,
                )
            artifacts = tuple(self._collect_feature(plan, item) for item in profiles)
            references: list[UsNewsCatalystCollectedFeatureRef] = []
            for artifact in artifacts:
                _path, _created = publish_us_news_catalyst_feature_artifact(
                    self._paths.feature_root,
                    artifact,
                )
                references.append(
                    UsNewsCatalystCollectedFeatureRef(
                        symbol=artifact.payload.symbol,
                        artifact_id=artifact.artifact_id,
                    )
                )
            receipt = create_us_news_catalyst_collection_receipt(
                plan,
                tuple(references),
            )
            receipt_path, created = publish_us_news_catalyst_collection_receipt(
                self._paths.receipt_root,
                receipt,
            )
            return UsNewsCatalystCohortCollectionResult(
                plan_path,
                receipt_path,
                plan,
                receipt,
                created,
            )
        except (AttributeError, OSError, TypeError, ValueError):
            raise InvalidUsNewsCatalystCohortCollectionError from None

    def _existing_plan(
        self,
        path: Path,
        cohort: UsNewsCatalystCohortArtifact,
        security_master: AlpacaSecurityMasterSnapshot,
        subscriptions: tuple[DesiredMarketDataSubscription, ...],
    ) -> UsNewsCatalystCollectionPlan | None:
        if not path.exists():
            return None
        plan = load_us_news_catalyst_collection_plan(path)
        validate_collection_plan(plan, cohort, security_master, subscriptions)
        return plan

    def _create_plan(
        self,
        cohort: UsNewsCatalystCohortArtifact,
        security_master: AlpacaSecurityMasterSnapshot,
        subscriptions: tuple[DesiredMarketDataSubscription, ...],
        evaluated_at: dt.datetime,
    ) -> tuple[
        UsNewsCatalystCollectionPlan,
        tuple[BoundUsNewsCatalystCollectionProfile, ...],
    ]:
        completed_minute = completed_regular_minute(evaluated_at)
        materialized = AlpacaSipProfileMaterializer(
            self._page_client,
            self._paths.profile_root,
        ).materialize_request(
            ProfileMaterializationRequest(
                subscriptions,
                cohort.payload.session_date,
                completed_minute,
            )
        )
        profiles = bind_new_collection_profiles(subscriptions, materialized)
        content = UsNewsCatalystCollectionPlanContent(
            cohort_artifact_id=cohort.artifact_id,
            trial_id=cohort.payload.trial_id,
            session_date=cohort.payload.session_date,
            cohort_observed_at=cohort.payload.observed_at,
            evaluated_at=evaluated_at,
            completed_minute=completed_minute,
            security_master_snapshot_id=security_master.snapshot_id,
            bindings=tuple(
                UsNewsCatalystCollectionProfileBinding(
                    symbol=item.subscription.symbol,
                    instrument_id=item.subscription.instrument_id,
                    profile_evidence_sha256=item.profile.evidence_sha256,
                )
                for item in profiles
            ),
        )
        return create_us_news_catalyst_collection_plan(content), profiles

    def _collect_feature(
        self,
        plan: UsNewsCatalystCollectionPlan,
        bound: BoundUsNewsCatalystCollectionProfile,
    ) -> UsNewsCatalystFeatureArtifact:
        content = plan.content
        return collect_us_news_catalyst_cohort_feature(
            self._page_client,
            UsNewsCatalystCohortRuntimePaths(
                self._paths.runtime_root,
                self._paths.canonical_root,
            ),
            UsNewsCatalystCohortFeatureRequest(
                bound.subscription,
                bound.profile,
                content.session_date,
                content.evaluated_at,
                content.completed_minute,
            ),
        )


__all__ = (
    "InvalidUsNewsCatalystCohortCollectionError",
    "UsNewsCatalystCohortCollectionPaths",
    "UsNewsCatalystCohortCollectionResult",
    "UsNewsCatalystCohortCollector",
)
