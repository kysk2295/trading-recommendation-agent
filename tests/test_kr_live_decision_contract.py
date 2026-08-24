from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.test_day_session_service import _config
from tests.test_kr_day_capsule_shadow import _advance, _plain_evaluation
from tests.test_kr_day_capsule_shadow_cli import _publish_request, _request_for
from trading_agent.day_session_service import run_day_session_service_tick
from trading_agent.day_session_service_config import KrDaySessionServiceConfig

_FIRST_CYCLE_STATUSES = frozenset({"INVESTIGATING", "ARMED", "REJECTED", "BLOCKED", "EXPIRED"})
_RESOLVED_STATUSES = frozenset({"ARMED", "REJECTED", "BLOCKED", "EXPIRED"})


def test_active_capsule_persists_one_reason_bearing_pre_entry_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one active KR capsule backed by a current-session completed-bar request.
    evaluation = _plain_evaluation()
    request = _publish_request(tmp_path, "first-cycle", _request_for(evaluation))
    config = _kr_config(tmp_path)
    _activate_capsule(monkeypatch, evaluation.capsule_id, request)

    # When: the public KR day-session service runs one real shadow child cycle.
    result = run_day_session_service_tick(
        config,
        clock=lambda: evaluation.evaluated_at.astimezone(dt.UTC),
    )

    # Then: the active capsule cannot disappear behind a None or an unreasoned shadow event.
    decisions = getattr(result, "decisions", ())
    assert result.status == "processed"
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision is not None
    assert decision.status in _FIRST_CYCLE_STATUSES
    assert decision.reason_codes
    assert decision.observed_at is not None


def test_unchanged_candidate_resolves_by_the_next_completed_bar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the same active capsule on two consecutive completed bars.
    first = _plain_evaluation()
    second = _advance(first)
    first_request = _publish_request(tmp_path, "first-cycle", _request_for(first))
    second_request = _publish_request(tmp_path, "second-cycle", _request_for(second))
    config = _kr_config(tmp_path)
    _activate_capsule(monkeypatch, first.capsule_id, first_request, second_request)

    # When: the public service observes the candidate again on the next completed bar.
    _ = run_day_session_service_tick(config, clock=lambda: first.evaluated_at.astimezone(dt.UTC))
    result = run_day_session_service_tick(config, clock=lambda: second.evaluated_at.astimezone(dt.UTC))

    # Then: an unchanged candidate is no longer left indefinitely INVESTIGATING.
    decisions = getattr(result, "decisions", ())
    assert result.status == "processed"
    assert len(decisions) == 1
    assert decisions[0].status in _RESOLVED_STATUSES


def test_no_opportunity_cycle_does_not_fabricate_a_recommendation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an open KR service cycle without an active capsule or opportunity.
    config = _kr_config(tmp_path)
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)

    # When: the public service runs its no-op cycle.
    result = run_day_session_service_tick(
        config,
        clock=lambda: dt.datetime(2026, 8, 24, 10, 2, 2, tzinfo=dt.UTC),
    )

    # Then: no decision projection fabricates a recommendation.
    assert result.status == "no_action"
    assert getattr(result, "decisions", ()) == ()


def _kr_config(tmp_path: Path) -> KrDaySessionServiceConfig:
    config = _config("kr", tmp_path)
    assert isinstance(config, KrDaySessionServiceConfig)
    return config


def _activate_capsule(
    monkeypatch: pytest.MonkeyPatch,
    capsule_id: str,
    first_request: Path,
    second_request: Path | None = None,
) -> None:
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)
    monkeypatch.setattr(
        "trading_agent.day_session_service._kr_active_capsule_ids",
        lambda _ledger, _now: (capsule_id,),
    )
    requests = (first_request,) if second_request is None else (first_request, second_request)
    call_count = 0

    def materialize(
        _config: KrDaySessionServiceConfig,
        _now: dt.datetime,
        _capsules: tuple[str, ...],
    ) -> tuple[Path, ...]:
        nonlocal call_count
        request = requests[min(call_count, len(requests) - 1)]
        call_count += 1
        return (request,)

    monkeypatch.setattr("trading_agent.day_session_service._materialize_kr_requests", materialize)
