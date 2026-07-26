from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.test_dashboard_directed_jobs import _write_experiment_package
from trading_agent.dashboard_commands import (
    DashboardInteractionMessage,
    execute_interaction,
    parse_dashboard_event,
)
from trading_agent.dashboard_directed_research import AuthoritativeDirectedResearchBroker
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader

PROJECT = Path(__file__).resolve().parents[1]
HYPOTHESIS_MANIFEST = PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json"


def _message(
    *,
    interaction_id: str = "019c0014-f0f5-7000-8000-000000000001",
    family_id: str = "market_context",
    mode: str = "conversation",
) -> DashboardInteractionMessage:
    return DashboardInteractionMessage.model_validate(
        {
            "type": "interaction",
            "interaction": {
                "id": interaction_id,
                "agent_id": family_id,
                "mode": mode,
                "command": "현재 실제 데이터 결손을 한 문장으로 설명해줘",
                "state": "queued",
                "response": None,
                "created_at": "2026-07-26T04:00:00Z",
                "updated_at": "2026-07-26T04:00:00Z",
            },
        }
    )


def test_dashboard_command_parser_rejects_unknown_agents() -> None:
    raw = _message().model_dump_json().replace('"market_context"', '"delivery"')

    with pytest.raises(ValidationError):
        parse_dashboard_event(raw)


@pytest.mark.anyio
async def test_first_then_resume_uses_exact_session_argv_across_restart(
    tmp_path: Path,
) -> None:
    # Given: a fake Hermes that records argv and emits strict terminal NDJSON
    fake = tmp_path / "fake-hermes"
    argv_log = tmp_path / "argv.jsonl"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['ARGV_LOG'], 'a') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print(json.dumps({'event':'complete','text':'bounded response',"
        "'session_id':'session-market-context-001','failed':False,'error':None}))\n"
    )
    fake.chmod(0o700)
    state = tmp_path / "state"

    # When: two explicit messages execute with a reconstructed local store
    first = await execute_interaction(
        _message().interaction,
        hermes_executable=fake,
        worktree=tmp_path,
        state_root=state,
        source_evidence_root=tmp_path,
        timeout_seconds=5,
        environment={"ARGV_LOG": str(argv_log)},
    )
    second = await execute_interaction(
        _message(interaction_id="019c0014-f0f5-7000-8000-000000000002").interaction,
        hermes_executable=fake,
        worktree=tmp_path,
        state_root=state,
        source_evidence_root=tmp_path,
        timeout_seconds=5,
        environment={"ARGV_LOG": str(argv_log)},
    )
    third = await execute_interaction(
        _message(
            interaction_id="019c0014-f0f5-7000-8000-000000000003",
            family_id="day_trading",
        ).interaction,
        hermes_executable=fake,
        worktree=tmp_path,
        state_root=state,
        source_evidence_root=tmp_path,
        timeout_seconds=5,
        environment={"ARGV_LOG": str(argv_log)},
    )

    # Then: the first captures a session and the second uses the literal resume pair
    invocations = [json.loads(line) for line in argv_log.read_text().splitlines()]
    assert "--resume" not in invocations[0]
    resume_index = invocations[1].index("--resume")
    assert invocations[1][resume_index : resume_index + 2] == [
        "--resume",
        "session-market-context-001",
    ]
    assert "--resume" not in invocations[2]
    assert first.result.state == "completed"
    assert second.result.state == "completed"
    assert third.result.state == "completed"
    assert first.process_started and second.process_started


@pytest.mark.anyio
async def test_duplicate_delivery_launches_no_second_process(tmp_path: Path) -> None:
    # Given: one fake Hermes invocation and one durable interaction UUID
    fake = tmp_path / "fake-hermes"
    count = tmp_path / "count"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "path = os.environ['COUNT_PATH']\n"
        "open(path, 'a').write('1\\n')\n"
        "print(json.dumps({'event':'complete','text':'done',"
        "'session_id':'session-systematic-001','failed':False,'error':None}))\n"
    )
    fake.chmod(0o700)
    message = _message(family_id="systematic_quant")
    settings = {
        "hermes_executable": fake,
        "worktree": tmp_path,
        "state_root": tmp_path / "state",
        "source_evidence_root": tmp_path,
        "timeout_seconds": 5,
        "environment": {"COUNT_PATH": str(count)},
    }

    # When: the exact interaction is delivered twice
    first = await execute_interaction(message.interaction, **settings)
    duplicate = await execute_interaction(message.interaction, **settings)

    # Then: the duplicate returns the durable terminal without another process
    assert count.read_text().splitlines() == ["1"]
    assert first.process_started
    assert not duplicate.process_started
    assert duplicate.result.state == "completed"


@pytest.mark.anyio
async def test_directed_first_then_resume_invokes_hermes_once_and_mutates_real_ledger(
    tmp_path: Path,
) -> None:
    # Given: strict plan-producing Hermes and a fixed owner-controlled hypothesis package
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

    # When: two UI-selected operations run for one persistent family
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

    # Then: each interaction invokes Hermes once, resumes the family, and causes real effects
    invocations = [json.loads(line) for line in argv_log.read_text().splitlines()]
    resume_index = invocations[1].index("--resume")
    reader = ExperimentLedgerReader(
        state / "directed-jobs" / "authoritative" / "opportunity_manager" / "experiment.sqlite3"
    )
    assert len(invocations) == 2
    assert "--resume" not in invocations[0]
    assert invocations[1][resume_index : resume_index + 2] == [
        "--resume",
        "session-directed-plan-001",
    ]
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
        (
            '{"schema_version":1,"operation":"hypothesis","intent":"unsafe",'
            '"argv":["paper-order"],"path":"/tmp/provider"}'
        ),
    ],
)
async def test_invalid_directed_plan_launches_no_research_tool(
    tmp_path: Path,
    plan_text: str,
) -> None:
    # Given: Hermes emits prose, a mismatched operation, or forbidden extra control fields
    fake = _write_fixed_plan_hermes(tmp_path, plan_text)
    source = _write_directed_hypothesis_package(tmp_path)
    state = tmp_path / "state"
    count = tmp_path / "count"

    # When: a directed interaction validates the model output
    outcome = await execute_interaction(
        _message(mode="hypothesis", family_id="opportunity_manager").interaction,
        hermes_executable=fake,
        worktree=tmp_path,
        state_root=state,
        source_evidence_root=source,
        timeout_seconds=5,
        environment={"COUNT_PATH": str(count)},
    )

    # Then: one model call fails terminally before any ledger or directed receipt exists
    assert count.read_text().splitlines() == ["1"]
    assert outcome.result.state == "failed"
    assert outcome.process_started
    assert not (state / "directed-jobs").exists()


