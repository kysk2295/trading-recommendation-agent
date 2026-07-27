from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from trading_agent.dashboard_agent_family import AGENT_FAMILY_REGISTRY
from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1


def autonomous_prompt(
    trigger: AutonomousTriggerV1,
    source_evidence: str | None = None,
) -> str:
    role = next(item.role for item in AGENT_FAMILY_REGISTRY if item.family_id == trigger.agent_family_id)
    return (
        f"Role: {role}. Family identity: {trigger.agent_family_id}. "
        f"Memory namespace: research-family:{trigger.agent_family_id}:memory-v1. "
        "This is a separate autonomous task session; never resume an interactive session. "
        "Read only source-bound evidence, write candidate evidence only in the declared experiment root, "
        "and do not mutate providers, Paper state, lifecycle authority, deployment, or the integration worktree. "
        f"Trigger type: {trigger.trigger_type}. Evidence refs: {','.join(trigger.evidence_refs)}. "
        f"Source evidence: {source_evidence or 'unavailable'}."
    )


def isolated_worktree_clean(worktree: Path) -> bool:
    checked = subprocess.run(
        ("git", "-C", str(worktree), "status", "--porcelain=v1", "--untracked-files=all"),
        check=False,
        capture_output=True,
        timeout=30,
    )
    return checked.returncode == 0 and not checked.stdout


def experiment_hashes(experiment: Path) -> tuple[str, ...]:
    return tuple(
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(experiment.rglob("*"))
        if path.is_file() and not path.is_symlink()
    )


def cleanup_isolated_worktree(
    repository: Path,
    task_root: Path,
    worktree: Path,
    worktree_added: bool,
) -> bool:
    if worktree_added:
        removed = subprocess.run(
            ("git", "-C", str(repository), "worktree", "remove", "--force", str(worktree)),
            check=False,
            capture_output=True,
            timeout=60,
        )
        if removed.returncode != 0:
            return False
    if task_root.exists():
        shutil.rmtree(task_root)
    return not task_root.exists()


__all__ = (
    "autonomous_prompt",
    "cleanup_isolated_worktree",
    "experiment_hashes",
    "isolated_worktree_clean",
)
