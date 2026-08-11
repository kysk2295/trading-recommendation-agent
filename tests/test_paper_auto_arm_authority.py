from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trading_agent.hermes_arm_request import HermesArmFailure, InvalidHermesArmRequestError
from trading_agent.paper_auto_arm_authority import require_current_clean_main, require_frozen_commit


def test_authority_requires_exact_clean_main_commit(tmp_path: Path) -> None:
    # Given: one clean main repository at a known current commit.
    repository = tmp_path / "repository"
    repository.mkdir(mode=0o700)
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "fixture")
    commit = _git(repository, "rev-parse", "HEAD")
    _git(repository, "update-ref", "refs/remotes/origin/main", commit)

    # When / Then: exact clean main passes, while dirt and a non-main branch fail closed.
    require_current_clean_main(repository, commit)
    (repository / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(InvalidHermesArmRequestError) as dirty:
        require_current_clean_main(repository, commit)
    assert dirty.value.reason is HermesArmFailure.DIRTY_COMMIT
    (repository / "untracked.txt").unlink()
    _git(repository, "switch", "-c", "feature")
    with pytest.raises(InvalidHermesArmRequestError) as branch:
        require_current_clean_main(repository, commit)
    assert branch.value.reason is HermesArmFailure.COMMIT_MISMATCH

    _git(repository, "switch", "main")
    (repository / "tracked.txt").write_text("advanced\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "advanced")
    advanced = _git(repository, "rev-parse", "HEAD")
    with pytest.raises(InvalidHermesArmRequestError) as divergence:
        require_current_clean_main(repository, advanced)
    assert divergence.value.reason is HermesArmFailure.COMMIT_MISMATCH


def test_frozen_runtime_must_share_policy_commit_with_authority_main(tmp_path: Path) -> None:
    # Given: a detached frozen runtime cloned from clean authority main.
    authority = tmp_path / "authority"
    authority.mkdir(mode=0o700)
    _git(authority, "init", "-b", "main")
    _git(authority, "config", "user.email", "test@example.invalid")
    _git(authority, "config", "user.name", "Test")
    (authority / "tracked.txt").write_text("first\n", encoding="utf-8")
    _git(authority, "add", "tracked.txt")
    _git(authority, "commit", "-m", "first")
    first = _git(authority, "rev-parse", "HEAD")
    frozen = tmp_path / "frozen"
    _ = subprocess.run(("git", "clone", str(authority), str(frozen)), check=True, capture_output=True, text=True)
    frozen.chmod(0o700)
    _git(frozen, "checkout", "--detach", first)

    # When / Then: matching detached HEAD passes, but an advanced authority main invalidates it.
    require_frozen_commit(frozen, first)
    (frozen / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(InvalidHermesArmRequestError) as dirty:
        require_frozen_commit(frozen, first)
    assert dirty.value.reason is HermesArmFailure.DIRTY_COMMIT
    (frozen / "untracked.txt").unlink()
    (authority / "tracked.txt").write_text("second\n", encoding="utf-8")
    _git(authority, "add", "tracked.txt")
    _git(authority, "commit", "-m", "second")
    second = _git(authority, "rev-parse", "HEAD")
    with pytest.raises(InvalidHermesArmRequestError) as mismatch:
        require_frozen_commit(frozen, second)
    assert mismatch.value.reason is HermesArmFailure.COMMIT_MISMATCH


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
