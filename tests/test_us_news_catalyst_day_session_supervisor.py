from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.us_news_catalyst_day_session_audit import (
    UsNewsCatalystDaySessionEventStatus,
    UsNewsCatalystDaySessionPhase,
)
from trading_agent.us_news_catalyst_day_session_evidence import (
    UsNewsCatalystDaySessionEvidence,
)
from trading_agent.us_news_catalyst_day_session_manifest import (
    UsNewsCatalystDaySessionIdentity,
    UsNewsCatalystDaySessionManifest,
    UsNewsCatalystDaySessionPaths,
    build_us_news_catalyst_day_session_manifest,
)
from trading_agent.us_news_catalyst_day_session_supervisor import (
    UsNewsCatalystDaySessionAction,
    UsNewsCatalystDaySessionActionStatus,
    UsNewsCatalystDaySessionRuntime,
    run_us_news_catalyst_day_session_tick,
)

OBSERVED = dt.datetime(2026, 7, 21, 14, tzinfo=dt.UTC)


def test_each_tick_executes_at_most_one_missing_phase(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    states: dict[UsNewsCatalystDaySessionPhase, UsNewsCatalystDaySessionEvidence] = {}
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        commands.append(command)
        phase = UsNewsCatalystDaySessionPhase(command[0])
        states[phase] = _evidence(phase)
        return 0

    runtime = UsNewsCatalystDaySessionRuntime(
        runner=runner,
        clock=lambda: OBSERVED,
        source_state=lambda _manifest, phase, _observed_at: states.get(phase),
        action=lambda _manifest, phase, _observed_at: UsNewsCatalystDaySessionAction(
            UsNewsCatalystDaySessionActionStatus.EXECUTE,
            (phase.value,),
            None,
        ),
    )

    results = tuple(
        run_us_news_catalyst_day_session_tick(manifest, OBSERVED, runtime)
        for _index in range(6)
    )
    replay = run_us_news_catalyst_day_session_tick(manifest, OBSERVED, runtime)

    assert tuple(result.phase for result in results) == tuple(UsNewsCatalystDaySessionPhase)
    assert all(result.event is not None for result in results)
    assert tuple(command[0] for command in commands) == tuple(
        phase.value for phase in UsNewsCatalystDaySessionPhase
    )
    assert replay.phase is None
    assert replay.event is None


def test_existing_domain_evidence_is_recovered_without_command(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    recovered = _evidence(UsNewsCatalystDaySessionPhase.REGISTER)
    calls = 0

    def runner(_command: tuple[str, ...]) -> int:
        nonlocal calls
        calls += 1
        return 1

    runtime = UsNewsCatalystDaySessionRuntime(
        runner=runner,
        clock=lambda: OBSERVED,
        source_state=lambda _manifest, phase, _observed_at: (
            recovered if phase is UsNewsCatalystDaySessionPhase.REGISTER else None
        ),
        action=lambda _manifest, phase, _observed_at: UsNewsCatalystDaySessionAction(
            UsNewsCatalystDaySessionActionStatus.EXECUTE,
            (phase.value,),
            None,
        ),
    )

    result = run_us_news_catalyst_day_session_tick(manifest, OBSERVED, runtime)

    assert calls == 0
    assert result.event is not None
    assert result.event.status is UsNewsCatalystDaySessionEventStatus.RECOVERED
    assert result.event.evidence_sha256 == recovered.evidence_sha256


def test_expired_missing_phase_is_audited_as_skipped(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    skipped = UsNewsCatalystDaySessionEvidence(
        phase=UsNewsCatalystDaySessionPhase.REGISTER,
        evidence_sha256="f" * 64,
        skipped_reason="registration_already_terminalized",
    )
    runtime = UsNewsCatalystDaySessionRuntime(
        runner=lambda _command: 1,
        clock=lambda: OBSERVED,
        source_state=lambda _manifest, _phase, _observed_at: skipped,
        action=lambda _manifest, phase, _observed_at: UsNewsCatalystDaySessionAction(
            UsNewsCatalystDaySessionActionStatus.EXECUTE,
            (phase.value,),
            None,
        ),
    )

    result = run_us_news_catalyst_day_session_tick(manifest, OBSERVED, runtime)

    assert result.event is not None
    assert result.event.status is UsNewsCatalystDaySessionEventStatus.SKIPPED
    assert result.event.reason_code == "registration_already_terminalized"


def test_phase_that_expires_during_command_is_skipped_not_completed(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    before = OBSERVED
    after = OBSERVED + dt.timedelta(seconds=2)
    skipped = UsNewsCatalystDaySessionEvidence(
        phase=UsNewsCatalystDaySessionPhase.REGISTER,
        evidence_sha256="e" * 64,
        skipped_reason="phase_window_missed",
    )
    runtime = UsNewsCatalystDaySessionRuntime(
        runner=lambda _command: 1,
        clock=lambda: after,
        source_state=lambda _manifest, _phase, observed_at: (
            skipped if observed_at == after else None
        ),
        action=lambda _manifest, phase, _observed_at: UsNewsCatalystDaySessionAction(
            UsNewsCatalystDaySessionActionStatus.EXECUTE,
            (phase.value,),
            None,
        ),
    )

    result = run_us_news_catalyst_day_session_tick(manifest, before, runtime)

    assert result.event is not None
    assert result.event.status is UsNewsCatalystDaySessionEventStatus.SKIPPED
    assert result.event.command_exit_code is None
    assert result.event.reason_code == "phase_window_missed"


def _evidence(phase: UsNewsCatalystDaySessionPhase) -> UsNewsCatalystDaySessionEvidence:
    digit = tuple(UsNewsCatalystDaySessionPhase).index(phase) + 1
    return UsNewsCatalystDaySessionEvidence(
        phase=phase,
        evidence_sha256=f"{digit:x}" * 64,
        skipped_reason=None,
    )


def _manifest(tmp_path: Path) -> UsNewsCatalystDaySessionManifest:
    root = tmp_path.absolute()
    paths = UsNewsCatalystDaySessionPaths(
        experiment_ledger=root / "ledger.sqlite3",
        registration_manifest=root / "research.json",
        projection_root=root / "projections",
        evidence_root=root / "evidence",
        security_master_store=root / "security.sqlite3",
        artifact_root=root / "artifacts",
        plan_root=root / "plans",
        profile_root=root / "profiles",
        runtime_root=root / "runtime",
        canonical_root=root / "canonical",
        feature_root=root / "features",
        receipt_root=root / "receipts",
        review_root=root / "reviews",
        audit_store=root / "audit.sqlite3",
        output_root=root / "reports",
        secret_path=root / "alpaca.env",
    )
    return build_us_news_catalyst_day_session_manifest(
        UsNewsCatalystDaySessionIdentity(
            strategy_version="us-news-catalyst-recency-v1-code-fixture",
            code_version="fixture-v1",
            session_date=dt.date(2026, 7, 21),
            created_at=dt.datetime(2026, 7, 21, 12, tzinfo=dt.UTC),
            paths=paths,
        )
    )
