from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tests.test_autonomous_task_models import NOW, budget, step_fixture, task_fixture
from trading_agent._autonomous_supervisor_steps import reasoning_request
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import canonical_reasoning_prompt


def test_reasoning_prompt_compacts_repeated_step_authority_without_losing_history(
    tmp_path: Path,
) -> None:
    steps = tuple(
        step_fixture(
            sequence=sequence,
            occurred_at=NOW + dt.timedelta(seconds=sequence),
            payload_json=json.dumps(
                {"detail": "bounded durable detail", "kind": "decision", "sequence": sequence},
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        for sequence in range(1, 7)
    )

    request = reasoning_request(
        AutonomousMemoryStore(tmp_path / "memory.sqlite3"),
        (("evidence.read",), ("evidence.read()",)),
        task_fixture(),
        steps,
        NOW + dt.timedelta(minutes=1),
        budget(),
    )

    rendered = json.loads(canonical_reasoning_prompt(request))["prior_steps"]

    assert request.prior_steps == steps
    assert [item["sequence"] for item in rendered] == list(range(1, 7))
    assert [item["payload_json"] for item in rendered[:2]] == ['{"kind":"decision"}'] * 2
    assert [item["payload_json"] for item in rendered[2:]] == [step.payload_json for step in steps[2:]]
    assert all(
        set(item)
        == {
            "blocked_reason",
            "next_wake_at",
            "next_wake_event",
            "occurred_at",
            "payload_json",
            "role",
            "sequence",
            "state",
            "terminal_reason",
        }
        for item in rendered
    )
