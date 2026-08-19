from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, TypedDict, Unpack

import pytest

from tests.test_generated_strategy_sandbox import _bar, _publish, _sandbox
from trading_agent import generated_strategy_session as session_module
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactStore,
    PublishedGeneratedStrategy,
)
from trading_agent.generated_strategy_execution import GeneratedStrategyExecutionError
from trading_agent.generated_strategy_source import require_generated_strategy_session_source


class _PopenArguments(TypedDict, total=False):
    cwd: Path
    env: dict[str, str]
    stdin: int
    stdout: int
    stderr: BinaryIO
    start_new_session: bool
    preexec_fn: Callable[[], None]
    pass_fds: tuple[int, ...]


def _signal_source(rationale: str) -> str:
    return (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            return {'symbol': bar['symbol'], 'timestamp': bar['timestamp'], "
        f"'entry': bar['close'], 'stop': bar['low'], 'rationale': '{rationale}'}}\n"
        "    return Strategy()\n"
    )


def test_capture_rejects_source_replaced_after_artifact_load(tmp_path: Path) -> None:
    # Given: an artifact loaded successfully before its source path is replaced.
    published = _publish(tmp_path, _signal_source("original"))
    store = GeneratedStrategyArtifactStore(
        published.source_path.parents[1],
        published.artifact.payload.runtime,
    )
    loaded = store.load(published.artifact.artifact_id)
    published = PublishedGeneratedStrategy(
        loaded,
        published.source_path,
        published.manifest_path,
        False,
    )
    published.source_path.write_text(_signal_source("replacement"), encoding="utf-8")
    published.source_path.chmod(0o600)

    # When/Then: stable source capture rejects bytes that differ from the loaded manifest.
    with pytest.raises(GeneratedStrategyExecutionError, match="generated_artifact_invalid"):
        _ = _sandbox(tmp_path, published).capture_source(published)


def test_two_sessions_execute_one_captured_source_after_original_replacement(tmp_path: Path) -> None:
    # Given: one verified in-memory source snapshot captured from a published artifact.
    published = _publish(tmp_path, _signal_source("original"))
    sandbox = _sandbox(tmp_path, published)
    snapshot = sandbox.capture_source(published)
    assert snapshot.source_sha256 == published.artifact.payload.source_sha256

    # When: the original path is replaced between two executions of that snapshot.
    with sandbox.open_source_session(snapshot) as first_session:
        first = first_session.observe(_bar(31), None)
    published.source_path.write_text(_signal_source("replacement"), encoding="utf-8")
    published.source_path.chmod(0o600)
    with sandbox.open_source_session(snapshot) as second_session:
        second = second_session.observe(_bar(31), None)

    # Then: both runs execute the exact captured bytes rather than reopening the artifact path.
    assert first is not None and first.rationale == "original"
    assert second is not None and second.rationale == "original"


@pytest.mark.parametrize("replacement", [None, b"replaced"])
def test_session_source_guard_rejects_missing_or_replaced_snapshot(
    tmp_path: Path,
    replacement: bytes | None,
) -> None:
    # Given: the expected digest for a private per-session source snapshot.
    source = tmp_path / "strategy.py"
    source.write_bytes(b"original")
    source.chmod(0o600)
    expected_sha256 = "0682c5f2076f099c34cfdd15a9e063849ed437a49677e6fcc5b4198c76575be5"
    if replacement is None:
        source.unlink()
    else:
        source.write_bytes(replacement)
        source.chmod(0o600)

    # When/Then: the immediate pre-Popen guard fails closed.
    with pytest.raises(GeneratedStrategyExecutionError, match="session_source_invalid"):
        require_generated_strategy_session_source(source, expected_sha256)


def test_session_source_guard_accepts_exact_private_snapshot(tmp_path: Path) -> None:
    # Given: a private source snapshot and its independent exact digest.
    source = tmp_path / "strategy.py"
    source.write_bytes(b"original")
    source.chmod(0o600)

    # When: the immediate pre-Popen guard checks the unchanged file.
    require_generated_strategy_session_source(
        source,
        "0682c5f2076f099c34cfdd15a9e063849ed437a49677e6fcc5b4198c76575be5",
    )

    # Then: the exact snapshot remains accepted without mutation.
    assert source.read_bytes() == b"original"


def test_open_source_session_rejects_snapshot_bytes_with_stale_digest(tmp_path: Path) -> None:
    # Given: a captured snapshot whose immutable dataclass is bypassed with different bytes.
    published = _publish(tmp_path, _signal_source("original"))
    sandbox = _sandbox(tmp_path, published)
    snapshot = replace(sandbox.capture_source(published), source_bytes=b"replacement")

    # When/Then: materialization is blocked before a session process can start.
    with pytest.raises(GeneratedStrategyExecutionError, match="session_source_invalid"):
        _ = sandbox.open_source_session(snapshot)


def test_post_guard_path_replacement_never_changes_executed_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real sandbox and a Popen boundary that replaces the guarded session pathname.
    published = _publish(tmp_path, _signal_source("original"))
    sandbox = _sandbox(tmp_path, published)
    snapshot = sandbox.capture_source(published)
    real_popen = subprocess.Popen
    observed_command: list[tuple[str, ...]] = []
    observed_descriptors: list[tuple[int, ...]] = []
    replaced_paths: list[Path] = []

    def replace_then_spawn(
        command: tuple[str, ...],
        **kwargs: Unpack[_PopenArguments],
    ) -> subprocess.Popen[bytes]:
        cwd = kwargs.get("cwd")
        if not isinstance(cwd, Path):
            return real_popen(command, **kwargs)
        source_path = cwd / "strategy.py"
        source_path.write_text(_signal_source("replaced-after-guard"), encoding="utf-8")
        source_path.chmod(0o600)
        replaced_paths.append(source_path)
        observed_command.append(command)
        observed_descriptors.append(kwargs.get("pass_fds", ()))
        return real_popen(command, **kwargs)

    monkeypatch.setattr(session_module.subprocess, "Popen", replace_then_spawn)

    # When: the child starts after the genuine descriptor guard.
    session = sandbox.open_source_session(snapshot)
    with pytest.raises(OSError):
        _ = os.fstat(observed_descriptors[0][0])
    with session:
        signal = session.observe(_bar(31), None)

    # Then: only inherited verified bytes execute and the private session root is removed.
    assert signal is not None and signal.rationale == "original"
    assert len(observed_descriptors) == 1 and len(observed_descriptors[0]) == 1
    assert str(replaced_paths[0]) not in observed_command[0]
    assert tuple(sandbox.task_root.iterdir()) == ()


def test_popen_failure_closes_source_descriptor_and_session_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a real verified snapshot and a process boundary that refuses the spawn.
    published = _publish(tmp_path, _signal_source("original"))
    sandbox = _sandbox(tmp_path, published)
    snapshot = sandbox.capture_source(published)
    real_popen = subprocess.Popen
    observed_descriptors: list[tuple[int, ...]] = []

    def fail_spawn(
        command: tuple[str, ...],
        **kwargs: Unpack[_PopenArguments],
    ) -> subprocess.Popen[bytes]:
        if not isinstance(kwargs.get("cwd"), Path):
            return real_popen(command, **kwargs)
        observed_descriptors.append(kwargs.get("pass_fds", ()))
        raise OSError

    monkeypatch.setattr(session_module.subprocess, "Popen", fail_spawn)

    # When: Popen fails after descriptor verification.
    with pytest.raises(GeneratedStrategyExecutionError, match="sandbox_preflight_failed"):
        _ = sandbox.open_source_session(snapshot)

    # Then: the parent descriptor is closed and its private session directory is removed.
    with pytest.raises(OSError):
        _ = os.fstat(observed_descriptors[0][0])
    assert tuple(sandbox.task_root.iterdir()) == ()


@pytest.mark.parametrize("mode", ["digest_mismatch", "missing_fd"])
def test_runner_rejects_untrusted_inherited_source_descriptor(tmp_path: Path, mode: str) -> None:
    # Given: the runner invoked with either a mismatched digest or a descriptor it did not inherit.
    source = tmp_path / "strategy.py"
    source.write_text(_signal_source("original"), encoding="utf-8")
    source.chmod(0o600)
    descriptor = source.open("rb")
    inherited = () if mode == "missing_fd" else (descriptor.fileno(),)
    expected_sha256 = "0" * 64
    runner = Path(session_module.__file__).with_name("generated_strategy_runner.py")

    # When: the isolated runner attempts to read and verify its only source channel.
    completed = subprocess.run(
        (sys.executable, "-I", str(runner), str(descriptor.fileno()), expected_sha256),
        check=False,
        capture_output=True,
        pass_fds=inherited,
    )
    descriptor.close()

    # Then: runner-side verification fails before compiling generated bytes.
    response = json.loads(completed.stdout.splitlines()[0])
    assert completed.returncode == 1
    assert response["reason"] == "generated_strategy_source_invalid"
