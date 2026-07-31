from __future__ import annotations

import datetime as dt
import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.future_session_plan_models import (
    DeferredTrialRegistrationState,
    FutureSessionArtifactLayout,
    FutureSessionPlanDecision,
    FutureSessionPlanRequest,
    JobTimingSpec,
    ReadyToPrepareSessionPlan,
    SessionCalendarProvenance,
    StrategyRegistrationIdentity,
    WaitingAuthorityReason,
    WaitingSessionAuthority,
    canonical_request_json,
    plan_content_sha256,
)
from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    KisKrSessionCalendarStore,
)
from trading_agent.kr_theme_research_chain_rollover import (
    InvalidKrThemeResearchChainRolloverError,
    KrThemeResearchRolloverBundle,
    kr_theme_research_rollover_bundle_sha256,
    load_kr_theme_research_rollover_bundle,
)

_KST = ZoneInfo("Asia/Seoul")


def compile_kr_future_session_plan(
    request: FutureSessionPlanRequest,
) -> FutureSessionPlanDecision:
    target, calendar = _calendar_authority(request)
    if target is None or calendar is None:
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.CALENDAR_AUTHORITY_MISSING,
        )
    if not _runtime_matches(request):
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.FROZEN_RUNTIME_INVALID,
        )
    ledger_compatible = _frozen_runtime_can_read_experiment_ledger(request)
    if ledger_compatible is not True:
        reason = (
            WaitingAuthorityReason.FROZEN_RUNTIME_STORE_SCHEMA_INCOMPATIBLE
            if ledger_compatible is False
            else WaitingAuthorityReason.RUNTIME_ENVIRONMENT_INVALID
        )
        return _waiting(request, target, reason)
    bundle = _bundle(request)
    if bundle is None:
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.ROLLOVER_BUNDLE_INVALID,
        )
    if (
        bundle.opportunity_version.code_version
        != request.frozen_runtime.commit_sha
        or bundle.day_version.code_version
        != request.frozen_runtime.commit_sha
    ):
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.ROLLOVER_BUNDLE_MISMATCH,
        )
    registrations = _registrations(request, bundle)
    if registrations is None:
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.ROLLOVER_BUNDLE_MISMATCH,
        )
    if not _target_shadow_lifecycle(request, bundle, target):
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.RUNTIME_AUTHORITY_MISSING,
        )
    try:
        trials = ExperimentLedgerReader(
            request.experiment_ledger
        ).multi_market_trials()
    except (InvalidExperimentLedgerSourceError, OSError, ValueError):
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.RUNTIME_AUTHORITY_MISSING,
        )
    if any(item.registration.planned_start == target for item in trials):
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.TRIAL_AUTHORITY_CONFLICT,
        )
    return _ready(request, target, calendar, registrations, bundle)


def _bundle(
    request: FutureSessionPlanRequest,
) -> KrThemeResearchRolloverBundle | None:
    try:
        if request.kr_rollover_bundle is None:
            raise InvalidKrThemeResearchChainRolloverError
        return load_kr_theme_research_rollover_bundle(
            request.kr_rollover_bundle
        )
    except (
        InvalidKrThemeResearchChainRolloverError,
        OSError,
        ValidationError,
        ValueError,
    ):
        return None


def _registrations(
    request: FutureSessionPlanRequest,
    bundle: KrThemeResearchRolloverBundle,
) -> tuple[StrategyRegistrationIdentity, ...] | None:
    try:
        stored = tuple(
            item.registration
            for item in ExperimentLedgerReader(
                request.experiment_ledger
            ).multi_market_strategy_versions()
        )
    except (InvalidExperimentLedgerSourceError, OSError, ValueError):
        return None
    expected = (bundle.opportunity_version, bundle.day_version)
    if any(
        sum(item == registration for item in stored) != 1
        for registration in expected
    ):
        return None
    return tuple(
        sorted(
            (
                StrategyRegistrationIdentity(
                    strategy_version=item.strategy_version,
                    code_version=item.code_version,
                    lane_id=item.strategy_lane.canonical_id,
                    operating_mode=item.operating_mode.value,
                    registration_sha256=hashlib.sha256(
                        canonical_experiment_ledger_json(item).encode()
                    ).hexdigest(),
                )
                for item in expected
            ),
            key=lambda item: item.strategy_version,
        )
    )


def _target_shadow_lifecycle(
    request: FutureSessionPlanRequest,
    bundle: KrThemeResearchRolloverBundle,
    target: dt.date,
) -> bool:
    try:
        state = ExperimentLedgerReader(
            request.experiment_ledger
        ).multi_market_lifecycle_state(
            bundle.day_version.strategy_version,
            target,
        )
    except (InvalidExperimentLedgerSourceError, OSError, ValueError):
        return False
    return (
        state is not None
        and state.event.to_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
    )


