from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.dashboard_commands import (
    DashboardInteractionMessage,
    execute_interaction,
    parse_dashboard_event,
)


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
async def test_directed_interaction_emits_real_evidence_without_hermes(tmp_path: Path) -> None:
    # Given: a typed hypothesis request with one local source receipt
    source = tmp_path / "source"
    source.mkdir()
    (source / "receipt.json").write_text('{"safe_ref":"safe"}')
    message = _message(mode="hypothesis", family_id="opportunity_manager")

    # When: the interactive executor handles it
    outcome = await execute_interaction(
        message.interaction,
        hermes_executable=tmp_path / "must-not-run",
        worktree=tmp_path,
        state_root=tmp_path / "state",
        source_evidence_root=source,
        timeout_seconds=5,
    )

    # Then: a code-owned directed job completes with progress, evidence, and result
    assert not outcome.process_started
    assert [event.kind for event in outcome.directed_events][-2:] == ["evidence", "result"]
    assert outcome.result.state == "completed"


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
