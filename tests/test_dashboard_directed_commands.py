from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_dashboard_commands import _message
from tests.test_dashboard_directed_jobs import _write_experiment_package
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_commands import execute_interaction
from trading_agent.dashboard_directed_research import AuthoritativeDirectedResearchBroker
from trading_agent.dashboard_directed_research_models import DirectedResearchKind
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader

PROJECT = Path(__file__).resolve().parents[1]
HYPOTHESIS_MANIFEST = PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json"


@pytest.mark.anyio
async def test_directed_first_then_resume_invokes_hermes_once_and_mutates_real_ledger(tmp_path: Path) -> None:
    fake = _write_directed_hermes(tmp_path)
    source = _write_directed_hypothesis_package(tmp_path)
    state = tmp_path / "state"
    argv_log = tmp_path / "directed-argv.jsonl"
    settings = {
        "hermes_executable": fake,
        "worktree": tmp_path,
        "state_root": state,
        "source_evidence_root": source,
        "timeout_seconds": 5,
        "environment": {"ARGV_LOG": str(argv_log)},
    }

    first = await execute_interaction(
        _message(mode="hypothesis", family_id="opportunity_manager").interaction,
        **settings,
    )
    second = await execute_interaction(
        _message(
            interaction_id="019c0014-f0f5-7000-8000-000000000004",
            mode="analysis",
            family_id="opportunity_manager",
        ).interaction,
        **settings,
    )

    invocations = [json.loads(line) for line in argv_log.read_text().splitlines()]
    resume_index = invocations[1].index("--resume")
    reader = ExperimentLedgerReader(
        state / "directed-jobs" / "authoritative" / "opportunity_manager" / "experiment.sqlite3"
    )
    assert len(invocations) == 2
    assert "--resume" not in invocations[0]
    assert invocations[1][resume_index : resume_index + 2] == ["--resume", "session-directed-plan-001"]
    assert len(reader.research_hypothesis_cards()) == 1
    assert first.result.state == "completed"
    assert second.result.state == "completed"
    assert first.process_started and second.process_started


@pytest.mark.anyio
@pytest.mark.parametrize(
    "plan_text",
    [
        "perform hypothesis registration",
        '{"schema_version":1,"operation":"analysis","intent":"mismatch"}',
        '{"schema_version":1,"operation":"hypothesis","intent":"unsafe","argv":["paper-order"],"path":"/tmp/provider"}',
    ],
)
async def test_invalid_directed_plan_launches_no_research_tool(tmp_path: Path, plan_text: str) -> None:
    fake = _write_fixed_plan_hermes(tmp_path, plan_text)
    source = _write_directed_hypothesis_package(tmp_path)
    state = tmp_path / "state"
    count = tmp_path / "count"

    outcome = await execute_interaction(
        _message(mode="hypothesis", family_id="opportunity_manager").interaction,
        hermes_executable=fake,
        worktree=tmp_path,
        state_root=state,
        source_evidence_root=source,
        timeout_seconds=5,
        environment={"COUNT_PATH": str(count)},
    )

    assert count.read_text().splitlines() == ["1"]
    assert outcome.result.state == "failed"
    assert outcome.process_started
    assert not (state / "directed-jobs").exists()


@pytest.mark.anyio
async def test_valid_directed_experiment_plan_completes_real_terminal_trial(tmp_path: Path) -> None:
    fake = _write_directed_hermes(tmp_path)
    source = _write_experiment_package(tmp_path)
    state = tmp_path / "state"

    outcome = await execute_interaction(
        _message(mode="experiment", family_id="systematic_quant").interaction,
        hermes_executable=fake,
        worktree=tmp_path,
        state_root=state,
        source_evidence_root=source,
        timeout_seconds=10,
        environment={"ARGV_LOG": str(tmp_path / "experiment-argv.jsonl")},
    )

    reader = ExperimentLedgerReader(
        state / "directed-jobs" / "authoritative" / "systematic_quant" / "experiment.sqlite3"
    )
    trial = reader.trials()[0].registration
    terminal = reader.trial_events(trial.trial_id)[-1].event
    assert terminal.event_kind is TrialEventKind.COMPLETED
    assert outcome.result.state == "completed"
    assert len((tmp_path / "experiment-argv.jsonl").read_text().splitlines()) == 1


@pytest.mark.anyio
async def test_post_effect_directed_crash_closes_claim_uncertain_without_relaunch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _write_directed_hermes(tmp_path)
    source = _write_directed_hypothesis_package(tmp_path)
    state = tmp_path / "state"
    argv_log = tmp_path / "uncertain-argv.jsonl"
    original = AuthoritativeDirectedResearchBroker.execute
    calls = 0

    def write_then_crash(
        broker: AuthoritativeDirectedResearchBroker,
        operation: DirectedResearchKind,
        family_id: AgentFamilyId,
    ) -> bytes:
        nonlocal calls
        calls += 1
        _ = original(broker, operation, family_id)
        raise OSError("terminal receipt lost")

    monkeypatch.setattr(AuthoritativeDirectedResearchBroker, "execute", write_then_crash)
    message = _message(mode="hypothesis", family_id="opportunity_manager")
    settings = {
        "hermes_executable": fake,
        "worktree": tmp_path,
        "state_root": state,
        "source_evidence_root": source,
        "timeout_seconds": 5,
        "environment": {"ARGV_LOG": str(argv_log)},
    }

    first = await execute_interaction(message.interaction, **settings)
    replay = await execute_interaction(message.interaction, **settings)

    reader = ExperimentLedgerReader(
        state / "directed-jobs" / "authoritative" / "opportunity_manager" / "experiment.sqlite3"
    )
    assert len(reader.research_hypothesis_cards()) == 1
    assert first.result.state == "uncertain"
    assert first.directed_events[-1].state == "uncertain"
    assert replay.result.state == "uncertain"
    assert calls == 1
    assert len(argv_log.read_text().splitlines()) == 1


def _write_directed_hypothesis_package(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    package = source / "directed-research"
    package.mkdir(parents=True)
    source.chmod(0o700)
    package.chmod(0o700)
    (package / "hypothesis.json").write_bytes(HYPOTHESIS_MANIFEST.read_bytes())
    (package / "hypothesis.json").chmod(0o600)
    return source


def _write_directed_hermes(tmp_path: Path) -> Path:
    fake = tmp_path / "fake-directed-hermes"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['ARGV_LOG'], 'a') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "prompt = sys.argv[-1]\n"
        "operations = ('research','analysis','hypothesis','experiment','allowed_code')\n"
        'operation = next(item for item in operations if f\'"operation":"{item}"\' in prompt)\n'
        "plan = json.dumps({'schema_version':1,'operation':operation,'intent':'bounded intent'})\n"
        "print(json.dumps({'event':'complete','text':plan,"
        "'session_id':'session-directed-plan-001','failed':False,'error':None}))\n"
    )
    fake.chmod(0o700)
    return fake


def _write_fixed_plan_hermes(tmp_path: Path, plan_text: str) -> Path:
    fake = tmp_path / "fake-fixed-plan-hermes"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "open(os.environ['COUNT_PATH'], 'a').write('1\\n')\n"
        f"plan = {plan_text!r}\n"
        "print(json.dumps({'event':'complete','text':plan,"
        "'session_id':'session-directed-plan-001','failed':False,'error':None}))\n"
    )
    fake.chmod(0o700)
    return fake
