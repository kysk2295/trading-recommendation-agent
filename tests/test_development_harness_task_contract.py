from __future__ import annotations

import pytest

from development_harness.task_contract import (
    MAX_ALLOWED_PATHS,
    MAX_COMMAND_LENGTH,
    MAX_COMMANDS_PER_LIST,
    MAX_PATH_LENGTH,
    GrokTaskContract,
)


def _valid_contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": "m4-replay-input",
        "base_commit": "a" * 40,
        "objective": "Add a replay-bound research input contract.",
        "allowed_paths": (
            "development_harness/task_contract.py",
            "tests/test_development_harness_task_contract.py",
        ),
        "required_commands": ("uv run pytest tests/test_development_harness_task_contract.py -q",),
        "manual_qa_commands": ("uv run python run_grok_task.py --help",),
        "expected_summary_fields": ("changed_files", "verification", "concerns"),
    }


def test_contract_accepts_relative_unique_allowed_paths() -> None:
    contract = GrokTaskContract.model_validate(_valid_contract())

    assert contract.allowed_paths == (
        "development_harness/task_contract.py",
        "tests/test_development_harness_task_contract.py",
    )
    assert contract.max_turns == 12


@pytest.mark.parametrize(
    "path",
    (
        "/tmp/outside.py",
        "../outside.py",
        ".hermes/state.json",
        ".omo/state.json",
        ".git/config",
        ".env",
        "secrets/api_key.txt",
        "a" * (MAX_PATH_LENGTH + 1),
    ),
)
def test_contract_rejects_unsafe_allowed_path(path: str) -> None:
    payload = _valid_contract()
    payload["allowed_paths"] = (path,)

    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(payload)


@pytest.mark.parametrize(
    "command",
    (
        "uv run pytest; curl https://example.invalid",
        "rm report.txt",
        "uv run basedpyright-malicious",
        "uv run pytest -p myplugin tests/test_x.py",
        "uv run ruff check --fix development_harness",
        "uv run ruff check --config evil.toml development_harness",
        "uv run ruff check --no-cache --fix development_harness",
        "uv run python evil.py",
        "uv run python trading_agent/module.py",
        "uv run python -c import os",
        "uv run python -c pass; curl x",
        "uv run pytest /tmp/tests/test_x.py -q",
        "uv run basedpyright",
        "uv run python -m compileall development_harness",
        "uv run python -m compileall -q development_harness",
        "uv run python -m compileall -q trading_agent",
        "uv run python run_alpaca_paper_entry_smoke.py --help",
        "uv run python run_kis_paper_scan.py --help",
        "uv run pytest tests/test_x.py --tb=long",
        "uv run ruff check credentials/secret.py",
    ),
)
def test_contract_rejects_unsafe_or_unbounded_commands(command: str) -> None:
    payload = _valid_contract()
    payload["required_commands"] = (command,)

    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(payload)


@pytest.mark.parametrize(
    "command",
    (
        "uv run pytest tests/test_development_harness_task_contract.py -q",
        "uv run ruff check development_harness tests",
        "uv run ruff check trading_agent",
        "uv run ruff check --no-cache development_harness",
        "uv run basedpyright development_harness run_grok_task.py",
        "uv run basedpyright trading_agent",
        "uv run python -c pass",
        "uv run python run_grok_task.py --help",
    ),
)
def test_contract_accepts_strictly_validated_commands(command: str) -> None:
    payload = _valid_contract()
    payload["required_commands"] = (command,)
    payload["manual_qa_commands"] = ("uv run python -c pass",) if command != "uv run python -c pass" else (
        "uv run python run_grok_task.py --help",
    )

    contract = GrokTaskContract.model_validate(payload)

    assert command in contract.required_commands


def test_contract_rejects_duplicate_paths_and_shell_syntax() -> None:
    duplicate = _valid_contract()
    duplicate["allowed_paths"] = (
        "development_harness/task_contract.py",
        "development_harness/task_contract.py",
    )

    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(duplicate)


def test_contract_rejects_unknown_fields_and_malformed_base_commit() -> None:
    unknown = _valid_contract()
    unknown["unbounded"] = "no"
    malformed = _valid_contract()
    malformed["base_commit"] = "HEAD"

    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(unknown)
    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(malformed)


def test_contract_rejects_oversized_path_and_command_collections() -> None:
    too_many_paths = _valid_contract()
    too_many_paths["allowed_paths"] = tuple(f"path_{index}.py" for index in range(MAX_ALLOWED_PATHS + 1))
    too_many_commands = _valid_contract()
    too_many_commands["required_commands"] = tuple(
        f"uv run pytest tests/test_{index}.py -q" for index in range(MAX_COMMANDS_PER_LIST + 1)
    )
    long_command = _valid_contract()
    long_command["manual_qa_commands"] = ("uv run python " + ("x" * MAX_COMMAND_LENGTH),)

    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(too_many_paths)
    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(too_many_commands)
    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(long_command)


def test_contract_validation_error_does_not_echo_untrusted_input() -> None:
    with pytest.raises(ValueError) as error:
        _ = GrokTaskContract.model_validate({"unsafe": "secret-like-value"})

    assert str(error.value) == "invalid Grok task contract"


def test_contract_model_copy_revalidates_updated_values() -> None:
    contract = GrokTaskContract.model_validate(_valid_contract())

    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = contract.model_copy(update={"allowed_paths": (".hermes/notes.txt",)})
    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = contract.model_copy(update={"allowed_paths": (".omo/notes.txt",)})