@pytest.mark.anyio
async def test_valid_directed_experiment_plan_completes_real_terminal_trial(
    tmp_path: Path,
) -> None:
    # Given: a strict experiment plan and the fixed safe local research package
    fake = _write_directed_hermes(tmp_path)
    source = _write_experiment_package(tmp_path)
    state = tmp_path / "state"

    # When: the UI-selected experiment executes through the persistent family channel
    outcome = await execute_interaction(
        _message(mode="experiment", family_id="systematic_quant").interaction,
        hermes_executable=fake,
        worktree=tmp_path,
        state_root=state,
        source_evidence_root=source,
        timeout_seconds=10,
        environment={"ARGV_LOG": str(tmp_path / "experiment-argv.jsonl")},
    )

    # Then: model planning occurs once and the authoritative ledger owns terminal completion
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
    # Given: the real hypothesis write succeeds but its broker receipt is lost
    fake = _write_directed_hermes(tmp_path)
    source = _write_directed_hypothesis_package(tmp_path)
    state = tmp_path / "state"
    argv_log = tmp_path / "uncertain-argv.jsonl"
    original = AuthoritativeDirectedResearchBroker.execute
    calls = 0

    def write_then_crash(
        broker: AuthoritativeDirectedResearchBroker,
        operation: str,
        family_id: str,
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

    # When: the exact interaction reconnects after the post-effect crash
    first = await execute_interaction(message.interaction, **settings)
    replay = await execute_interaction(message.interaction, **settings)

    # Then: claim and directed terminal agree on uncertain with one model/tool invocation
    reader = ExperimentLedgerReader(
        state / "directed-jobs" / "authoritative" / "opportunity_manager" / "experiment.sqlite3"
    )
    assert len(reader.research_hypothesis_cards()) == 1
    assert first.result.state == "uncertain"
    assert first.directed_events[-1].state == "uncertain"
    assert replay.result.state == "uncertain"
    assert calls == 1
    assert len(argv_log.read_text().splitlines()) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["launch", "timeout", "malformed"])
async def test_process_failure_seams_close_without_paid_retry(
    tmp_path: Path,
    failure: str,
) -> None:
    # Given: a launch, timeout, or strict-protocol failure seam
    fake = tmp_path / "fake-hermes"
    match failure:
        case "launch":
            pass
        case "timeout":
            fake.write_text("#!/usr/bin/env python3\nimport select\nselect.select([], [], [])\n")
            fake.chmod(0o700)
        case "malformed":
            fake.write_text("#!/usr/bin/env python3\nprint('not-json')\n")
            fake.chmod(0o700)
        case unexpected:
            raise AssertionError(unexpected)
    message = _message(family_id="derivatives_research")
    settings = {
        "hermes_executable": fake,
        "worktree": tmp_path,
        "state_root": tmp_path / "state",
        "source_evidence_root": tmp_path,
        "timeout_seconds": 0.05,
    }

    # When: the same interaction is delivered again after the failure
    first = await execute_interaction(message.interaction, **settings)
    duplicate = await execute_interaction(message.interaction, **settings)

    # Then: it stays terminal with at most one attempted process and no retry
    assert first.result.state == "failed"
    assert duplicate.result.state == "failed"
    assert first.process_started
    assert not duplicate.process_started


def _write_directed_hypothesis_package(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    package = source / "directed-research"
    package.mkdir(parents=True)
    (package / "hypothesis.json").write_bytes(HYPOTHESIS_MANIFEST.read_bytes())
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
