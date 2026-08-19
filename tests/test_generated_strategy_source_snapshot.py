from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_generated_strategy_sandbox import _bar, _publish, _sandbox
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactStore,
    PublishedGeneratedStrategy,
)
from trading_agent.generated_strategy_execution import GeneratedStrategyExecutionError
from trading_agent.generated_strategy_source import require_generated_strategy_session_source


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
