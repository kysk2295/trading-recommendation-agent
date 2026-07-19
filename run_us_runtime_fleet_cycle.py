#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx2

from trading_agent.alpaca_http import (
    ALPACA_DATA_URL,
)
from trading_agent.alpaca_sip_dynamic_plan_store import AlpacaSipDynamicPlanStoreError
from trading_agent.alpaca_sip_profile_materializer import (
    AlpacaSipProfileMaterializer,
    AlpacaSipProfileMaterializerError,
)
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.alpaca_sip_runtime_owner import (
    AlpacaSipRuntimeOwnerFactory,
    AlpacaSipRuntimeOwnerFactoryConfig,
)
from trading_agent.research_evidence_artifact import (
    ResearchEvidenceArtifactError,
    write_research_evidence_artifact,
)
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_feature_evidence_models import UsFeatureEvidenceBinding
from trading_agent.us_market_data_fleet import UsMarketDataFleet
from trading_agent.us_market_data_fleet_audit_store import RuntimeFleetAuditStore
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerProjectionError
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore
from trading_agent.us_runtime_actionability_manifest_dispatch import (
    UsRuntimeActionabilityManifestDispatchError,
    dispatch_us_runtime_actionability_outbox_counts,
)
from trading_agent.us_runtime_actionability_plan import (
    RuntimeActionabilityPlanConfig,
)
from trading_agent.us_runtime_fleet_cycle import (
    ProfileArtifactBinding,
    RuntimeFleetCycleError,
    bind_runtime_profiles,
    execute_runtime_fleet_cycle,
)
from trading_agent.us_runtime_fleet_cycle_args import parse_runtime_fleet_cycle_args as parse_args
from trading_agent.us_runtime_fleet_cycle_cli_result import (
    LIVE_BLOCKED,
    LIVE_DISABLED,
    LIVE_NOT_ATTEMPTED,
    RuntimeFleetCycleCliResult,
    completed_live_outcome,
)
from trading_agent.us_runtime_fleet_cycle_report import (
    RuntimeFleetCycleReportFields,
    write_runtime_fleet_cycle_ready_report,
    write_runtime_fleet_cycle_report,
)
from trading_agent.us_runtime_live_actionability_config import (
    RuntimeLiveActionabilityConfig,
    RuntimeLiveActionabilityConfigError,
)
from trading_agent.us_runtime_policy_scope import (
    RuntimePolicyScopeError,
    RuntimePolicyScopeRequest,
    prepare_runtime_policy_scope,
)
from trading_agent.us_sip_research_evidence_projection import (
    UsSipResearchEvidenceProjectionError,
    project_us_sip_research_evidence,
)
from trading_agent.us_subscription_models import (
    SubscriptionPolicyConfig,
)
from trading_agent.us_subscription_policy_state import (
    SubscriptionPolicyStateError,
    advance_subscription_policy_state,
)
from trading_agent.us_subscription_policy_state_store import SubscriptionPolicyStateStore

REPORT_NAME = "us_runtime_fleet_cycle_ko.md"


def create_data_client() -> httpx2.Client:
    return httpx2.Client(
        base_url=ALPACA_DATA_URL,
        follow_redirects=False,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    now: dt.datetime | None = None,
    client_factory: Callable[[], httpx2.Client] = create_data_client,
) -> int:
    return run_cycle(argv, now=now, client_factory=client_factory).exit_code


