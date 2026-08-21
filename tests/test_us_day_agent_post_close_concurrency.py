from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path
from typing import Literal, assert_never

import pytest

from tests.us_day_agent_tick_close_support import CLOSE_AT
from trading_agent.private_immutable_file import publish_private_immutable_text, read_private_text
from trading_agent.us_day_agent_service import (
    UsDayAgentService,
    UsDayAgentServiceConfig,
    UsDayAgentTickRequest,
    UsDayAgentTickResult,
    tick_id_for,
)
from trading_agent.us_day_post_close_lease import UsDayPostCloseLeaseKey, us_day_post_close_lease

type UnsafeLockKind = Literal["parent_symlink", "lock_symlink", "non_private", "hard_link"]


class _UnexpectedPhaseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _ConcurrentPostCloseRuntime:
    effects: Path
    ready: ProcessEvent | None
    release: ProcessEvent | None
    synchronize: bool
    replacement: tuple[Path, Path] | None = None

    def premarket(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        del request
        raise _UnexpectedPhaseError

    def regular(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        del request
        raise _UnexpectedPhaseError

    def cutoff(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        del request
        raise _UnexpectedPhaseError

    def eod(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        del request
        raise _UnexpectedPhaseError

    def recover(self, request: UsDayAgentTickRequest) -> None:
        del request
        self._append("recover")
        if self.synchronize:
            assert self.ready is not None and self.release is not None
            self.ready.set()
            assert self.release.wait(timeout=10)

    def post_close(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        for stage in ("finalize", "payload", "report", "loop", "proposal", "capsule"):
            self._append(stage)
        if self.replacement is not None:
            lock, held = self.replacement
            lock.replace(held)
            lock.write_bytes(b"")
            lock.chmod(0o600)
        return UsDayAgentTickResult.accepted(
            request,
            paper_status="finalized",
            market_close_report_id="a" * 64,
            challenger_version_id="b" * 64,
        )

    def _append(self, stage: str) -> None:
        descriptor = os.open(
            self.effects,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            _ = os.write(descriptor, f"{stage}\n".encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class _ChildInvocation:
    output: Path
    receipts: Path
    request: UsDayAgentTickRequest
    runtime: _ConcurrentPostCloseRuntime


def _run_child(invocation: _ChildInvocation) -> None:
    service = UsDayAgentService(
        UsDayAgentServiceConfig(invocation.receipts),
        invocation.runtime,
        lambda: CLOSE_AT,
    )
    result = service.tick(invocation.request)
    assert publish_private_immutable_text(invocation.output, result.model_dump_json())


def _crash_holder(root: Path, key: UsDayPostCloseLeaseKey, ready: ProcessEvent) -> None:
    with us_day_post_close_lease(root, key):
        ready.set()
        os._exit(17)


def test_post_close_processes_serialize_side_effects_and_replay_exact_receipt(tmp_path: Path) -> None:
    # Given: two scheduler processes for one post-close tick, synchronized inside the first recovery call.
    root = (tmp_path / "private").absolute()
    root.mkdir(mode=0o700)
    receipts = root / "receipts"
    effects = root / "effects.log"
    request = UsDayAgentTickRequest(
        situation_path=root / "source.json",
        evaluated_at=CLOSE_AT,
        source_sha256="c" * 64,
    )
    context = get_context("spawn")
    ready = context.Event()
    release = context.Event()
    primary_output = root / "primary.json"
    busy_output = root / "busy.json"

    # When: the second process enters while the first owns the tick, then the first completes and is replayed.
    primary = context.Process(
        target=_run_child,
        args=(
            _ChildInvocation(
                primary_output,
                receipts,
                request,
                _ConcurrentPostCloseRuntime(effects, ready, release, True),
            ),
        ),
    )
    primary.start()
    assert ready.wait(timeout=10)
    contender = context.Process(
        target=_run_child,
        args=(
            _ChildInvocation(
                busy_output,
                receipts,
                request,
                _ConcurrentPostCloseRuntime(effects, None, None, False),
            ),
        ),
    )
    contender.start()
    contender.join(timeout=10)
    assert not contender.is_alive()
    assert contender.exitcode == 0
    release.set()
    primary.join(timeout=10)
    assert not primary.is_alive()
    assert primary.exitcode == 0
    replay = UsDayAgentService(
        UsDayAgentServiceConfig(receipts),
        _ConcurrentPostCloseRuntime(effects, None, None, False),
        lambda: CLOSE_AT,
    ).tick(request)

    # Then: the contender is transiently busy, every side effect occurs once, and replay is exact.
    primary_result = UsDayAgentTickResult.model_validate_json(read_private_text(primary_output))
    busy_result = UsDayAgentTickResult.model_validate_json(read_private_text(busy_output))
    assert busy_result.status == "blocked"
    assert busy_result.reason == "post_close_busy"
    assert primary_result.status == "accepted"
    assert replay == primary_result
    assert effects.read_text(encoding="utf-8").splitlines() == [
        "recover",
        "finalize",
        "payload",
        "report",
        "loop",
        "proposal",
        "capsule",
    ]


def test_replaced_post_close_lock_fails_without_publishing_receipt(tmp_path: Path) -> None:
    # Given: a post-close runtime that replaces its held lease name before returning a result.
    root = (tmp_path / "private").absolute()
    root.mkdir(mode=0o700)
    receipts = root / "receipts"
    effects = root / "effects.log"
    request = UsDayAgentTickRequest(
        situation_path=root / "source.json",
        evaluated_at=CLOSE_AT,
        source_sha256="d" * 64,
    )
    tick_id = tick_id_for(request)
    lock = receipts / ".post_close_leases" / tick_id / f"XNYS-{CLOSE_AT.date().isoformat()}.lock"
    held = root / "held.lock"
    service = UsDayAgentService(
        UsDayAgentServiceConfig(receipts),
        _ConcurrentPostCloseRuntime(effects, None, None, False, (lock, held)),
        lambda: CLOSE_AT,
    )

    # When: the process completes its side effects against the replaced lock binding.
    try:
        result = service.tick(request)
    finally:
        lock.unlink(missing_ok=True)
        held.replace(lock)

    # Then: the unsafe binding is blocked and cannot publish an accepted tick receipt.
    assert result.status == "blocked"
    assert result.reason == "post_close_lease_invalid"
    assert not (receipts / f"{tick_id}.json").exists()


def test_crashed_post_close_lease_holder_releases_for_retry(tmp_path: Path) -> None:
    # Given: a child process that exits abruptly while holding an OS-backed post-close lease.
    root = (tmp_path / "private").absolute()
    root.mkdir(mode=0o700)
    key = UsDayPostCloseLeaseKey("e" * 64, "XNYS-2026-08-20")
    context = get_context("spawn")
    ready = context.Event()
    holder = context.Process(target=_crash_holder, args=(root / "leases", key, ready))
    holder.start()

    # When: the parent observes the crash and immediately acquires the same lease.
    assert ready.wait(timeout=10)
    holder.join(timeout=10)
    with us_day_post_close_lease(root / "leases", key):
        lock = root / "leases" / key.tick_id / f"{key.session_id}.lock"

    # Then: OS cleanup made the lease reusable without weakening private-file metadata.
    assert holder.exitcode == 17
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


@pytest.mark.parametrize("unsafe_kind", ("parent_symlink", "lock_symlink", "non_private", "hard_link"))
def test_unsafe_post_close_lease_fails_before_recovery(tmp_path: Path, unsafe_kind: UnsafeLockKind) -> None:
    # Given: a lease path with one unsafe ancestor, symlink, mode, or inode-link condition.
    root = (tmp_path / "private").absolute()
    root.mkdir(mode=0o700)
    receipts = root / "receipts"
    effects = root / "effects.log"
    request = UsDayAgentTickRequest(
        situation_path=root / "source.json",
        evaluated_at=CLOSE_AT,
        source_sha256="f" * 64,
    )
    tick_id = tick_id_for(request)
    lease_root = receipts / ".post_close_leases"
    lock_parent = lease_root / tick_id
    lock = lock_parent / f"XNYS-{CLOSE_AT.date().isoformat()}.lock"
    target = root / "target.lock"
    match unsafe_kind:
        case "parent_symlink":
            real = root / "real-leases"
            real.mkdir(mode=0o700)
            receipts.mkdir(mode=0o700)
            lease_root.symlink_to(real, target_is_directory=True)
        case "lock_symlink":
            lock_parent.mkdir(parents=True, mode=0o700)
            target.write_bytes(b"unchanged")
            target.chmod(0o600)
            lock.symlink_to(target)
        case "non_private":
            lock_parent.mkdir(parents=True, mode=0o700)
            lock.write_bytes(b"unchanged")
            lock.chmod(0o644)
        case "hard_link":
            lock_parent.mkdir(parents=True, mode=0o700)
            target.write_bytes(b"unchanged")
            target.chmod(0o600)
            os.link(target, lock)
        case unreachable:
            assert_never(unreachable)
    service = UsDayAgentService(
        UsDayAgentServiceConfig(receipts),
        _ConcurrentPostCloseRuntime(effects, None, None, False),
        lambda: CLOSE_AT,
    )

    # When: the scheduler attempts post-close recovery through the unsafe lease path.
    result = service.tick(request)

    # Then: it fails closed before recovery or receipt publication and preserves any target.
    assert result.status == "blocked"
    assert result.reason == "post_close_lease_invalid"
    assert not effects.exists()
    assert not (receipts / f"{tick_id}.json").exists()
    if target.exists():
        assert target.read_bytes() == b"unchanged"
