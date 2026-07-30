from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

from trading_agent.future_session_payload_renderer import render_job_payload
from trading_agent.future_session_plan_models import (
    FutureSessionPayloadMode,
    FutureSessionUsRole,
    JobTimingSpec,
)


def test_projection_invokes_only_for_changed_source_signature(
    tmp_path: Path,
) -> None:
    # Given
    opportunities = tmp_path / "opportunities.sqlite3"
    opportunities.write_text("opportunity\n", encoding="utf-8")
    signals = tmp_path / "signals.sqlite3"
    calls = tmp_path / "projection-calls.txt"
    job = _projection_job(
        tmp_path,
        opportunities,
        signals,
        calls,
        (
            "print -r -- called >> $1; "
            "count=$(/usr/bin/wc -l < $1); "
            "if (( count == 1 )); then print -r -- signal > $2; fi"
        ),
        seconds=4,
    )

    # When
    completed = _run(tmp_path, "projection-change.zsh", job, timeout=7)

    # Then
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert calls.read_text(encoding="utf-8").splitlines() == ["called", "called"]


def test_projection_no_input_completes_without_invocation(
    tmp_path: Path,
) -> None:
    # Given
    opportunities = tmp_path / "opportunities.sqlite3"
    signals = tmp_path / "signals.sqlite3"
    calls = tmp_path / "projection-calls.txt"
    job = _projection_job(
        tmp_path,
        opportunities,
        signals,
        calls,
        "print -r -- called >> $1",
        seconds=2,
    )

    # When
    completed = _run(tmp_path, "projection-empty.zsh", job, timeout=5)

    # Then
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not calls.exists()


def test_projection_retries_failed_signature_and_propagates_failure(
    tmp_path: Path,
) -> None:
    # Given
    opportunities = tmp_path / "opportunities.sqlite3"
    opportunities.write_text("opportunity\n", encoding="utf-8")
    signals = tmp_path / "signals.sqlite3"
    calls = tmp_path / "projection-calls.txt"
    job = _projection_job(
        tmp_path,
        opportunities,
        signals,
        calls,
        "print -r -- called >> $1; exit 7",
        seconds=3,
    )

    # When
    completed = _run(tmp_path, "projection-failure.zsh", job, timeout=6)

    # Then
    assert completed.returncode == 7
    assert completed.stderr == ""
    assert len(calls.read_text(encoding="utf-8").splitlines()) >= 2


def test_preflight_no_file_reaches_typed_censored_cutoff(
    tmp_path: Path,
) -> None:
    # Given
    watch = tmp_path / "paper_recommendations.sqlite3"
    calls = tmp_path / "preflight-calls.txt"
    job = _preflight_job(
        tmp_path,
        watch,
        calls,
        "print -r -- called >> $1",
        seconds=2,
    )

    # When
    completed = _run(tmp_path, "preflight-empty.zsh", job, timeout=5)

    # Then
    assert completed.returncode == 0
    assert completed.stdout == (
        '{"reason":"no_ready_current_setup","result":"censored"}\n'
    )
    assert completed.stderr == ""
    assert not calls.exists()


def test_preflight_ready_result_exits_immediately(tmp_path: Path) -> None:
    # Given
    watch = tmp_path / "paper_recommendations.sqlite3"
    watch.write_text("ready\n", encoding="utf-8")
    calls = tmp_path / "preflight-calls.txt"
    job = _preflight_job(
        tmp_path,
        watch,
        calls,
        'print -r -- called >> $1; print -r -- \'{"result":"ready"}\'',
        seconds=4,
    )

    # When
    completed = _run(tmp_path, "preflight-ready.zsh", job, timeout=3)

    # Then
    assert completed.returncode == 0
    assert completed.stdout == '{"result":"ready"}\n'
    assert completed.stderr == ""
    assert calls.read_text(encoding="utf-8").splitlines() == ["called"]


def test_preflight_preserves_nonready_log_then_censors_at_cutoff(
    tmp_path: Path,
) -> None:
    # Given
    watch = tmp_path / "paper_recommendations.sqlite3"
    watch.write_text("not-ready\n", encoding="utf-8")
    calls = tmp_path / "preflight-calls.txt"
    job = _preflight_job(
        tmp_path,
        watch,
        calls,
        (
            "print -r -- called >> $1; "
            "print -r -- "
            "'{\"reason\":\"invalid_current_orb_source\",\"result\":\"blocked\"}'; "
            "exit 1"
        ),
        seconds=2,
    )

    # When
    completed = _run(tmp_path, "preflight-nonready.zsh", job, timeout=5)

    # Then
    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        '{"reason":"invalid_current_orb_source","result":"blocked"}',
        '{"reason":"no_ready_current_setup","result":"censored"}',
    ]
    assert completed.stderr == ""
    assert calls.read_text(encoding="utf-8").splitlines() == ["called"]


def test_preflight_unclassified_failure_remains_nonzero(tmp_path: Path) -> None:
    # Given
    watch = tmp_path / "paper_recommendations.sqlite3"
    watch.write_text("unclassifiable\n", encoding="utf-8")
    calls = tmp_path / "preflight-calls.txt"
    job = _preflight_job(
        tmp_path,
        watch,
        calls,
        "print -r -- called >> $1; exit 2",
        seconds=4,
    )

    # When
    completed = _run(tmp_path, "preflight-unclassified.zsh", job, timeout=3)

    # Then
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert calls.read_text(encoding="utf-8").splitlines() == ["called"]


def _projection_job(
    tmp_path: Path,
    opportunities: Path,
    signals: Path,
    calls: Path,
    script: str,
    *,
    seconds: int,
) -> JobTimingSpec:
    return _polling_job(
        tmp_path,
        FutureSessionUsRole.US_HERMES_PROJECTION,
        FutureSessionPayloadMode.REPEAT_THROUGH_DEADLINE,
        (opportunities, signals),
        calls,
        script,
        seconds,
    )


def _preflight_job(
    tmp_path: Path,
    watch: Path,
    calls: Path,
    script: str,
    *,
    seconds: int,
) -> JobTimingSpec:
    return _polling_job(
        tmp_path,
        FutureSessionUsRole.US_DAY_PREFLIGHT_OBSERVER,
        FutureSessionPayloadMode.RETRY_UNTIL_SUCCESS,
        (watch,),
        calls,
        script,
        seconds,
    )


def _polling_job(
    tmp_path: Path,
    role: FutureSessionUsRole,
    mode: FutureSessionPayloadMode,
    sources: tuple[Path, ...],
    calls: Path,
    script: str,
    seconds: int,
) -> JobTimingSpec:
    now = dt.datetime.now(dt.UTC)
    return JobTimingSpec(
        job_id=role.value,
        role=role,
        run_at=now,
        purpose=role.value,
        command=("/bin/zsh", "-c", script, "_", str(calls), str(sources[-1])),
        source_paths=sources,
        destination_paths=(tmp_path / "destination.sqlite3",),
        payload_mode=mode,
        poll_until=now + dt.timedelta(seconds=seconds),
        poll_interval_seconds=1,
    )


def _run(
    tmp_path: Path,
    name: str,
    job: JobTimingSpec,
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    wrapper = tmp_path / name
    wrapper.write_text(render_job_payload(job), encoding="utf-8")
    wrapper.chmod(0o700)
    return subprocess.run(
        (str(wrapper),),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