def _calendar_authority(
    request: FutureSessionPlanRequest,
) -> tuple[dt.date | None, SessionCalendarProvenance | None]:
    try:
        if request.kr_calendar_store is None:
            return None, None
        snapshots = KisKrSessionCalendarStore(
            request.kr_calendar_store
        ).snapshots()
        candidates = tuple(
            (day.session_date, snapshot)
            for snapshot in snapshots
            for day in snapshot.payload.days
            if day.open_day and day.session_date > request.after_date
        )
    except (InvalidKisKrSessionCalendarStoreError, OSError, ValueError):
        return None, None
    if not candidates:
        return None, None
    target = min(date for date, _ in candidates)
    authorities = tuple(
        snapshot for date, snapshot in candidates if date == target
    )
    if len({snapshot.snapshot_id for snapshot in authorities}) != 1:
        return target, None
    snapshot = authorities[0]
    return target, SessionCalendarProvenance(
        source="KIS_CHK_HOLIDAY",
        source_version=snapshot.payload.adapter_version,
        evidence_sha256=snapshot.snapshot_id,
        observed_at=snapshot.payload.observed_at,
    )


def _runtime_matches(request: FutureSessionPlanRequest) -> bool:
    runtime = request.frozen_runtime
    try:
        metadata = runtime.directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or runtime.directory.is_symlink()
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or _git(runtime.directory, "rev-parse", "HEAD")
            != runtime.commit_sha
            or _git(
                runtime.directory,
                "status",
                "--porcelain",
                "--untracked-files=all",
            )
        ):
            return False
        return all(
            subprocess.run(
                (
                    "git",
                    "-C",
                    str(runtime.directory),
                    "merge-base",
                    "--is-ancestor",
                    required,
                    runtime.commit_sha,
                ),
                check=False,
                capture_output=True,
                timeout=10,
            ).returncode
            == 0
            for required in request.required_runtime_commits
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def _frozen_runtime_can_read_experiment_ledger(
    request: FutureSessionPlanRequest,
) -> bool | None:
    runtime = request.frozen_runtime.directory
    import_root = (
        runtime
        if (runtime / "trading_agent").is_dir()
        else Path(__file__).parents[1]
    )
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(import_root)
        if existing_pythonpath is None
        else os.pathsep.join((str(import_root), existing_pythonpath))
    )
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from trading_agent.experiment_ledger_store import ExperimentLedgerReader\n"
        "print(int(ExperimentLedgerReader(Path(sys.argv[1])).is_initialized()))\n"
    )
    try:
        interpreter = (
            sys.executable
            if request.runtime_interpreter is None
            else str(request.runtime_interpreter)
        )
        completed = subprocess.run(
            (interpreter, "-c", script, str(request.experiment_ledger)),
            cwd=runtime,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    match completed.stdout.strip():
        case "1":
            return True
        case "0":
            return False
        case _:
            return None


def _ready(
    request: FutureSessionPlanRequest,
    target: dt.date,
    calendar: SessionCalendarProvenance,
    registrations: tuple[StrategyRegistrationIdentity, ...],
    bundle: KrThemeResearchRolloverBundle,
) -> ReadyToPrepareSessionPlan:
    values = {
        "market": request.market,
        "source_request_sha256": hashlib.sha256(
            canonical_request_json(request).encode()
        ).hexdigest(),
        "target_session": target,
        "compiled_at": request.compiled_at,
        "scheduler_main_sha": request.scheduler_main_sha,
        "frozen_runtime": request.frozen_runtime,
        "calendar_provenance": calendar,
        "strategy_registrations": registrations,
        "kr_rollover_bundle_sha256": (
            kr_theme_research_rollover_bundle_sha256(bundle)
        ),
        "kr_policy_sha256": hashlib.sha256(
            canonical_experiment_ledger_json(
                bundle.opportunity_policy
            ).encode()
        ).hexdigest(),
        "artifact_layout": FutureSessionArtifactLayout.from_root(
            request.artifact_root / request.market.value / target.isoformat()
        ),
        "trial_registration_state": (
            DeferredTrialRegistrationState.DEFERRED_UNTIL_PREOPEN
        ),
        "jobs": _jobs(target),
        "runtime_environment": None,
    }
    provisional = ReadyToPrepareSessionPlan.model_construct(
        plan_sha256="0" * 64,
        **values,
    )
    return ReadyToPrepareSessionPlan(
        plan_sha256=plan_content_sha256(provisional),
        **values,
    )


def _jobs(target: dt.date) -> tuple[JobTimingSpec, ...]:
    specifications = (
        ("source-readiness", dt.time(8, 30), "source_readiness"),
        ("prepare", dt.time(8, 55), "preopen_prepare"),
        ("start", dt.time(9), "shadow_trial_start"),
        ("source-cycle", dt.time(9, 5), "source_cycle"),
        ("terminal", dt.time(15, 32), "post_session_terminal"),
        ("verify", dt.time(15, 45), "post_session_verify"),
    )
    return tuple(
        JobTimingSpec(
            job_id=f"kr-{name}-{target.isoformat()}",
            run_at=dt.datetime.combine(target, time, tzinfo=_KST),
            purpose=purpose,
        )
        for name, time, purpose in specifications
    )


def _waiting(
    request: FutureSessionPlanRequest,
    target: dt.date | None,
    reason: WaitingAuthorityReason,
) -> WaitingSessionAuthority:
    return WaitingSessionAuthority(
        market=request.market,
        target_session=target,
        compiled_at=request.compiled_at,
        scheduler_main_sha=request.scheduler_main_sha,
        frozen_runtime=request.frozen_runtime,
        reasons=(reason,),
    )


__all__ = ("compile_kr_future_session_plan",)
