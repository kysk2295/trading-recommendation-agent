from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from pydantic import ValidationError

from tests.test_autonomous_memory_store import NOW, TASK, record_fixture

OTHER_TASK = hashlib.sha256(b"other-memory-source-task").hexdigest()


def test_memory_key_enforces_regex_and_length_boundaries() -> None:
    assert len(record_fixture(memory_key="a1234567").memory_key) == 8
    assert len(record_fixture(memory_key="a" * 160).memory_key) == 160
    with pytest.raises(ValidationError):
        record_fixture(memory_key="short")
    with pytest.raises(ValidationError):
        record_fixture(memory_key="!1234567")
    with pytest.raises(ValidationError):
        record_fixture(memory_key="a" * 161)


def test_version_and_summary_enforce_boundaries() -> None:
    assert record_fixture(summary="s" * 8).summary == "s" * 8
    assert len(record_fixture(summary="s" * 4_000).summary) == 4_000
    with pytest.raises(ValidationError):
        record_fixture(version=0)
    with pytest.raises(ValidationError):
        record_fixture(summary="short")
    with pytest.raises(ValidationError):
        record_fixture(summary="s" * 4_001)


def test_recorded_at_rejects_naive_and_normalizes_aware_time() -> None:
    normalized = record_fixture(recorded_at=dt.datetime(2026, 8, 26, 23, 30, tzinfo=dt.timezone(dt.timedelta(hours=9))))
    assert normalized.recorded_at == NOW
    with pytest.raises(ValidationError):
        record_fixture(recorded_at=dt.datetime(2026, 8, 26, 14, 30))


def test_fact_refs_require_sorted_unique_nonempty_entries() -> None:
    with pytest.raises(ValidationError, match="sorted_unique_fact_refs_required"):
        record_fixture(fact_refs=("fact:z", "fact:a"))
    with pytest.raises(ValidationError, match="sorted_unique_fact_refs_required"):
        record_fixture(fact_refs=("fact:a", "fact:a"))
    with pytest.raises(ValidationError, match="sorted_unique_fact_refs_required"):
        record_fixture(fact_refs=("",))


def test_inference_refs_require_sorted_unique_nonempty_entries() -> None:
    with pytest.raises(ValidationError, match="sorted_unique_inference_refs_required"):
        record_fixture(inference_refs=("inference:z", "inference:a"))
    with pytest.raises(ValidationError, match="sorted_unique_inference_refs_required"):
        record_fixture(inference_refs=("inference:a", "inference:a"))
    with pytest.raises(ValidationError, match="sorted_unique_inference_refs_required"):
        record_fixture(inference_refs=("",))


def test_subject_refs_require_sorted_unique_nonempty_entries() -> None:
    with pytest.raises(ValidationError, match="sorted_unique_subject_refs_required"):
        record_fixture(subject_refs=("subject:z", "subject:a"))
    with pytest.raises(ValidationError, match="sorted_unique_subject_refs_required"):
        record_fixture(subject_refs=("subject:a", "subject:a"))
    with pytest.raises(ValidationError, match="sorted_unique_subject_refs_required"):
        record_fixture(subject_refs=("",))


def test_evidence_refs_require_sorted_unique_nonempty_entries() -> None:
    with pytest.raises(ValidationError, match="sorted_unique_evidence_refs_required"):
        record_fixture(evidence_refs=("evidence:z", "evidence:a"))
    with pytest.raises(ValidationError, match="sorted_unique_evidence_refs_required"):
        record_fixture(evidence_refs=("evidence:a", "evidence:a"))
    with pytest.raises(ValidationError, match="sorted_unique_evidence_refs_required"):
        record_fixture(evidence_refs=("",))


def test_source_task_ids_require_sorted_unique_nonempty_entries() -> None:
    with pytest.raises(ValidationError):
        record_fixture(source_task_ids=())
    with pytest.raises(ValidationError, match="sorted_unique_source_task_ids_required"):
        record_fixture(source_task_ids=(TASK, TASK))
    with pytest.raises(ValidationError, match="sorted_unique_source_task_ids_required"):
        record_fixture(source_task_ids=tuple(sorted((TASK, OTHER_TASK), reverse=True)))


def test_record_requires_fact_or_inference_but_accepts_inference_only() -> None:
    inference_only = record_fixture(fact_refs=(), inference_refs=("inference:market",))
    assert inference_only.inference_refs == ("inference:market",)
    with pytest.raises(ValidationError, match="memory_lineage_required"):
        record_fixture(fact_refs=(), inference_refs=())
