from __future__ import annotations

import datetime as dt
import subprocess
import time
from pathlib import Path

import pytest

from trading_agent.future_session_payload_renderer import render_job_payload
from trading_agent.future_session_plan_models import (
    FutureSessionPayloadMode,
    FutureSessionUsRole,
    JobTimingSpec,
)


def test_repeat_payload_runs_through_deadline(tmp_path: Path) -> None:
    # Given
    counter = tmp_path / "repeat.txt"
    now = dt.datetime.now(dt.UTC)
    deadline = now + dt.timedelta(seconds=2)
    job = JobTimingSpec(
        job_id="repeat",
        run_at=now,
        purpose="repeat",
        command=(
            "/bin/zsh",
            "-c",
            (
                "count=0; [[ -f $1 ]] && count=$(/bin/cat $1); "
                "count=$(( count + 1 )); print -r -- $count > $1; "
                "(( count >= 2 ))"
            ),
            "_",
            str(counter),
        ),
        payload_mode=FutureSessionPayloadMode.REPEAT_THROUGH_DEADLINE,
        poll_until=deadline,
        poll_interval_seconds=1,
    )
    wrapper = _wrapper(tmp_path, "repeat.zsh", job)

    # When
    completed = subprocess.run(
        (str(wrapper),),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    # Then
    assert completed.returncode == 0
    assert int(counter.read_text(encoding="utf-8")) >= 2


def test_retry_payload_waits_then_propagates_real_success(tmp_path: Path) -> None:
    # Given
    counter = tmp_path / "retry.txt"
    now = dt.datetime.now(dt.UTC)
    not_before = now + dt.timedelta(seconds=2)
    deadline = now + dt.timedelta(seconds=5)
    job = JobTimingSpec(
        job_id="retry",
        run_at=now,
        purpose="retry",
        command=(
            "/bin/zsh",
            "-c",
            (
                "count=0; [[ -f $1 ]] && count=$(/bin/cat $1); "
                "count=$(( count + 1 )); print -r -- $count > $1; "
                "(( count >= 2 ))"
            ),
            "_",
            str(counter),
        ),
        payload_mode=FutureSessionPayloadMode.RETRY_UNTIL_SUCCESS,
        not_before=not_before,
        poll_until=deadline,
        poll_interval_seconds=1,
    )
    wrapper = _wrapper(tmp_path, "retry.zsh", job)

    # When
    started = time.monotonic()
    completed = subprocess.run(
        (str(wrapper),),
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
    )
    elapsed = time.monotonic() - started

    # Then
    assert completed.returncode == 0
    assert counter.read_text(encoding="utf-8") == "2\n"
    assert elapsed >= 1


@pytest.mark.parametrize(
    "mode",
    (
        FutureSessionPayloadMode.REPEAT_THROUGH_DEADLINE,
        FutureSessionPayloadMode.RETRY_UNTIL_SUCCESS,
    ),
)
def test_polling_payload_propagates_failure_at_deadline(
    tmp_path: Path,
    mode: FutureSessionPayloadMode,
) -> None:
    # Given
    now = dt.datetime.now(dt.UTC)
    job = JobTimingSpec(
        job_id=mode.value,
        run_at=now,
        purpose=mode.value,
        command=("/usr/bin/false",),
        payload_mode=mode,
        poll_until=now + dt.timedelta(seconds=1),
        poll_interval_seconds=1,
    )
    wrapper = _wrapper(tmp_path, f"{mode.value}.zsh", job)

    # When
    completed = subprocess.run(
        (str(wrapper),),
        check=False,
        capture_output=True,
        text=True,
        timeout=4,
    )

    # Then
    assert completed.returncode == 1


def test_finalizer_waits_for_inactive_watcher_and_stable_source(
    tmp_path: Path,
) -> None:
    # Given
    active = tmp_path / "watcher-active"
    active.touch()
    source = tmp_path / "paper_recommendations.sqlite3"
    source.write_text("first\n", encoding="utf-8")
    calls = tmp_path / "finalizer-calls.txt"
    now = dt.datetime.now(dt.UTC)
    job = _gated_finalizer_job(
        now=now,
        deadline=now + dt.timedelta(seconds=20),
        active=active,
        source=source,
        calls=calls,
    )
    wrapper = _wrapper(tmp_path, "gated-finalizer.zsh", job)

    # When
    running = subprocess.Popen(
        (str(wrapper),),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(1)
    assert not calls.exists()
    active.unlink()
    time.sleep(1)
    source.write_text("second-state\n", encoding="utf-8")
    assert not calls.exists()
    stdout, stderr = running.communicate(timeout=15)

    # Then
    assert running.returncode == 0
    assert stdout == ""
    assert stderr == ""
    assert calls.read_text(encoding="utf-8").splitlines() == ["called"]


@pytest.mark.parametrize(
    ("reason", "watcher_active", "source_exists"),
    (
        ("watcher_active", True, True),
        ("watch_source_missing", False, False),
        ("watch_source_unstable", False, True),
    ),
)
def test_finalizer_gate_blocks_with_typed_reason_at_deadline(
    tmp_path: Path,
    reason: str,
    watcher_active: bool,
    source_exists: bool,
) -> None:
    # Given
    active = tmp_path / "watcher-active"
    if watcher_active:
        active.touch()
    source = tmp_path / "paper_recommendations.sqlite3"
    if source_exists:
        source.write_text("source\n", encoding="utf-8")
    calls = tmp_path / "finalizer-calls.txt"
    now = dt.datetime.now(dt.UTC)
    job = _gated_finalizer_job(
        now=now,
        deadline=now + dt.timedelta(seconds=2),
        active=active,
        source=source,
        calls=calls,
    )
    wrapper = _wrapper(tmp_path, f"{reason}.zsh", job)

    # When
    completed = subprocess.run(
        (str(wrapper),),
        check=False,
        capture_output=True,
        text=True,
        timeout=6,
    )

    # Then
    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        f'{{"reason":"{reason}","result":"blocked"}}\n'
    )
    assert not calls.exists()


def _gated_finalizer_job(
    *,
    now: dt.datetime,
    deadline: dt.datetime,
    active: Path,
    source: Path,
    calls: Path,
) -> JobTimingSpec:
    return JobTimingSpec.model_validate(
        {
            "job_id": "gated-finalizer",
            "run_at": now,
            "purpose": "gated-finalizer",
            "role": FutureSessionUsRole.US_DAY_CLOSE_FINALIZER,
            "command": (
                "/bin/zsh",
                "-c",
                "print -r -- called >> $1",
                "_",
                str(calls),
            ),
            "payload_mode": FutureSessionPayloadMode.RETRY_UNTIL_SUCCESS,
            "not_before": now,
            "poll_until": deadline,
            "poll_interval_seconds": 1,
            "finalizer_gate": {
                "watcher_label": "ai.trading-agent.test-watcher",
                "watcher_active_probe": (
                    "/bin/zsh",
                    "-c",
                    "print -r -- probe; print -u2 -r -- probe; [[ -f $1 ]]",
                    "_",
                    str(active),
                ),
                "source_path": source,
                "stability_seconds": 5,
            },
        }
    )


def _wrapper(
    tmp_path: Path,
    name: str,
    job: JobTimingSpec,
) -> Path:
    wrapper = tmp_path / name
    wrapper.write_text(render_job_payload(job), encoding="utf-8")
    wrapper.chmod(0o700)
    return wrapper
