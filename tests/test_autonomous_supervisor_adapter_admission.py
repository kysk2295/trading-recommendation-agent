from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_autonomous_supervisor_adapter import NOW, _adapter, _evidence


def test_related_admission_preserves_payload_hash_distinct_from_source_reference(tmp_path: Path) -> None:
    # Given: related evidence has a provider reference distinct from its bounded payload hash.
    adapter = _adapter(
        tmp_path,
        (NOW + dt.timedelta(minutes=5), NOW + dt.timedelta(minutes=10)),
    )
    first = _evidence("day_trading", "a")
    second = _evidence(
        "day_trading",
        "b",
        payload='{"price":71000}',
        evidence_ref="provider:second",
    )
    _ = adapter.tick(first, NOW)

    # When: the related evidence is admitted to the existing task.
    admitted = adapter.tick(second, NOW + dt.timedelta(minutes=5))

    # Then: both independent references remain durable.
    task = adapter.runtime.tasks.reader().task(admitted.task_id or "")
    assert task is not None
    assert second.payload_sha256 in task.evidence_refs
    assert "provider:second" in task.evidence_refs
