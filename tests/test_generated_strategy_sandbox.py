from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_keys import research_source_key
from trading_agent.experiment_ledger_models import HypothesisRegistration, ResearchHypothesisCard
from trading_agent.experiment_scope_models import ExperimentScope
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactStore,
    PublishedGeneratedStrategy,
)
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import (
    GeneratedStrategyExecutionError,
    GeneratedStrategyLimits,
    GeneratedStrategySandbox,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.models import BarInput
from trading_agent.research_hypothesis_registration import load_research_hypothesis_manifest
from trading_agent.researcher_agent import CandidateStrategyDraft, LlmCallReceipt, ProposedHypothesis

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_EXAMPLE = PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json"


def test_real_sandbox_keeps_state_and_emits_only_after_two_bars(tmp_path: Path) -> None:
    # Given: unrestricted stateful Python published as a runtime-bound artifact.
    published = _publish(
        tmp_path,
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        count = 0\n"
        "        def observe(self, bar, candidate):\n"
        "            self.count += 1\n"
        "            if self.count < 2:\n"
        "                return None\n"
        "            return {'symbol': bar['symbol'], 'timestamp': bar['timestamp'], "
        "'entry': bar['close'], 'stop': bar['low'], 'rationale': 'stateful'}\n"
        "    return Strategy()\n",
    )
    sandbox = _sandbox(tmp_path, published)

    # When: the host streams two completed bars one at a time.
    with sandbox.open_session(published) as strategy:
        first = strategy.observe(_bar(31), None)
        second = strategy.observe(_bar(32), None)

    # Then: state survives within the fold and the validated host signal is causal.
    assert first is None
    assert second is not None
    assert second.timestamp == _bar(32).timestamp
    assert second.strategy == f"generated-python:{published.artifact.artifact_id}"


def test_real_sandbox_denies_network_and_outside_file_reads(tmp_path: Path) -> None:
    # Given: generated code that probes a socket and a private sentinel outside its roots.
    sentinel = tmp_path / "outside-sentinel.txt"
    sentinel.write_text("secret", encoding="utf-8")
    source = (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            import socket\n"
        "            from pathlib import Path\n"
        "            denied = []\n"
        "            probe = socket.socket()\n"
        "            try:\n"
        "                probe.bind(('127.0.0.1', 0))\n"
        "            except OSError:\n"
        "                denied.append('network')\n"
        "            finally:\n"
        "                probe.close()\n"
        "            try:\n"
        f"                Path({json.dumps(str(sentinel))}).read_text()\n"
        "            except OSError:\n"
        "                denied.append('file')\n"
        "            return {'symbol': bar['symbol'], 'timestamp': bar['timestamp'], "
        "'entry': bar['close'], 'stop': bar['low'], 'rationale': ','.join(denied)}\n"
        "    return Strategy()\n"
    )
    published = _publish(tmp_path, source)

    # When: the probe runs through the real macOS sandbox-exec boundary.
    with _sandbox(tmp_path, published).open_session(published) as strategy:
        signal = strategy.observe(_bar(31), None)

    # Then: both capabilities are denied while ordinary strategy computation continues.
    assert signal is not None
    assert signal.rationale == "network,file"


def test_real_sandbox_times_out_infinite_generated_code(tmp_path: Path) -> None:
    # Given: generated code that never returns from its bar observation.
    published = _publish(
        tmp_path,
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            while True:\n"
        "                pass\n"
        "    return Strategy()\n",
    )
    sandbox = _sandbox(
        tmp_path,
        published,
        GeneratedStrategyLimits(wall_seconds=0.3, cpu_seconds=1, rss_bytes=512 * 1024 * 1024),
    )

    # When/Then: the host kills the process group at the bounded deadline.
    with (
        sandbox.open_session(published) as strategy,
        pytest.raises(GeneratedStrategyExecutionError, match="frame_timeout"),
    ):
        _ = strategy.observe(_bar(31), None)


def test_real_sandbox_stops_generated_code_over_rss_limit(tmp_path: Path) -> None:
    # Given: generated code that continuously retains allocated memory.
    published = _publish(
        tmp_path,
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            blocks = []\n"
        "            while True:\n"
        "                blocks.append(bytearray(8 * 1024 * 1024))\n"
        "    return Strategy()\n",
    )
    sandbox = _sandbox(
        tmp_path,
        published,
        GeneratedStrategyLimits(wall_seconds=2.0, cpu_seconds=3, rss_bytes=256 * 1024 * 1024),
    )

    # When/Then: host RSS observation terminates the process group at the configured cap.
    with (
        sandbox.open_session(published) as strategy,
        pytest.raises(GeneratedStrategyExecutionError, match="rss_limit_exceeded"),
    ):
        _ = strategy.observe(_bar(31), None)


def test_sandbox_profile_is_deny_by_default_without_network_allow(tmp_path: Path) -> None:
    # Given: a valid generated artifact and isolated task root.
    published = _publish(tmp_path, _no_signal_source())
    sandbox = _sandbox(tmp_path, published)

    # When: the versioned profile is rendered.
    profile = sandbox.render_profile(published, tmp_path / "tasks")

    # Then: network is denied and only runtime, artifact, runner, and task paths are exposed.
    assert profile.startswith("(version 1)\n(deny default)")
    assert "(deny network*)" in profile
    assert "allow network" not in profile
    assert str(published.source_path) in profile
    assert str(tmp_path / "tasks") in profile
    assert ".config/trading-agent" not in profile


def _sandbox(
    tmp_path: Path,
    published: PublishedGeneratedStrategy,
    limits: GeneratedStrategyLimits | None = None,
) -> GeneratedStrategySandbox:
    return GeneratedStrategySandbox(
        published.artifact.payload.runtime,
        tmp_path / "tasks",
        limits or GeneratedStrategyLimits(),
    )


def _publish(tmp_path: Path, source: str) -> PublishedGeneratedStrategy:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    return GeneratedStrategyArtifactStore(tmp_path / "artifacts", runtime).publish(_proposal(source))


def _proposal(source: str) -> ProposedHypothesis:
    manifest = load_research_hypothesis_manifest(SOURCE_EXAMPLE)
    scope = ExperimentScope.model_validate(manifest.experiment_scope.model_dump(mode="python"))
    registration = HypothesisRegistration(
        hypothesis_id=scope.hypothesis_id,
        experiment_scope=scope,
        experiment_scope_key=experiment_scope_key(scope),
        primary_lane=scope.primary_lane,
        hypothesis=manifest.hypothesis,
        falsification_rule=manifest.falsification_rule,
        source_registered_at=scope.registered_at,
        ledger_recorded_at=scope.registered_at,
    )
    card = ResearchHypothesisCard(
        hypothesis=registration,
        research_source_keys=tuple(
            sorted(str(research_source_key(item)) for item in manifest.research_sources)
        ),
        economic_mechanism=manifest.economic_mechanism,
        counterfactual_baseline=manifest.counterfactual_baseline,
    )
    return ProposedHypothesis(
        card=card,
        cited_sources=manifest.research_sources,
        llm_receipt=LlmCallReceipt(
            model_id="fixture-researcher-v1",
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
            seed=7,
            temperature=0.0,
            called_at=dt.datetime(2026, 7, 23, 2, 31, tzinfo=dt.UTC),
        ),
        strategy_draft=CandidateStrategyDraft(source, ()),
    )


def _bar(minute: int) -> BarInput:
    return BarInput(
        "TEST",
        dt.datetime(2026, 7, 23, 13, minute, tzinfo=dt.UTC),
        10.0,
        11.0,
        9.5,
        10.5,
        100_000,
        9.8,
        1_000_000,
        20.0,
    )


def _no_signal_source() -> str:
    return (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            return None\n"
        "    return Strategy()\n"
    )
