from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import run_day_discovery_cycle as cli
from trading_agent.day_discovery_loop import DayDiscoveryEvidenceView
from trading_agent.research_agent_source_common import canonical_model_json
from trading_agent.researcher_llm import FixtureLlmProposalClient, load_researcher_context_input

PROJECT = Path(__file__).resolve().parents[1]
CLI_NOW = dt.datetime(2026, 8, 20, 14, 0, tzinfo=dt.UTC)


def test_cli_help_exposes_only_bounded_research_inputs() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT / "run_day_discovery_cycle.py"), "--help"],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0
    for option in (
        "--market", "--evidence-view", "--calendar-snapshot", "--experiment-ledger",
        "--generated-artifact-root", "--receipt-root", "--config", "--context", "--max-drafts",
    ):
        assert option in result.stdout
    for forbidden in ("--credential", "--broker", "--risk", "--size", "--order", "--endpoint"):
        assert forbidden not in result.stdout


def test_cli_rejects_out_of_range_max_drafts(tmp_path: Path) -> None:
    assert cli.main([*_args(tmp_path), "--max-drafts", "4"]) != 0


def test_cli_accepted_and_fully_criticized_are_zero_but_invalid_input_is_nonzero(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    response = json.loads(
        (PROJECT / "examples/research/researcher-response-fixture-v1.json").read_text(encoding="utf-8")
    )
    response["methodology_tags"] = ["novel_liquidity_echo", "online_state_machine"]
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps(response), encoding="utf-8")
    client = FixtureLlmProposalClient(response_path.read_bytes())
    context = load_researcher_context_input(PROJECT / "examples/research/researcher-context-v1.json")
    assert cli.main(
        [*_args(tmp_path / "accepted"), "--max-drafts", "1"],
        proposal_client=client, context_input=context, clock=lambda: CLI_NOW,
    ) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["capsule_id"] is not None
    assert set(accepted) == {
        "admission_id", "attempt_ids", "capsule_id", "cycle_id", "family_id",
        "hypothesis_version_id", "terminal_reason",
    }

    evidence = json.loads(
        (PROJECT / "tests/fixtures/day-research/discovery-evidence.json").read_text(encoding="utf-8")
    )
    from dataclasses import replace

    from tests.day_strategy_capsule_support import proposal as fixture_proposal
    from trading_agent.researcher_agent import CandidateStrategyDraft
    from trading_agent.researcher_llm import LlmHypothesisDraft
    draft = LlmHypothesisDraft.model_validate(response)
    candidate = fixture_proposal(draft.strategy_source)
    candidate = replace(
        candidate,
        strategy_draft=CandidateStrategyDraft(
            draft.strategy_source, draft.free_parameters, draft.methodology_tags
        ),
    )
    from trading_agent.day_discovery_loop import _proposal_semantic_hash
    evidence["existing_semantic_hashes"] = [_proposal_semantic_hash(candidate)]
    rejected_path = tmp_path / "rejected.json"
    rejected_path.write_text(
        canonical_model_json(DayDiscoveryEvidenceView.model_validate(evidence)),
        encoding="utf-8",
    )
    rejected_path.chmod(0o600)
    rejected_args = _args(tmp_path / "rejected")
    rejected_args[rejected_args.index("--evidence-view") + 1] = str(rejected_path)
    assert cli.main(
        [*rejected_args, "--max-drafts", "1"], proposal_client=client,
        context_input=context, clock=lambda: CLI_NOW,
    ) == 0
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["terminal_reason"] == "semantic_duplicate"
    assert len(rejected["attempt_ids"]) == 1

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"market_id":"us_equities"}', encoding="utf-8")
    invalid.chmod(0o600)
    invalid_args = _args(tmp_path / "invalid")
    invalid_args[invalid_args.index("--evidence-view") + 1] = str(invalid)
    assert cli.main(
        invalid_args, proposal_client=client, context_input=context, clock=lambda: CLI_NOW
    ) != 0


def test_cli_rejects_public_or_symlinked_private_inputs(tmp_path: Path) -> None:
    response = json.loads(
        (PROJECT / "examples/research/researcher-response-fixture-v1.json").read_text()
    )
    response["methodology_tags"] = ["novel_liquidity_echo", "online_state_machine"]
    client = FixtureLlmProposalClient(json.dumps(response).encode())
    context = load_researcher_context_input(PROJECT / "examples/research/researcher-context-v1.json")
    args = _args(tmp_path / "public")
    evidence = Path(args[args.index("--evidence-view") + 1])
    evidence.chmod(0o644)
    assert cli.main(
        args, proposal_client=client, context_input=context, clock=lambda: CLI_NOW
    ) != 0

    evidence.chmod(0o600)
    alias = evidence.with_name("evidence-alias.json")
    alias.symlink_to(evidence)
    args[args.index("--evidence-view") + 1] = str(alias)
    assert cli.main(
        args, proposal_client=client, context_input=context, clock=lambda: CLI_NOW
    ) != 0


def _args(tmp_path: Path) -> list[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_path.chmod(0o700)
    evidence = DayDiscoveryEvidenceView.model_validate_json(
        (PROJECT / "tests/fixtures/day-research/discovery-evidence.json").read_text(encoding="utf-8")
    )
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(canonical_model_json(evidence), encoding="utf-8")
    evidence_path.chmod(0o600)
    calendar_path = tmp_path / "calendar.json"
    calendar = json.loads(
        (PROJECT / "tests/fixtures/day-research/calendar-snapshot.json").read_text(encoding="utf-8")
    )
    calendar_path.write_text(json.dumps(calendar, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    calendar_path.chmod(0o600)
    context_path = tmp_path / "context.json"
    context = load_researcher_context_input(PROJECT / "examples/research/researcher-context-v1.json")
    context_path.write_text(canonical_model_json(context), encoding="utf-8")
    context_path.chmod(0o600)
    return [
        "--market", "us_equities",
        "--evidence-view", str(evidence_path),
        "--calendar-snapshot", str(calendar_path),
        "--experiment-ledger", str(tmp_path / "ledger.sqlite3"),
        "--generated-artifact-root", str(tmp_path / "artifacts"),
        "--receipt-root", str(tmp_path / "receipts"),
        "--context", str(context_path),
    ]
