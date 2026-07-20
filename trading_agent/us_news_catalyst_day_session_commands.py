from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import assert_never

from trading_agent.alpaca_news_opportunity_evidence_artifact import (
    load_alpaca_news_opportunity_evidence,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_news_catalyst_day_session_audit import UsNewsCatalystDaySessionPhase
from trading_agent.us_news_catalyst_day_session_manifest import UsNewsCatalystDaySessionManifest
from trading_agent.us_news_catalyst_day_session_supervisor import (
    UsNewsCatalystDaySessionAction,
    UsNewsCatalystDaySessionActionStatus,
)
from trading_agent.us_news_catalyst_opportunity_artifact import (
    load_us_news_catalyst_opportunity_projection,
)
from trading_agent.us_news_catalyst_opportunity_models import (
    UsNewsCatalystOpportunityProjection,
    UsNewsCatalystProjectionStatus,
)
from trading_agent.us_news_catalyst_trial_artifact import cohorts_in, setup_manifests_in
from trading_agent.us_news_catalyst_trial_models import UsNewsCatalystCohortArtifact

_ROOT = Path(__file__).resolve().parent.parent
_HORIZON = dt.timedelta(minutes=30)
_MAX_DELAY = dt.timedelta(minutes=2)


def us_news_catalyst_day_session_action(
    manifest: UsNewsCatalystDaySessionManifest,
    phase: UsNewsCatalystDaySessionPhase,
    observed_at: dt.datetime,
) -> UsNewsCatalystDaySessionAction:
    try:
        bounds = regular_session_bounds(manifest.session_date)
        if bounds is None or observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return _blocked("session_time_invalid")
        output = manifest.paths.output_root / phase.value / _path_time(observed_at)
        match phase:
            case UsNewsCatalystDaySessionPhase.REGISTER:
                if observed_at >= bounds[0]:
                    return _blocked("registration_window_missed")
                return _execute(
                    str(_ROOT / "run_us_news_catalyst_shadow_trial.py"),
                    "register",
                    "--registration-manifest",
                    str(manifest.paths.registration_manifest),
                    "--session-date",
                    manifest.session_date.isoformat(),
                    *_ledger_output(manifest, output),
                )
            case UsNewsCatalystDaySessionPhase.START:
                return _start_action(manifest, observed_at, bounds, output)
            case UsNewsCatalystDaySessionPhase.COLLECT:
                return _collect_action(manifest, observed_at, output)
            case UsNewsCatalystDaySessionPhase.OBSERVE:
                return _observe_action(manifest, observed_at, output)
            case UsNewsCatalystDaySessionPhase.FINALIZE:
                return _finalize_action(manifest, observed_at, output)
            case UsNewsCatalystDaySessionPhase.REVIEW:
                if observed_at < bounds[1]:
                    return _waiting()
                return _execute(
                    str(_ROOT / "run_us_news_catalyst_shadow_trial.py"),
                    "review",
                    "--strategy-version",
                    manifest.strategy_version,
                    "--as-of-session",
                    manifest.session_date.isoformat(),
                    "--artifact-root",
                    str(manifest.paths.artifact_root),
                    "--review-root",
                    str(manifest.paths.review_root),
                    *_ledger_output(manifest, output),
                )
            case unreachable:
                assert_never(unreachable)
    except (AttributeError, OSError, TypeError, ValueError):
        return _blocked("phase_input_invalid")


def _start_action(
    manifest: UsNewsCatalystDaySessionManifest,
    observed_at: dt.datetime,
    bounds: tuple[dt.datetime, dt.datetime],
    output: Path,
) -> UsNewsCatalystDaySessionAction:
    if observed_at < bounds[0]:
        return _waiting()
    if observed_at >= bounds[1]:
        return _blocked("start_window_missed")
    selected = _active_projection(manifest, observed_at)
    if selected is None:
        return _waiting()
    projection_path, projection = selected
    evidence_path = manifest.paths.evidence_root / (
        f"alpaca_news_opportunity_evidence_{projection.evidence_bundle_id}.json"
    )
    evidence = load_alpaca_news_opportunity_evidence(evidence_path)
    if evidence.bundle_id != projection.evidence_bundle_id:
        return _blocked("start_evidence_mismatch")
    return _execute(
        str(_ROOT / "run_us_news_catalyst_shadow_trial.py"),
        "start",
        "--trial-id",
        manifest.trial_id,
        "--projection",
        str(projection_path),
        "--evidence",
        str(evidence_path),
        "--artifact-root",
        str(manifest.paths.artifact_root),
        *_ledger_output(manifest, output),
    )


def _collect_action(
    manifest: UsNewsCatalystDaySessionManifest,
    observed_at: dt.datetime,
    output: Path,
) -> UsNewsCatalystDaySessionAction:
    cohort_path, cohort = _cohort(manifest)
    window = _window_action(cohort.payload.observed_at, observed_at)
    if window is not None:
        return window
    return _execute(
        str(_ROOT / "run_us_news_catalyst_cohort_collect.py"),
        "--cohort",
        str(cohort_path),
        "--security-master-store",
        str(manifest.paths.security_master_store),
        "--plan-root",
        str(manifest.paths.plan_root),
        "--profile-root",
        str(manifest.paths.profile_root),
        "--runtime-root",
        str(manifest.paths.runtime_root),
        "--canonical-root",
        str(manifest.paths.canonical_root),
        "--feature-root",
        str(manifest.paths.feature_root),
        "--receipt-root",
        str(manifest.paths.receipt_root),
        "--secret-path",
        str(manifest.paths.secret_path),
        "--output-dir",
        str(output),
    )


def _observe_action(
    manifest: UsNewsCatalystDaySessionManifest,
    observed_at: dt.datetime,
    output: Path,
) -> UsNewsCatalystDaySessionAction:
    cohort_path, cohort = _cohort(manifest)
    window = _window_action(cohort.payload.observed_at, observed_at)
    if window is not None:
        return window
    return _execute(
        str(_ROOT / "run_us_news_catalyst_setup_observation.py"),
        "--cohort",
        str(cohort_path),
        "--feature-root",
        str(manifest.paths.feature_root),
        "--artifact-root",
        str(manifest.paths.artifact_root),
        "--output-dir",
        str(output),
    )


def _finalize_action(
    manifest: UsNewsCatalystDaySessionManifest,
    observed_at: dt.datetime,
    output: Path,
) -> UsNewsCatalystDaySessionAction:
    cohort_path, cohort = _cohort(manifest)
    if observed_at < cohort.payload.observed_at + _HORIZON:
        return _waiting()
    manifests = tuple(
        item
        for item in setup_manifests_in(manifest.paths.artifact_root)
        if item.trial_id == manifest.trial_id
    )
    if len(manifests) > 1:
        return _blocked("observation_manifest_ambiguous")
    observation = () if not manifests else (
        "--observation-manifest",
        str(
            manifest.paths.artifact_root
            / f"us_news_catalyst_setup_{manifests[0].manifest_id}.json"
        ),
    )
    return _execute(
        str(_ROOT / "run_us_news_catalyst_shadow_trial.py"),
        "finalize",
        "--trial-id",
        manifest.trial_id,
        "--cohort",
        str(cohort_path),
        *observation,
        "--artifact-root",
        str(manifest.paths.artifact_root),
        *_ledger_output(manifest, output),
    )


def _active_projection(
    manifest: UsNewsCatalystDaySessionManifest,
    observed_at: dt.datetime,
) -> tuple[Path, UsNewsCatalystOpportunityProjection] | None:
    candidates: list[tuple[Path, UsNewsCatalystOpportunityProjection]] = []
    for path in sorted(manifest.paths.projection_root.glob("us_news_catalyst_projection_*.json")):
        item = load_us_news_catalyst_opportunity_projection(path)
        snapshot = item.snapshot
        if (
            item.status is UsNewsCatalystProjectionStatus.RANKED
            and snapshot is not None
            and item.strategy_version == manifest.strategy_version
            and item.projected_at.astimezone(NEW_YORK).date() == manifest.session_date
            and item.projected_at <= observed_at < snapshot.valid_until
        ):
            candidates.append((path, item))
    return None if not candidates else max(
        candidates,
        key=lambda value: (value[1].projected_at, value[1].projection_id),
    )


def _cohort(
    manifest: UsNewsCatalystDaySessionManifest,
) -> tuple[Path, UsNewsCatalystCohortArtifact]:
    matches = tuple(
        item
        for item in cohorts_in(manifest.paths.artifact_root)
        if item.payload.trial_id == manifest.trial_id
    )
    if len(matches) != 1:
        raise ValueError
    item = matches[0]
    path = manifest.paths.artifact_root / f"us_news_catalyst_cohort_{item.artifact_id}.json"
    return path, item


def _window_action(started_at: dt.datetime, observed_at: dt.datetime) -> UsNewsCatalystDaySessionAction | None:
    target = started_at + _HORIZON
    if observed_at <= target:
        return _waiting()
    if observed_at > target + _MAX_DELAY:
        return _blocked("phase_window_missed")
    return None


def _ledger_output(manifest: UsNewsCatalystDaySessionManifest, output: Path) -> tuple[str, ...]:
    return (
        "--experiment-ledger", str(manifest.paths.experiment_ledger),
        "--output-dir", str(output),
    )


def _execute(*command: str) -> UsNewsCatalystDaySessionAction:
    return UsNewsCatalystDaySessionAction(UsNewsCatalystDaySessionActionStatus.EXECUTE, command, None)


def _waiting() -> UsNewsCatalystDaySessionAction:
    return UsNewsCatalystDaySessionAction(UsNewsCatalystDaySessionActionStatus.WAITING, None, None)


def _blocked(reason: str) -> UsNewsCatalystDaySessionAction:
    return UsNewsCatalystDaySessionAction(UsNewsCatalystDaySessionActionStatus.BLOCKED, None, reason)


def _path_time(value: dt.datetime) -> str:
    return value.isoformat().replace(":", "").replace("+", "p")
