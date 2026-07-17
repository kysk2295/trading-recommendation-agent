from __future__ import annotations

import pytest

from development_harness.task_contract import GrokTaskContract


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
        ".git/config",
        ".env",
        "secrets/api_key.txt",
    ),
)
def test_contract_rejects_unsafe_allowed_path(path: str) -> None:
    payload = _valid_contract()
    payload["allowed_paths"] = (path,)

    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(payload)


def test_contract_rejects_duplicate_paths_and_shell_syntax() -> None:
    duplicate = _valid_contract()
    duplicate["allowed_paths"] = (
        "development_harness/task_contract.py",
        "development_harness/task_contract.py",
    )
    shell = _valid_contract()
    shell["required_commands"] = ("uv run pytest; curl https://example.invalid",)
    destructive = _valid_contract()
    destructive["manual_qa_commands"] = ("rm report.txt",)
    lookalike = _valid_contract()
    lookalike["required_commands"] = ("uv run basedpyright-malicious",)

    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(duplicate)
    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(shell)
    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(destructive)
    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(lookalike)


def test_contract_rejects_unknown_fields_and_malformed_base_commit() -> None:
    unknown = _valid_contract()
    unknown["unbounded"] = "no"
    malformed = _valid_contract()
    malformed["base_commit"] = "HEAD"

    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(unknown)
    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = GrokTaskContract.model_validate(malformed)


def test_contract_validation_error_does_not_echo_untrusted_input() -> None:
    with pytest.raises(ValueError) as error:
        _ = GrokTaskContract.model_validate({"unsafe": "secret-like-value"})

    assert str(error.value) == "invalid Grok task contract"


def test_contract_model_copy_revalidates_updated_values() -> None:
    contract = GrokTaskContract.model_validate(_valid_contract())

    with pytest.raises(ValueError, match="invalid Grok task contract"):
        _ = contract.model_copy(update={"allowed_paths": (".hermes/notes.txt",)})
