from __future__ import annotations

import json
import os
import pwd
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.researcher_llm_contracts import LlmHypothesisDraft, ResearcherLlmError

_MAX_RESPONSE_BYTES = 256 * 1024
_PROVIDER_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_CLAUDE_MAX_BUDGET_USD: Final = "0.05"

type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class HermesCliProposalClient:
    executable: Path
    model_id: str
    provider_id: str
    seed: int | None = None
    temperature: float = 0.2
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if _PROVIDER_ID.fullmatch(self.provider_id) is None:
            raise ResearcherLlmError

    def complete(self, prompt: str) -> bytes:
        try:
            executable = self.executable.resolve(strict=True)
            metadata = executable.stat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or not os.access(executable, os.X_OK)
            ):
                raise ResearcherLlmError
            if self.provider_id == "claude-code":
                return _complete_with_claude(
                    executable,
                    self.model_id,
                    prompt,
                    timeout_seconds=self.timeout_seconds,
                )
            completed = subprocess.run(
                (
                    str(executable),
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--provider",
                    self.provider_id,
                    "-m",
                    self.model_id,
                    "-t",
                    "",
                    "-z",
                    prompt,
                ),
                check=False,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
            if completed.returncode != 0 or not completed.stdout or len(completed.stdout) > _MAX_RESPONSE_BYTES:
                raise ResearcherLlmError
            return completed.stdout
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise ResearcherLlmError from error


def _complete_with_claude(
    executable: Path,
    model_id: str,
    prompt: str,
    *,
    timeout_seconds: float,
) -> bytes:
    response_enveloped = _uses_response_envelope(prompt)
    schema = json.dumps(
        _claude_response_schema(prompt),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    completed = subprocess.run(
        (
            str(executable),
            "-p",
            "--safe-mode",
            "--disable-slash-commands",
            "--tools",
            "",
            "--no-session-persistence",
            "--model",
            model_id,
            "--max-budget-usd",
            _CLAUDE_MAX_BUDGET_USD,
            "--json-schema",
            schema,
            "--output-format",
            "json",
            prompt,
        ),
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout_seconds,
        env=_claude_environment(executable),
    )
    if completed.returncode != 0 or not completed.stdout or len(completed.stdout) > _MAX_RESPONSE_BYTES:
        raise ResearcherLlmError
    try:
        wrapper = json.loads(completed.stdout)
        if not isinstance(wrapper, dict) or wrapper.get("is_error") is not False:
            raise ResearcherLlmError
        structured = wrapper["structured_output"]
        if not isinstance(structured, dict):
            raise ResearcherLlmError
        if response_enveloped:
            structured = structured.get("response")
            if not isinstance(structured, dict):
                raise ResearcherLlmError
        return json.dumps(
            structured,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (KeyError, TypeError, ValueError) as error:
        raise ResearcherLlmError from error


def _claude_response_schema(prompt: str) -> dict[str, JsonValue]:
    try:
        payload = json.loads(prompt)
    except (TypeError, ValueError):
        return LlmHypothesisDraft.model_json_schema()
    if isinstance(payload, dict) and isinstance(payload.get("response_schema"), dict):
        raw_schema = payload["response_schema"]
        discriminator = raw_schema.get("discriminator")
        discriminator_property = discriminator.get("propertyName") if isinstance(discriminator, dict) else None
        schema = {str(key): value for key, value in raw_schema.items() if key != "discriminator"}
        if isinstance(discriminator_property, str):
            return _claude_discriminated_object_schema(schema, discriminator_property)
        return schema
    return LlmHypothesisDraft.model_json_schema()


def _claude_discriminated_object_schema(
    schema: dict[str, JsonValue],
    discriminator_property: str,
) -> dict[str, JsonValue]:
    del discriminator_property
    variants = schema.get("oneOf")
    definitions = schema.get("$defs")
    if not isinstance(variants, list) or not isinstance(definitions, dict):
        return schema
    preserved_variants: list[JsonValue] = []
    for item in variants:
        if not isinstance(item, dict):
            return schema
        reference = item.get("$ref")
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            return schema
        variant = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(variant, dict):
            return schema
        preserved_variants.append(item)
    if not preserved_variants:
        return schema
    response_schema: dict[str, JsonValue] = {"oneOf": preserved_variants}
    properties: dict[str, JsonValue] = {"response": response_schema}
    return {
        "$defs": definitions,
        "additionalProperties": False,
        "properties": properties,
        "required": ["response"],
        "type": "object",
    }


def _uses_response_envelope(prompt: str) -> bool:
    try:
        payload = json.loads(prompt)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    response_schema = payload.get("response_schema")
    if not isinstance(response_schema, dict):
        return False
    discriminator = response_schema.get("discriminator")
    return (
        isinstance(discriminator, dict)
        and isinstance(discriminator.get("propertyName"), str)
        and isinstance(response_schema.get("oneOf"), list)
        and isinstance(response_schema.get("$defs"), dict)
    )


def _claude_environment(executable: Path) -> dict[str, str]:
    account = pwd.getpwuid(os.geteuid())
    return {
        "HOME": account.pw_dir,
        "LOGNAME": account.pw_name,
        "PATH": f"{executable.parent}{os.pathsep}{os.defpath}",
        "SHELL": account.pw_shell,
        "TMPDIR": tempfile.gettempdir(),
        "USER": account.pw_name,
    }


__all__ = ("HermesCliProposalClient",)
