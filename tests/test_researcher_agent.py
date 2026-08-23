from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from trading_agent import researcher_llm
from trading_agent.critic_agent import (
    DeterministicHypothesisCritic,
    ObjectionKind,
)
from trading_agent.experiment_ledger_keys import research_source_key, strategy_lifecycle_event_key
from trading_agent.experiment_ledger_models import (
    HypothesisRegistration,
    ResearchHypothesisCard,
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    StrategyVersionRegistration,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.experiment_scope_models import ExperimentScope
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.research_hypothesis_registration import load_research_hypothesis_manifest
from trading_agent.researcher_agent import (
    CandidateStrategyDraft,
    FailureDigest,
    FixedHypothesisGenerator,
    LlmCallReceipt,
    ProposedHypothesis,
    ResearcherContext,
)
from trading_agent.researcher_llm import (
    FixtureLlmProposalClient,
    HermesCliProposalClient,
    LlmHypothesisDraft,
    ResearcherLlmError,
    StructuredHypothesisGenerator,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json"


def test_researcher_and_critic_modules_exist_as_upstream_only_layers() -> None:
    # Given: the deterministic ledger, queue, trial, and review core remains unchanged.
    expected = (
        PROJECT / "trading_agent" / "researcher_agent.py",
        PROJECT / "trading_agent" / "critic_agent.py",
    )

    # When: the new generation layer is inspected.
    existing = tuple(path.is_file() for path in expected)

    # Then: Researcher and Critic live in separate upstream modules.
    assert existing == (True, True)


def test_fixed_generator_returns_the_exact_prebuilt_proposal() -> None:
    # Given: a deterministic proposal and a context whose content cannot affect it.
    proposal = _proposal("return bars[index]")
    generator = FixedHypothesisGenerator(proposal)
    context = _context()

    # When: the fake generator proposes a hypothesis.
    generated = generator.propose(context)

    # Then: the exact immutable fixture is returned.
    assert generated is proposal


def test_deterministic_critic_allows_unrestricted_python_source() -> None:
    # Given: candidate code whose ordinary Python is isolated by the streaming runtime boundary.
    proposal = _proposal("return bars[index + 1]")
    critic = DeterministicHypothesisCritic(max_free_parameters=4)

    # When: hard checks inspect the candidate without an LLM.
    report = critic.critique(proposal, ExperimentLedgerReader(Path("missing.sqlite3")))

    # Then: the Critic leaves code capability enforcement to the sandbox protocol.
    assert report.is_blocked is False
    assert report.objections == ()


def test_deterministic_critic_blocks_too_many_free_parameters() -> None:
    # Given: a candidate whose free parameter count exceeds the registered ceiling.
    proposal = _proposal("return bars[index]", free_parameters=("a", "b", "c", "d", "e"))
    critic = DeterministicHypothesisCritic(max_free_parameters=4)

    # When: hard checks inspect the candidate.
    report = critic.critique(proposal, ExperimentLedgerReader(Path("missing.sqlite3")))

    # Then: parameter freedom is blocked without changing any ledger.
    assert report.is_blocked is True
    assert tuple(item.kind for item in report.objections) == (ObjectionKind.FREE_PARAMS,)


def test_deterministic_critic_blocks_rejected_text_in_the_same_lane_scope(tmp_path: Path) -> None:
    # Given: a rejected prior hypothesis and a newly identified duplicate in the same lane.
    rejected = _proposal("return bars[index]")
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_rejected_proposal(ledger, rejected)
    duplicate = _proposal(
        "return bars[index]",
        hypothesis_id="H-MOM-VWAP-DUPLICATE-003",
        registered_at=dt.datetime(2026, 7, 24, 2, 31, tzinfo=dt.UTC),
    )

    # When: deterministic redundancy checks compare immutable history.
    report = DeterministicHypothesisCritic(max_free_parameters=4).critique(duplicate, ledger)

    # Then: a new identifier cannot bypass a prior rejection.
    assert report.is_blocked is True
    assert tuple(item.kind for item in report.objections) == (ObjectionKind.REDUNDANCY,)


def test_invalid_structured_response_is_receipted_before_parse_failure(tmp_path: Path) -> None:
    # Given: a completed model call whose bytes do not satisfy the response schema.
    generator = StructuredHypothesisGenerator(
        FixtureLlmProposalClient(b"not-json"),
        ResearcherReceiptStore(tmp_path),
        lambda: dt.datetime(2026, 7, 23, 2, 31, tzinfo=dt.UTC),
    )

    # When: the structured generator parses the completed call.
    with pytest.raises(ResearcherLlmError):
        _ = generator.propose(_context())

    # Then: raw prompt, response, and immutable call metadata remain auditable.
    assert len(tuple((tmp_path / "prompts").glob("*.txt"))) == 1
    assert len(tuple((tmp_path / "responses").glob("*.txt"))) == 1
    assert len(tuple((tmp_path / "calls").glob("*.json"))) == 1


def test_hermes_client_executes_owned_local_adapter_and_returns_stdout(tmp_path: Path) -> None:
    # Given: an owned executable standing in for the installed Hermes entrypoint.
    executable = tmp_path / "hermes-fixture"
    executable.write_text("#!/bin/sh\nprintf '{\"schema_version\":1}'\n", encoding="utf-8")
    executable.chmod(0o700)
    client = HermesCliProposalClient(executable, "hermes-fixture-v1", "openai-codex")

    # When: the actual subprocess adapter completes a prompt.
    response = client.complete("{}")

    # Then: only the captured model stdout crosses into the structured generator.
    assert response == b'{"schema_version":1}'


def test_hermes_client_binds_the_receipted_model_to_the_invocation(tmp_path: Path) -> None:
    # Given: an executable that accepts only the model recorded by the client.
    executable = tmp_path / "hermes-model-fixture"
    executable.write_text(
        "#!/bin/sh\n"
        "[ \"$3\" = \"--provider\" ] && [ \"$4\" = \"openai-codex\" ] || exit 41\n"
        "[ \"$5\" = \"-m\" ] && [ \"$6\" = \"research/model-v1\" ] || exit 42\n"
        "printf '{\"schema_version\":1}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    client = HermesCliProposalClient(executable, "research/model-v1", "openai-codex")

    # When: one structured completion is requested.
    response = client.complete("{}")

    # Then: the invocation succeeds only when its model binding is exact.
    assert response == b'{"schema_version":1}'


def test_hermes_client_uses_claude_cli_without_provider_fallback_when_provider_is_claude_code(
    tmp_path: Path,
) -> None:
    # Given: the pinned Claude executable used by the production claude-code binding.
    executable = tmp_path / "claude-fixture"
    executable.write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = \"-p\" ] || exit 41\n"
        "previous=''\n"
        "for argument in \"$@\"; do\n"
        "  [ \"$argument\" = \"--provider\" ] && exit 42\n"
        "  if [ \"$previous\" = \"--model\" ]; then [ \"$argument\" = \"haiku\" ] || exit 43; fi\n"
        "  previous=\"$argument\"\n"
        "done\n"
        "printf '{\"is_error\":false,\"structured_output\":{\"schema_version\":1}}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    client = HermesCliProposalClient(executable, "haiku", "claude-code")

    # When: one structured completion is requested through the Claude provider binding.
    response = client.complete("{}")

    # Then: the direct Claude result is returned without entering Hermes provider fallback routing.
    assert response == b'{"schema_version":1}'


def test_researcher_prompt_carries_the_machine_output_schema() -> None:
    # Given: the typed response boundary used to parse a model completion.
    expected_schema = LlmHypothesisDraft.model_json_schema()

    # When: a Researcher prompt is serialized for Hermes.
    payload = json.loads(researcher_llm._prompt(_context()))

    # Then: the same machine schema is bound into the invocation contract.
    assert payload["contract"]["output_json_schema"] == expected_schema


def _context() -> ResearcherContext:
    manifest = load_research_hypothesis_manifest(EXAMPLE)
    return ResearcherContext(
        lane_id=manifest.experiment_scope.primary_lane,
        sources=manifest.research_sources,
        failure_digest=FailureDigest((), (), ()),
        regime_context="regular_session_high_liquidity",
        existing_hypothesis_texts=(),
    )


def _proposal(
    source_code: str,
    *,
    free_parameters: tuple[str, ...] = ("minimum_relative_volume",),
    hypothesis_id: str | None = None,
    registered_at: dt.datetime | None = None,
) -> ProposedHypothesis:
    manifest = load_research_hypothesis_manifest(EXAMPLE)
    scope = ExperimentScope.model_validate(
        manifest.experiment_scope.model_dump(mode="python")
        | {
            "hypothesis_id": hypothesis_id or manifest.experiment_scope.hypothesis_id,
            "registered_at": registered_at or manifest.experiment_scope.registered_at,
        }
    )
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
            sorted(str(research_source_key(source)) for source in manifest.research_sources)
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
        strategy_draft=CandidateStrategyDraft(
            source_code=source_code,
            free_parameters=free_parameters,
        ),
    )


def _register_rejected_proposal(
    ledger: ExperimentLedgerStore,
    proposal: ProposedHypothesis,
) -> None:
    hypothesis = proposal.card.hypothesis
    version = StrategyVersionRegistration(
        strategy_id="vwap_reclaim",
        strategy_version="rejected-vwap-reclaim-v1",
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_scope_key=hypothesis.experiment_scope_key,
        lane_id=hypothesis.primary_lane,
        code_version="a" * 40,
        parameter_set=("minimum_relative_volume:1.5",),
        data_contract=("completed_minute_bars_v1",),
        cost_model=("round_trip_bps:40",),
        portfolio_policy=("equal_risk",),
        source_registered_at=hypothesis.source_registered_at,
        ledger_recorded_at=hypothesis.ledger_recorded_at,
    )
    registration = StrategyLifecycleEvent(
        strategy_version=version.strategy_version,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.IDEA,
        policy_version="strategy_lifecycle_v1",
        decision_session_date=dt.date(2026, 7, 23),
        effective_session_date=dt.date(2026, 7, 24),
        decided_at=dt.datetime(2026, 7, 23, 14, tzinfo=dt.UTC),
        evidence_keys=("e" * 64,),
        reason_codes=("new_strategy_registration",),
        previous_event_key=None,
    )
    rejected = StrategyLifecycleEvent(
        strategy_version=version.strategy_version,
        sequence=2,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=StrategyLifecycleState.IDEA,
        to_state=StrategyLifecycleState.REJECTED,
        policy_version=registration.policy_version,
        decision_session_date=dt.date(2026, 7, 24),
        effective_session_date=dt.date(2026, 7, 27),
        decided_at=dt.datetime(2026, 7, 24, 14, tzinfo=dt.UTC),
        evidence_keys=("f" * 64,),
        reason_codes=("review_evidence_verified",),
        previous_event_key=str(strategy_lifecycle_event_key(registration)),
    )
    with ledger.writer() as writer:
        for source in proposal.cited_sources:
            assert writer.register_research_source(source)
        assert writer.register_research_hypothesis(proposal.card)
        assert writer.register_strategy_version(version)
        assert writer.append_lifecycle_event(registration)
        assert writer.append_lifecycle_event(rejected)