def run_cycle(
    argv: Sequence[str] | None = None,
    *,
    now: dt.datetime | None = None,
    client_factory: Callable[[], httpx2.Client] = create_data_client,
) -> RuntimeFleetCycleCliResult:
    args = parse_args(argv)
    evaluated_at = dt.datetime.now(dt.UTC) if now is None else now
    live_outcome = LIVE_NOT_ATTEMPTED
    live_invoked = False
    try:
        actionability = RuntimeActionabilityPlanConfig(
            args.conditional_signal_outbox,
            args.actionability_manifest_root,
            args.dynamic_plan_store,
            args.policy_state_store,
        )
        live_actionability = RuntimeLiveActionabilityConfig(
            args.live_actionability_receipt_root,
            args.live_actionability_store,
            args.arm_live_actionability,
            actionability,
        )
        if not live_actionability.armed:
            live_outcome = LIVE_DISABLED
        _validate_research_options(args)
        state_store = SubscriptionPolicyStateStore(args.policy_state_store)
        prior_state = state_store.latest()
        manual_profiles = None if args.profile is None else _profile_bindings(args.profile)
        scope = prepare_runtime_policy_scope(
            UsOpportunityScannerStore(args.scanner_store),
            RuntimePolicyScopeRequest(
                evaluated_at,
                () if prior_state is None else prior_state.active,
                () if prior_state is None else prior_state.cooldowns,
                _policy_config(args),
            ),
        )
        next_state = advance_subscription_policy_state(prior_state, scope.decision)
        state_appended = state_store.append(next_state)
        plan_roll = actionability.roll(next_state)
        prepared = None if manual_profiles is None else bind_runtime_profiles(scope, manual_profiles)
        credentials = live_actionability.load_credentials(args.secret_path)
        with client_factory() as client:
            page_client = AlpacaSipMinutePageClient(
                client,
                credentials,
                clock=lambda: evaluated_at,
            )
            if prepared is None:
                if args.auto_profile_root is None:
                    raise RuntimeFleetCycleError
                profiles = AlpacaSipProfileMaterializer(
                    page_client,
                    args.auto_profile_root,
                ).materialize(scope)
                prepared = bind_runtime_profiles(scope, profiles)
            factory = AlpacaSipRuntimeOwnerFactory(
                page_client,
                AlpacaSipRuntimeOwnerFactoryConfig(
                    args.runtime_root,
                    args.canonical_root,
                    evaluated_at.astimezone(NEW_YORK).date(),
                    lambda: evaluated_at,
                ),
            )
            result = execute_runtime_fleet_cycle(
                prepared,
                UsMarketDataFleet(factory),
                RuntimeFleetAuditStore(args.audit_store),
            )
        research_counts = _write_research_artifacts(args, result.fleet.bindings)
        actionability_counts = dispatch_us_runtime_actionability_outbox_counts(
            actionability.signal_outbox,
            actionability.manifest_root,
            result.fleet.bindings,
            None if plan_roll is None else plan_roll.plan,
        )
        live_invoked = live_actionability.armed
        live_actionability_result = live_actionability.dispatch(evaluated_at, credentials)
        if live_actionability_result is not None:
            live_outcome = completed_live_outcome(live_actionability_result)
    except (
        AlpacaSipDynamicPlanStoreError,
        AlpacaSipProfileMaterializerError,
        OSError,
        ResearchEvidenceArtifactError,
        RuntimeFleetCycleError,
        RuntimeLiveActionabilityConfigError,
        RuntimePolicyScopeError,
        SubscriptionPolicyStateError,
        TypeError,
        UsSipResearchEvidenceProjectionError,
        UsOpportunityScannerProjectionError,
        UsRuntimeActionabilityManifestDispatchError,
        ValueError,
    ):
        if live_invoked:
            live_outcome = LIVE_BLOCKED
        _report(args.output_dir, ("result: blocked", "account/order mutation: 0"))
        return RuntimeFleetCycleCliResult(1, live_outcome)
    ready = result.audit.fleet_status == "ready" and result.audit.gate_status == "ready"
    write_runtime_fleet_cycle_ready_report(
        args.output_dir,
        REPORT_NAME,
        RuntimeFleetCycleReportFields(
            ready,
            result.audit.fleet_status,
            result.audit.gate_status,
            len(result.audit.owners),
            result.audit_appended,
            state_appended,
            plan_roll,
            research_counts,
            actionability_counts,
            live_actionability_result,
        ),
    )
    return RuntimeFleetCycleCliResult(0 if ready else 1, live_outcome)


def _profile_bindings(values: list[str]) -> tuple[ProfileArtifactBinding, ...]:
    bindings: list[ProfileArtifactBinding] = []
    for value in values:
        instrument_id, separator, raw_path = value.partition("=")
        if not separator or not instrument_id or not raw_path:
            raise RuntimeFleetCycleError
        bindings.append(ProfileArtifactBinding(instrument_id, Path(raw_path).expanduser().absolute()))
    return tuple(bindings)


def _policy_config(args: argparse.Namespace) -> SubscriptionPolicyConfig:
    return SubscriptionPolicyConfig(
        args.capacity,
        dt.timedelta(seconds=args.max_candidate_age_seconds),
        dt.timedelta(seconds=args.minimum_residency_seconds),
        dt.timedelta(seconds=args.eviction_cooldown_seconds),
    )


def _validate_research_options(args: argparse.Namespace) -> None:
    if type(args.minimum_rvol_bps) is not int or not 1 <= args.minimum_rvol_bps <= 100_000:
        raise RuntimeFleetCycleError


def _write_research_artifacts(
    args: argparse.Namespace,
    bindings: tuple[UsFeatureEvidenceBinding, ...],
) -> tuple[int, int] | None:
    if args.research_artifact_root is None:
        return None
    models = project_us_sip_research_evidence(
        bindings,
        args.canonical_root,
        minimum_rvol_bps=args.minimum_rvol_bps,
    )
    created = tuple(write_research_evidence_artifact(args.research_artifact_root, model)[1] for model in models)
    return sum(created), len(created) - sum(created)


def _report(output_dir: Path, details: tuple[str, ...]) -> None:
    write_runtime_fleet_cycle_report(output_dir, REPORT_NAME, details)


if __name__ == "__main__":
    raise SystemExit(main())
