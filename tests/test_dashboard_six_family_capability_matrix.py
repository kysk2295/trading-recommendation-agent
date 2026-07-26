from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter

from tests.test_dashboard_commands import _message
from trading_agent.dashboard_agent_family import (
    PRIMARY_AGENT_FAMILIES,
    AgentFamilyId,
)
from trading_agent.dashboard_commands import execute_interaction

_argv_adapter = TypeAdapter(list[str])


@pytest.mark.anyio
@pytest.mark.parametrize("family_id", PRIMARY_AGENT_FAMILIES)
async def test_six_family_initial_resume_and_directed_process_boundary(
    tmp_path: Path,
    family_id: AgentFamilyId,
) -> None:
    # Given: one deterministic non-model process boundary for a primary family.
    executable = _write_capability_stub(tmp_path)
    argv_log = tmp_path / "argv.jsonl"
    family_index = PRIMARY_AGENT_FAMILIES.index(family_id)
    settings = {
        "hermes_executable": executable,
        "worktree": tmp_path,
        "state_root": tmp_path / family_id,
        "source_evidence_root": tmp_path / "source",
        "timeout_seconds": 5,
        "environment": {"ARGV_LOG": str(argv_log)},
    }

    # When: initial conversation, resume, and directed analysis cross the process seam.
    first = await execute_interaction(
        _message(
            interaction_id=_interaction_id(family_index, 1),
            family_id=family_id,
        ).interaction,
        **settings,
    )
    resumed = await execute_interaction(
        _message(
            interaction_id=_interaction_id(family_index, 2),
            family_id=family_id,
        ).interaction,
        **settings,
    )
    directed = await execute_interaction(
        _message(
            interaction_id=_interaction_id(family_index, 3),
            family_id=family_id,
            mode="analysis",
        ).interaction,
        **settings,
    )

    # Then: all three invocations occur, both later calls resume the family-bound session.
    invocations = [
        _argv_adapter.validate_json(line)
        for line in argv_log.read_text(encoding="utf-8").splitlines()
    ]
    session_id = f"fixture-session-{family_id}"
    assert len(invocations) == 3
    assert "--resume" not in invocations[0]
    assert _resume_value(invocations[1]) == session_id
    assert _resume_value(invocations[2]) == session_id
    assert first.process_started and first.result.state == "completed"
    assert resumed.process_started and resumed.result.state == "completed"
    assert directed.process_started


def _interaction_id(family_index: int, sequence: int) -> str:
    suffix = family_index * 10 + sequence
    return f"019c0014-f0f5-7000-8000-{suffix:012d}"


def _resume_value(argv: list[str]) -> str:
    return argv[argv.index("--resume") + 1]


def _write_capability_stub(tmp_path: Path) -> Path:
    executable = tmp_path / "capability-stub"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "prompt = sys.argv[-1]\n"
        "family = prompt.split('<agent-family>', 1)[1].split('</agent-family>', 1)[0]\n"
        "session = f'fixture-session-{family}'\n"
        "directed = 'The operation must remain analysis.' in prompt\n"
        "text = json.dumps({'schema_version':1,'operation':'analysis',"
        "'intent':'bounded matrix analysis'}) if directed else 'bounded response'\n"
        "with open(os.environ['ARGV_LOG'], 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print(json.dumps({'event':'complete','text':text,'session_id':session,"
        "'failed':False,'error':None}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable
