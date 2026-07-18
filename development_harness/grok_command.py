"""Grok CLI command and worker prompt construction."""

from __future__ import annotations

from pathlib import Path

from development_harness.grok_verification import GrokVerificationError, cache_safe_command
from development_harness.grok_worker_report import WORKER_SUMMARY_JSON_SCHEMA
from development_harness.task_contract import GrokTaskContract


class GrokCommandError(RuntimeError):
    """Raised when a worker command or prompt cannot be built safely."""


def _bullet_block(values: tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values)


def worker_facing_commands(commands: tuple[str, ...]) -> tuple[str, ...]:
    """Commands shown to the worker, with Ruff ``--no-cache`` injected when needed."""

    try:
        return tuple(cache_safe_command(command) for command in commands)
    except GrokVerificationError as error:
        raise GrokCommandError(str(error)) from error


def build_worker_prompt(contract: GrokTaskContract) -> str:
    fields = ", ".join(contract.expected_summary_fields)
    required = worker_facing_commands(contract.required_commands)
    manual = worker_facing_commands(contract.manual_qa_commands)
    return (
        "Implement exactly this bounded development task.\n"
        f"Task ID: {contract.task_id}\n"
        f"Objective: {contract.objective}\n\n"
        f"Allowed paths:\n{_bullet_block(contract.allowed_paths)}\n\n"
        f"Required verification commands:\n{_bullet_block(required)}\n\n"
        f"Manual QA commands:\n{_bullet_block(manual)}\n\n"
        "Rules: use TDD; do not change paths outside the allow-list; do not read credentials, "
        "provider modules, broker modules, or user-owned .hermes/.omo state; do not make network, "
        "market-data, broker, Paper, or live-trading calls; do not commit, push, create a branch, "
        "create a worktree, or spawn a subagent. Work in-place on the current repository root only. "
        "You may edit allow-listed working-tree files but must not commit or push history. "
        "Run the required verification. Your final response must be JSON only and contain these keys: "
        f"{fields}.\n"
    )


def build_grok_command(
    contract: GrokTaskContract,
    *,
    grok_binary: str,
    repository: Path,
    prompt: str,
) -> tuple[str, ...]:
    return (
        grok_binary,
        "--cwd",
        str(repository),
        "--always-approve",
        "--permission-mode",
        "bypassPermissions",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--json-schema",
        WORKER_SUMMARY_JSON_SCHEMA,
        "--no-plan",
        "--no-subagents",
        "--disable-web-search",
        "--no-memory",
        "--max-turns",
        str(contract.max_turns),
    )
