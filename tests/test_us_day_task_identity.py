from __future__ import annotations

from trading_agent.us_day_task_identity import us_day_task_id


def test_task_identity_changes_with_the_current_situation() -> None:
    # Given: one deployed version and two completed-bar situation maps.
    version_id = "a" * 64

    # When: task identities are derived for each situation.
    first = us_day_task_id(version_id, "b" * 64)
    second = us_day_task_id(version_id, "c" * 64)

    # Then: each current situation receives a distinct restart-stable task.
    assert first != second
    assert first == us_day_task_id(version_id, "b" * 64)
    assert first.startswith("us-day-")
