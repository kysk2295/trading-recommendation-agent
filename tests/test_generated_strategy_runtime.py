from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trading_agent.generated_strategy_runtime import (
    GeneratedStrategyRuntimeError,
    require_generated_strategy_runtime,
    resolve_generated_strategy_runtime,
)


def test_runtime_identity_is_deterministic_for_bound_python() -> None:
    # Given: the Python executable running the research coordinator.
    executable = Path(sys.executable)

    # When: the generated-strategy runtime is resolved twice.
    first = resolve_generated_strategy_runtime(executable)
    second = resolve_generated_strategy_runtime(executable)

    # Then: executable, package inventory, sandbox profile, and aggregate identity are stable.
    assert first == second
    assert first.python_executable == executable.resolve(strict=True)
    assert len(first.python_executable_sha256) == 64
    assert len(first.package_inventory_sha256) == 64
    assert len(first.runtime_fingerprint) == 64
    assert first.sandbox_executable == Path("/usr/bin/sandbox-exec")
    assert first.sandbox_profile_version == "generated_strategy_sandbox_v1"


def test_runtime_identity_rejects_executable_substitution(tmp_path: Path) -> None:
    # Given: a private executable wrapper whose bytes are bound into a runtime identity.
    executable = tmp_path / "python-copy"
    executable.write_text(
        f"#!/bin/sh\nexec {Path(sys.executable).resolve(strict=True)} \"$@\"\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    identity = resolve_generated_strategy_runtime(executable)
    executable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    executable.chmod(0o700)

    # When/Then: execution-time revalidation rejects the changed file.
    with pytest.raises(GeneratedStrategyRuntimeError):
        _ = require_generated_strategy_runtime(identity)


def test_runtime_identity_canonicalizes_symlinked_python(tmp_path: Path) -> None:
    # Given: an executable path that enters through a symlink.
    executable = tmp_path / "python-link"
    executable.symlink_to(sys.executable)

    # When: the runtime boundary resolves the executable identity.
    identity = resolve_generated_strategy_runtime(executable)

    # Then: later execution is bound to the immutable target instead of the symlink name.
    assert identity.python_executable == Path(sys.executable).resolve(strict=True)
