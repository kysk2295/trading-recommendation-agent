from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from development_harness.safe_command import (
    MAX_COMMAND_LENGTH,
    MAX_PATH_LENGTH,
    is_safe_command,
    is_safe_path,
)

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_TASK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_MODEL_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "base_commit",
        "objective",
        "allowed_paths",
        "required_commands",
        "manual_qa_commands",
        "expected_summary_fields",
        "max_turns",
    }
)
_REQUIRED_SUMMARY_FIELDS = ("changed_files", "verification", "concerns")
_REQUIRED_VERIFICATION_TOOLS: Final = frozenset({"pytest", "ruff", "basedpyright"})
_NOOP_MANUAL_QA: Final = frozenset({"uv run python -c pass"})

MAX_ALLOWED_PATHS: Final = 32
MAX_COMMANDS_PER_LIST: Final = 16

# Re-export bounds used by contract tests.
__all__ = (
    "MAX_ALLOWED_PATHS",
    "MAX_COMMANDS_PER_LIST",
    "MAX_COMMAND_LENGTH",
    "MAX_PATH_LENGTH",
    "GrokTaskContract",
    "InvalidGrokTaskContractError",
)


class _GrokTaskValidationError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid Grok task contract")


class InvalidGrokTaskContractError(ValueError):
    """A sanitized task-contract validation failure."""

    def __init__(self) -> None:
        super().__init__("invalid Grok task contract")


class GrokTaskContract(BaseModel):
    """The immutable, side-effect-bounded input to one development worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    task_id: Annotated[str, Field(min_length=3, max_length=80)]
    base_commit: str
    objective: Annotated[str, Field(min_length=1, max_length=800)]
    allowed_paths: tuple[str, ...]
    required_commands: tuple[str, ...]
    manual_qa_commands: tuple[str, ...]
    expected_summary_fields: tuple[str, ...]
    max_turns: Annotated[int, Field(ge=1, le=48)] = 12

    _summary_field_pattern: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError:
            raise InvalidGrokTaskContractError() from None

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: Any | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        try:
            return super().model_validate(
                obj,
                strict=strict,
                extra=extra,
                from_attributes=from_attributes,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except ValidationError:
            raise InvalidGrokTaskContractError() from None

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Any | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        try:
            return super().model_validate_json(
                json_data,
                strict=strict,
                extra=extra,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except ValidationError:
            raise InvalidGrokTaskContractError() from None

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_or_non_mapping_input(cls, value: Any) -> Any:
        if not isinstance(value, dict) or set(value).difference(_MODEL_FIELDS):
            raise _GrokTaskValidationError()
        return value

    @field_validator("task_id")
    @classmethod
    def _validate_task_id(cls, value: str) -> str:
        if not _TASK_ID_PATTERN.fullmatch(value):
            raise _GrokTaskValidationError()
        return value

    @field_validator("base_commit")
    @classmethod
    def _validate_base_commit(cls, value: str) -> str:
        if not _COMMIT_PATTERN.fullmatch(value):
            raise _GrokTaskValidationError()
        return value

    @field_validator("objective")
    @classmethod
    def _validate_objective(cls, value: str) -> str:
        if value != value.strip() or "\x00" in value:
            raise _GrokTaskValidationError()
        return value

    @field_validator("allowed_paths")
    @classmethod
    def _validate_allowed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            not value
            or len(value) > MAX_ALLOWED_PATHS
            or len(value) != len(set(value))
            or any(not is_safe_path(path) for path in value)
        ):
            raise _GrokTaskValidationError()
        return value

    @field_validator("required_commands", "manual_qa_commands")
    @classmethod
    def _validate_commands(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            not value
            or len(value) > MAX_COMMANDS_PER_LIST
            or len(value) != len(set(value))
            or any(not is_safe_command(command) for command in value)
        ):
            raise _GrokTaskValidationError()
        return value

    @field_validator("expected_summary_fields")
    @classmethod
    def _validate_expected_summary_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != _REQUIRED_SUMMARY_FIELDS:
            raise _GrokTaskValidationError()
        if any(not cls._summary_field_pattern.fullmatch(field) for field in value):
            raise _GrokTaskValidationError()
        return value

    @staticmethod
    def _command_tool(command: str) -> str | None:
        parts = command.split()
        if len(parts) < 3 or parts[0] != "uv" or parts[1] != "run":
            return None
        return parts[2]

    @staticmethod
    def _is_python_cli_help(command: str) -> bool:
        parts = command.split()
        return (
            len(parts) == 5
            and parts[:3] == ["uv", "run", "python"]
            and parts[3].endswith(".py")
            and parts[4] == "--help"
        )

    @model_validator(mode="after")
    def _reject_empty_values(self) -> GrokTaskContract:
        if not self.objective or not self.required_commands or not self.manual_qa_commands:
            raise _GrokTaskValidationError()
        tools = {
            tool
            for command in self.required_commands
            if (tool := self._command_tool(command)) is not None
        }
        if not _REQUIRED_VERIFICATION_TOOLS.issubset(tools):
            raise _GrokTaskValidationError()
        if any(command in _NOOP_MANUAL_QA for command in self.manual_qa_commands):
            raise _GrokTaskValidationError()
        if not any(self._is_python_cli_help(command) for command in self.manual_qa_commands):
            # Task-specific manual QA must include a real CLI help probe, not only
            # automated lint/type/test commands already covered by required_commands.
            raise _GrokTaskValidationError()
        return self

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Keep Pydantic's convenience API from bypassing contract validation."""

        _ = deep
        payload = self.model_dump(mode="python")
        if update is not None:
            payload.update(update)
        return type(self).model_validate(payload)
