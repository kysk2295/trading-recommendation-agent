from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

import pytest

from tests.test_research_agent_service_cli import _config
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.research_agent_service_health import (
    ResearchAgentServiceHealth,
    ResearchAgentServiceHealthEvaluation,
    await_fresh_research_agent_service_health,
    evaluate_persisted_research_agent_service_health,
    research_agent_service_health_path,
)

NOW = dt.datetime(2026, 8, 11, 3, 0, tzinfo=dt.UTC)
CONFIG_SHA256 = "a" * 64


def test_evaluator_accepts_only_a_fresh_matching_ready_health(tmp_path) -> None:
    # Given: a candidate-bound ready report written after its kickstart.
    health = ResearchAgentServiceHealth(
        config_sha256=CONFIG_SHA256,
        observed_at=NOW + dt.timedelta(seconds=1),
        state="ready",
        reason="runtime_ready",
    )
    write_private_stable_report(
        research_agent_service_health_path(tmp_path),
        health.model_dump_json() + "\n",
    )

    # When: the candidate cutover evaluates the private artifact.
    evaluation = evaluate_persisted_research_agent_service_health(
        tmp_path,
        CONFIG_SHA256,
        NOW,
        NOW + dt.timedelta(seconds=2),
    )

    # Then: the candidate is the only accepted healthy instance.
    assert evaluation.accepted is True
    assert evaluation.state == "healthy"
    assert evaluation.reason == "fresh_matching_ready"


def test_evaluator_rejects_a_mismatched_candidate_health(tmp_path: Path) -> None:
    # Given: a report that is both from another candidate and not ready.
    health = ResearchAgentServiceHealth(
        config_sha256="b" * 64,
        observed_at=NOW + dt.timedelta(seconds=1),
        state="failed",
        reason="runtime_failed",
    )
    write_private_stable_report(
        research_agent_service_health_path(tmp_path),
        health.model_dump_json() + "\n",
    )

    # When: the replacement checks the candidate-bound health artifact.
    evaluation = evaluate_persisted_research_agent_service_health(
        tmp_path,
        CONFIG_SHA256,
        NOW,
        NOW + dt.timedelta(seconds=2),
    )

    # Then: the identity mismatch prevents the failed report from being accepted.
    assert evaluation.accepted is False
    assert evaluation.state == "unhealthy"
    assert evaluation.reason == "candidate_mismatch"


def test_evaluator_rejects_missing_health(tmp_path: Path) -> None:
    # Given: no health artifact in the private output root.

    # When: the candidate cutover evaluates persisted health.
    evaluation = evaluate_persisted_research_agent_service_health(
        tmp_path,
        CONFIG_SHA256,
        NOW,
        NOW + dt.timedelta(seconds=2),
    )

    # Then: absence fails closed with its typed reason.
    assert evaluation.accepted is False
    assert evaluation.reason == "report_missing_or_invalid"


def test_evaluator_rejects_malformed_health(tmp_path: Path) -> None:
    # Given: a private health artifact that is not valid JSON.
    write_private_stable_report(research_agent_service_health_path(tmp_path), "not-json\n")

    # When: the candidate cutover evaluates persisted health.
    evaluation = evaluate_persisted_research_agent_service_health(
        tmp_path,
        CONFIG_SHA256,
        NOW,
        NOW + dt.timedelta(seconds=2),
    )

    # Then: malformed data fails closed with its typed reason.
    assert evaluation.accepted is False
    assert evaluation.reason == "report_missing_or_invalid"


def test_evaluator_rejects_stale_health(tmp_path: Path) -> None:
    # Given: matching ready health observed at the cutover boundary.
    _write_health(tmp_path, observed_at=NOW, state="ready", reason="runtime_ready")

    # When: the candidate cutover evaluates persisted health.
    evaluation = evaluate_persisted_research_agent_service_health(
        tmp_path,
        CONFIG_SHA256,
        NOW,
        NOW + dt.timedelta(seconds=2),
    )

    # Then: boundary-equal health is stale and rejected.
    assert evaluation.accepted is False
    assert evaluation.reason == "not_fresh"


def test_evaluator_rejects_future_health(tmp_path: Path) -> None:
    # Given: matching ready health that claims a future observation.
    _write_health(
        tmp_path,
        observed_at=NOW + dt.timedelta(seconds=3),
        state="ready",
        reason="runtime_ready",
    )

    # When: the candidate cutover evaluates persisted health.
    evaluation = evaluate_persisted_research_agent_service_health(
        tmp_path,
        CONFIG_SHA256,
        NOW,
        NOW + dt.timedelta(seconds=2),
    )

    # Then: future health is rejected before readiness is considered.
    assert evaluation.accepted is False
    assert evaluation.reason == "observed_in_future"


def test_evaluator_rejects_failed_health(tmp_path: Path) -> None:
    # Given: fresh matching health that explicitly reports runtime failure.
    _write_health(
        tmp_path,
        observed_at=NOW + dt.timedelta(seconds=1),
        state="failed",
        reason="runtime_failed",
    )

    # When: the candidate cutover evaluates persisted health.
    evaluation = evaluate_persisted_research_agent_service_health(
        tmp_path,
        CONFIG_SHA256,
        NOW,
        NOW + dt.timedelta(seconds=2),
    )

    # Then: failed health cannot satisfy candidate readiness.
    assert evaluation.accepted is False
    assert evaluation.reason == "runtime_failed"


def test_health_wait_accepts_slow_candidate_within_thirty_seconds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    monkeypatch.setattr("trading_agent.research_agent_service_health.time.sleep", lambda _seconds: None)

    def evaluator(*_args) -> ResearchAgentServiceHealthEvaluation:
        nonlocal attempts
        attempts += 1
        accepted = attempts == 25
        return ResearchAgentServiceHealthEvaluation(
            accepted=accepted,
            state="healthy" if accepted else "unhealthy",
            reason="fresh_matching_ready" if accepted else "report_missing_or_invalid",
            health=None,
        )

    evaluation = await_fresh_research_agent_service_health(
        _config(tmp_path),
        NOW,
        lambda: NOW + dt.timedelta(seconds=1),
        evaluator,
    )

    assert evaluation.accepted
    assert attempts == 25


def test_health_wait_returns_terminal_candidate_mismatch_without_sleep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(
        "trading_agent.research_agent_service_health.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    evaluation = await_fresh_research_agent_service_health(
        _config(tmp_path),
        NOW,
        lambda: NOW + dt.timedelta(seconds=1),
        lambda *_args: ResearchAgentServiceHealthEvaluation(
            accepted=False,
            state="unhealthy",
            reason="candidate_mismatch",
            health=None,
        ),
    )

    assert evaluation.reason == "candidate_mismatch"
    assert sleeps == []


def _write_health(
    tmp_path: Path,
    observed_at: dt.datetime,
    state: Literal["ready", "failed"],
    reason: Literal["runtime_ready", "runtime_failed"],
) -> None:
    health = ResearchAgentServiceHealth(
        config_sha256=CONFIG_SHA256,
        observed_at=observed_at,
        state=state,
        reason=reason,
    )
    write_private_stable_report(
        research_agent_service_health_path(tmp_path),
        health.model_dump_json() + "\n",
    )
