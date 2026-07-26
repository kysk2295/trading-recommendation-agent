from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.dashboard_commands import DashboardInteractionMessage, execute_interaction, parse_dashboard_event


def _message(
    *,
    interaction_id: str = "019c0014-f0f5-7000-8000-000000000001",
    family_id: str = "market_context",
    mode: str = "conversation",
    command: str = "현재 실제 데이터 결손을 한 문장으로 설명해줘",
) -> DashboardInteractionMessage:
    return DashboardInteractionMessage.model_validate(
        {
            "type": "interaction",
            "interaction": {
                "id": interaction_id,
                "agent_id": family_id,
                "mode": mode,
                "command": command,
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
async def test_first_then_resume_uses_exact_session_argv_across_restart(tmp_path: Path) -> None:
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
    settings = {
        "hermes_executable": fake,
        "worktree": tmp_path,
        "state_root": state,
        "source_evidence_root": tmp_path,
        "timeout_seconds": 5,
        "environment": {"ARGV_LOG": str(argv_log)},
    }

    first = await execute_interaction(_message().interaction, **settings)
    second = await execute_interaction(
        _message(interaction_id="019c0014-f0f5-7000-8000-000000000002").interaction,
        **settings,
    )
    third = await execute_interaction(
        _message(interaction_id="019c0014-f0f5-7000-8000-000000000003", family_id="day_trading").interaction,
        **settings,
    )

    invocations = [json.loads(line) for line in argv_log.read_text().splitlines()]
    resume_index = invocations[1].index("--resume")
    assert "--resume" not in invocations[0]
    assert invocations[1][resume_index : resume_index + 2] == ["--resume", "session-market-context-001"]
    assert "--resume" not in invocations[2]
    assert first.result.state == "completed"
    assert second.result.state == "completed"
    assert third.result.state == "completed"
    assert first.process_started and second.process_started


@pytest.mark.anyio
async def test_duplicate_delivery_launches_no_second_process(tmp_path: Path) -> None:
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

    first = await execute_interaction(message.interaction, **settings)
    duplicate = await execute_interaction(message.interaction, **settings)

    assert count.read_text().splitlines() == ["1"]
    assert first.process_started
    assert not duplicate.process_started
    assert duplicate.result.state == "completed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("family_id", "mode", "command"),
    [
        ("day_trading", "conversation", "현재 실제 데이터 결손을 한 문장으로 설명해줘"),
        ("market_context", "analysis", "현재 실제 데이터 결손을 한 문장으로 설명해줘"),
        ("market_context", "conversation", "다른 요청"),
    ],
)
async def test_reused_uuid_with_conflicting_identity_launches_zero_and_rejects(
    tmp_path: Path,
    family_id: str,
    mode: str,
    command: str,
) -> None:
    fake = tmp_path / "fake-hermes"
    count = tmp_path / "count"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "open(os.environ['COUNT_PATH'], 'a').write('1\\n')\n"
        "print(json.dumps({'event':'complete','text':'done',"
        "'session_id':'session-identity-001','failed':False,'error':None}))\n"
    )
    fake.chmod(0o700)
    first = _message()
    conflict = _message(family_id=family_id, mode=mode, command=command)
    settings = {
        "hermes_executable": fake,
        "worktree": tmp_path,
        "state_root": tmp_path / "state",
        "source_evidence_root": tmp_path,
        "timeout_seconds": 5,
        "environment": {"COUNT_PATH": str(count)},
    }

    original = await execute_interaction(first.interaction, **settings)
    rejected = await execute_interaction(conflict.interaction, **settings)

    assert original.result.state == "completed"
    assert rejected.result.state == "failed"
    assert rejected.result.response == "interaction_identity_conflict"
    assert not rejected.process_started
    assert count.read_text().splitlines() == ["1"]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["launch", "timeout", "malformed"])
async def test_process_failure_seams_close_without_paid_retry(tmp_path: Path, failure: str) -> None:
    fake = tmp_path / "fake-hermes"
    if failure == "timeout":
        fake.write_text("#!/usr/bin/env python3\nimport select\nselect.select([], [], [])\n")
        fake.chmod(0o700)
    elif failure == "malformed":
        fake.write_text("#!/usr/bin/env python3\nprint('not-json')\n")
        fake.chmod(0o700)
    elif failure != "launch":
        raise AssertionError(failure)
    message = _message(family_id="derivatives_research")
    settings = {
        "hermes_executable": fake,
        "worktree": tmp_path,
        "state_root": tmp_path / "state",
        "source_evidence_root": tmp_path,
        "timeout_seconds": 0.05,
    }

    first = await execute_interaction(message.interaction, **settings)
    duplicate = await execute_interaction(message.interaction, **settings)

    assert first.result.state == "failed"
    assert duplicate.result.state == "failed"
    assert first.process_started
    assert not duplicate.process_started
