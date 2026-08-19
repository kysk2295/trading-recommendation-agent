from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import run_day_discovery_cycle as cli
from trading_agent.researcher_llm import FixtureLlmProposalClient, load_researcher_context_input

PROJECT = Path(__file__).resolve().parents[1]


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
        proposal_client=client, context_input=context,
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
    rejected_path.write_text(json.dumps(evidence, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    rejected_args = _args(tmp_path / "rejected")
    evidence_fixture = str(PROJECT / "tests/fixtures/day-research/discovery-evidence.json")
    rejected_args[rejected_args.index(evidence_fixture)] = str(rejected_path)
    assert cli.main(
        [*rejected_args, "--max-drafts", "1"], proposal_client=client, context_input=context
    ) == 0
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["terminal_reason"] == "semantic_duplicate"
    assert len(rejected["attempt_ids"]) == 1

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"market_id":"us_equities"}', encoding="utf-8")
    invalid_args = _args(tmp_path / "invalid")
    invalid_args[invalid_args.index(evidence_fixture)] = str(invalid)
    assert cli.main(invalid_args, proposal_client=client, context_input=context) != 0


def _args(tmp_path: Path) -> list[str]:
    return [
        "--market", "us_equities",
        "--evidence-view", str(PROJECT / "tests/fixtures/day-research/discovery-evidence.json"),
        "--calendar-snapshot", str(PROJECT / "tests/fixtures/day-research/calendar-snapshot.json"),
        "--experiment-ledger", str(tmp_path / "ledger.sqlite3"),
        "--generated-artifact-root", str(tmp_path / "artifacts"),
        "--receipt-root", str(tmp_path / "receipts"),
        "--context", str(PROJECT / "examples/research/researcher-context-v1.json"),
    ]
