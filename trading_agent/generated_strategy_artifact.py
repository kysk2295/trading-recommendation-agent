from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.experiment_ledger_keys import (
    canonical_experiment_ledger_json,
    research_hypothesis_card_key,
)
from trading_agent.generated_strategy_runtime import GeneratedStrategyRuntimeIdentity
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.researcher_agent import ProposedHypothesis


class GeneratedStrategyArtifactError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"generated strategy artifact invalid: {self.reason}"


class GeneratedStrategyArtifactPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypothesis_id: str
    card_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    research_source_keys: tuple[str, ...]
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str
    free_parameters: tuple[str, ...]
    runtime: GeneratedStrategyRuntimeIdentity
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if (
            not self.hypothesis_id
            or not self.model_id
            or not self.research_source_keys
            or self.research_source_keys != tuple(sorted(set(self.research_source_keys)))
            or any(
                len(key) != 64
                or any(character not in "0123456789abcdef" for character in key)
                for key in self.research_source_keys
            )
            or self.free_parameters != tuple(sorted(set(self.free_parameters)))
            or len(self.free_parameters) > 4
            or any(not parameter for parameter in self.free_parameters)
        ):
            raise GeneratedStrategyArtifactError("payload_fields_invalid")
        return self


class GeneratedStrategyArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: GeneratedStrategyArtifactPayload

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = hashlib.sha256(
            canonical_experiment_ledger_json(self.payload).encode()
        ).hexdigest()
        if self.artifact_id != expected:
            raise GeneratedStrategyArtifactError("artifact_identity_invalid")
        return self


@dataclass(frozen=True, slots=True)
class PublishedGeneratedStrategy:
    artifact: GeneratedStrategyArtifact
    source_path: Path
    manifest_path: Path
    created: bool


@dataclass(frozen=True, slots=True)
class GeneratedStrategyArtifactStore:
    root: Path
    runtime: GeneratedStrategyRuntimeIdentity

    def publish(self, proposal: ProposedHypothesis) -> PublishedGeneratedStrategy:
        source = proposal.strategy_draft.source_code
        try:
            payload = GeneratedStrategyArtifactPayload(
                source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                hypothesis_id=proposal.card.hypothesis.hypothesis_id,
                card_key=str(research_hypothesis_card_key(proposal.card)),
                research_source_keys=proposal.card.research_source_keys,
                prompt_sha256=proposal.llm_receipt.prompt_sha256,
                response_sha256=proposal.llm_receipt.response_sha256,
                model_id=proposal.llm_receipt.model_id,
                free_parameters=proposal.strategy_draft.free_parameters,
                runtime=self.runtime,
                created_at=proposal.llm_receipt.called_at,
            )
            artifact = GeneratedStrategyArtifact(
                artifact_id=hashlib.sha256(
                    canonical_experiment_ledger_json(payload).encode()
                ).hexdigest(),
                payload=payload,
            )
            directory = self.root / artifact.artifact_id
            source_path = directory / "strategy.py"
            manifest_path = directory / "manifest.json"
            source_created = publish_private_immutable_text(source_path, source)
            manifest_created = publish_private_immutable_text(
                manifest_path,
                canonical_experiment_ledger_json(artifact) + "\n",
            )
            return PublishedGeneratedStrategy(
                artifact=artifact,
                source_path=source_path,
                manifest_path=manifest_path,
                created=source_created or manifest_created,
            )
        except GeneratedStrategyArtifactError:
            raise
        except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
            raise GeneratedStrategyArtifactError("publication_failed") from None

    def load(self, artifact_id: str) -> GeneratedStrategyArtifact:
        try:
            if len(artifact_id) != 64 or any(
                character not in "0123456789abcdef" for character in artifact_id
            ):
                raise GeneratedStrategyArtifactError("artifact_id_invalid")
            directory = self.root / artifact_id
            manifest = read_private_text(directory / "manifest.json")
            source = read_private_text(directory / "strategy.py")
            artifact = GeneratedStrategyArtifact.model_validate_json(manifest)
            if (
                artifact.artifact_id != artifact_id
                or artifact.payload.runtime != self.runtime
                or manifest != canonical_experiment_ledger_json(artifact) + "\n"
                or hashlib.sha256(source.encode()).hexdigest()
                != artifact.payload.source_sha256
            ):
                raise GeneratedStrategyArtifactError("artifact_content_invalid")
            return artifact
        except GeneratedStrategyArtifactError:
            raise
        except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
            raise GeneratedStrategyArtifactError("load_failed") from None


__all__ = (
    "GeneratedStrategyArtifact",
    "GeneratedStrategyArtifactError",
    "GeneratedStrategyArtifactPayload",
    "GeneratedStrategyArtifactStore",
    "PublishedGeneratedStrategy",
)
