from __future__ import annotations

import datetime as dt
import json
from contextlib import suppress
from pathlib import Path

import pytest

import trading_agent.research_agent_service_builder as service_builder_module
from tests.test_research_agent_service_cli import _config
from trading_agent.research_agent_source_common import canonical_model_json
from trading_agent.research_agent_systematic import SystematicResearchActionConfig
from trading_agent.researcher_llm import (
    LlmHypothesisDraft,
    ResearcherContextInput,
    ResearcherLlmError,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize("target", ("response", "context"))
@pytest.mark.parametrize("failure", ("pretty", "mode", "symlink"))
def test_configured_day_discovery_rejects_noncanonical_or_nonprivate_inputs_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    failure: str,
) -> None:
    config = _config(tmp_path)
    response = LlmHypothesisDraft.model_validate_json(
        (Path(__file__).parents[1] / "examples/research/researcher-response-fixture-v1.json").read_text()
    )
    context = ResearcherContextInput.model_validate_json(
        (Path(__file__).parents[1] / "examples/research/researcher-context-v1.json").read_text()
    )
    response_path = tmp_path / "systematic" / "response.json"
    context_path = tmp_path / "systematic" / "context.json"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(canonical_model_json(response), encoding="utf-8")
    context_path.write_text(canonical_model_json(context), encoding="utf-8")
    response_path.chmod(0o600)
    context_path.chmod(0o600)
    selected = response_path if target == "response" else context_path
    model = response if target == "response" else context
    if failure == "pretty":
        selected.write_text(json.dumps(model.model_dump(mode="json"), indent=2), encoding="utf-8")
    elif failure == "mode":
        selected.chmod(0o644)
    else:
        alias = selected.with_name(f"{selected.stem}-alias.json")
        alias.symlink_to(selected)
        selected = alias
    systematic = SystematicResearchActionConfig.model_validate(
        config.systematic.model_dump(mode="python")
        | {
            "context": selected if target == "context" else context_path,
            "response_fixture": selected if target == "response" else response_path,
            "hermes_executable": None,
        }
    )
    configured = config.model_copy(update={"systematic": systematic})
    runtime_calls: list[Path] = []
    real_resolve = service_builder_module.resolve_generated_strategy_runtime

    def recording_resolve(path: Path):
        runtime_calls.append(path)
        return real_resolve(path)

    monkeypatch.setattr(service_builder_module, "resolve_generated_strategy_runtime", recording_resolve)

    with suppress(ResearcherLlmError):
        service_builder_module._day_discovery_executor(configured, NOW)
    assert runtime_calls == []
