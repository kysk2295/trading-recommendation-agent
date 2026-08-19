from __future__ import annotations

import datetime as dt
import resource
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_strategy_research_science_kernel import _attempt, _draft, _experiment, _payload
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.strategy_research_catalog import STRATEGY_RESEARCH_CATALOG
from trading_agent.strategy_research_experiment_models import ScienceCycleResult
from trading_agent.strategy_research_ledger import StrategyResearchLedgerError
from trading_agent.strategy_research_observation_builders import SourceAuthorityReceipt
from trading_agent.strategy_research_policy import FeedbackWorkPurpose
from trading_agent.strategy_research_runtime import (
    StrategyResearchRuntime,
    StrategyResearchWork,
)
from trading_agent.strategy_research_runtime_models import StrategyResearchRuntimeBusyError
from trading_agent.strategy_research_runtime_source import ScienceKernelCycleRunner
from trading_agent.strategy_research_types import ResearchAgentId

UTC = dt.UTC
NOW = dt.datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def _work(agent_id: ResearchAgentId, due_at: dt.datetime) -> StrategyResearchWork:
    identity = next(row for row in STRATEGY_RESEARCH_CATALOG if row.agent_id is agent_id)
    available_at = due_at - dt.timedelta(minutes=identity.cadence.delay_minutes)
    payload = _payload((1.0, 0.03))
    draft = _draft(payload).model_copy(
        update={
            "agent_id": agent_id,
            "observation": _draft(payload).observation.model_copy(update={"owner_agent_id": agent_id}),
            "hypothesis_id": f"hypothesis-{agent_id.value}-001",
            "search_family_id": f"family-{agent_id.value}-001",
        }
    )
    source_id = draft.source_refs[0].source_id
    receipts = tuple(
        SourceAuthorityReceipt(
            authority=authority,
            source_id=source_id,
            as_of=available_at - age,
            available_at=available_at,
            immutable=True,
            complete=True,
            wiring_only=True,
        )
        for authority, age in (
            ("consolidated_completed_bar", dt.timedelta(minutes=5)),
            ("fresh_actionable_spread", dt.timedelta(minutes=1)),
            ("current_market_session", dt.timedelta(0)),
        )
    )
    return StrategyResearchWork(
        evidence_event_id=f"evidence-{agent_id.value}-001",
        available_at=available_at,
        maturity_at=available_at,
        draft=draft,
        experiment=_experiment(_attempt(1.0, 0.02)),
        sealed_holdout=payload,
        source_receipts=receipts,
    )


def test_pending_future_outcome_keeps_owner_visible_without_starting_science_cycle(tmp_path: Path) -> None:
    agent_id = ResearchAgentId.INTRADAY_MOMENTUM
    pending = _work(agent_id, NOW).model_copy(
        update={"experiment": None, "sealed_holdout": None, "maturity_at": NOW + dt.timedelta(minutes=30)}
    )
    source = _Source({agent_id: pending})
    runner = _Runner()

    status = StrategyResearchRuntime(ExperimentLedgerStore(tmp_path / "ledger.sqlite3"), source, runner).tick(NOW)

    assert status.slot(agent_id).state == "waiting_maturity"
    assert status.slot(agent_id).hypothesis_id == pending.draft.hypothesis_id
    assert status.heavy_cycles_started == 0
    assert runner.completed == []


@dataclass(slots=True)
class _Source:
    work: dict[ResearchAgentId, StrategyResearchWork]

    def next_work(
        self,
        agent_id: ResearchAgentId,
        evidence_cursor: str | None,
    ) -> StrategyResearchWork | None:
        candidate = self.work.get(agent_id)
        return None if candidate is None or candidate.evidence_event_id == evidence_cursor else candidate


@dataclass(slots=True)
class _Runner:
    completed: list[ResearchAgentId] = field(default_factory=list)
    fail_once: set[ResearchAgentId] = field(default_factory=set)
    interrupt_once: set[ResearchAgentId] = field(default_factory=set)

    def run(self, work: StrategyResearchWork) -> ScienceCycleResult:
        agent_id = work.draft.agent_id
        if agent_id in self.interrupt_once:
            self.interrupt_once.remove(agent_id)
            raise KeyboardInterrupt
        if agent_id in self.fail_once:
            self.fail_once.remove(agent_id)
            raise StrategyResearchLedgerError("bounded_fixture_failure")
        self.completed.append(agent_id)
        return ScienceCycleResult.model_construct(
            source_ids=(work.draft.source_refs[0].source_id,),
            owner_agent_id=agent_id,
            hypothesis_id=work.draft.hypothesis_id,
            protocol_id="a" * 64,
            attempt_ids=(f"attempt-{agent_id.value}",),
            selected_attempt_id=f"attempt-{agent_id.value}",
            holdout_reveal_id=f"reveal-{agent_id.value}",
            terminal=None,
            feedback_result_id=f"feedback-{agent_id.value}",
        )


def _runtime(tmp_path: Path, source: _Source, runner: _Runner) -> StrategyResearchRuntime:
    return StrategyResearchRuntime(ExperimentLedgerStore(tmp_path / "ledger.sqlite3"), source, runner)


def _terminal_work(
    agent_id: ResearchAgentId,
    outcome_value: float,
    observations: int = 24,
) -> StrategyResearchWork:
    payload = _payload((1.0, outcome_value), observations=observations)
    work = _work(agent_id, NOW)
    seal = work.draft.holdout_period_sealed_ref.model_copy(update={"commitment_sha256": payload.content_sha256})
    return work.model_copy(
        update={
            "draft": work.draft.model_copy(update={"holdout_period_sealed_ref": seal}),
            "sealed_holdout": payload,
        }
    )


def test_momentum_progresses_when_catalyst_has_no_work(tmp_path: Path) -> None:
    # Given: momentum is due while the catalyst family has no evidence.
    source = _Source({ResearchAgentId.INTRADAY_MOMENTUM: _work(ResearchAgentId.INTRADAY_MOMENTUM, NOW)})
    runner = _Runner()

    # When: the independent scheduler evaluates all six slots.
    report = _runtime(tmp_path, source, runner).tick(NOW)

    # Then: momentum completes without a global six-family barrier.
    assert runner.completed == [ResearchAgentId.INTRADAY_MOMENTUM]
    assert report.heavy_cycles_started == 1
    assert report.slot(ResearchAgentId.CATALYST_EVENT).state == "waiting_evidence"


def test_six_slots_keep_independent_due_times_and_restart_state(tmp_path: Path) -> None:
    # Given: each catalog family has evidence at a different timestamp.
    source = _Source(
        {
            row.agent_id: _work(row.agent_id, NOW + dt.timedelta(minutes=index + 10))
            for index, row in enumerate(STRATEGY_RESEARCH_CATALOG)
        }
    )

    # When: one runtime initializes state and another process reads the same ledger.
    first = _runtime(tmp_path, source, _Runner()).tick(NOW)
    restarted = _runtime(tmp_path, source, _Runner()).tick(NOW)

    # Then: all six keyed states persist with six independent due timestamps.
    assert tuple(slot.agent_id for slot in first.slots) == tuple(row.agent_id for row in STRATEGY_RESEARCH_CATALOG)
    assert len({slot.next_due_at for slot in first.slots}) == 6
    assert restarted.slots == first.slots


def test_one_heavy_cycle_per_tick_then_next_due_agent_progresses(tmp_path: Path) -> None:
    # Given: momentum and mean reversion are simultaneously due.
    source = _Source(
        {
            agent_id: _work(agent_id, NOW)
            for agent_id in (ResearchAgentId.INTRADAY_MOMENTUM, ResearchAgentId.INTRADAY_MEAN_REVERSION)
        }
    )
    runner = _Runner()

    # When: two adjacent daemon ticks run.
    first = _runtime(tmp_path, source, runner).tick(NOW)
    second = _runtime(tmp_path, source, runner).tick(NOW + dt.timedelta(seconds=1))

    # Then: deterministic catalog order starts one heavy cycle on each tick.
    assert first.heavy_cycles_started == second.heavy_cycles_started == 1
    assert runner.completed == [ResearchAgentId.INTRADAY_MOMENTUM, ResearchAgentId.INTRADAY_MEAN_REVERSION]


def test_second_process_fails_before_state_mutation_while_science_cycle_is_active(tmp_path: Path) -> None:
    # Given: one production runtime process is blocked inside its active science cycle.
    child_source = """
import sys
from pathlib import Path
from tests.test_strategy_research_runtime import NOW, _Source, _runtime, _work
from trading_agent.strategy_research_types import ResearchAgentId

class BlockingRunner:
    def run(self, work):
        print("science-cycle-active", flush=True)
        _ = sys.stdin.readline()
        raise KeyboardInterrupt

agent_id = ResearchAgentId.INTRADAY_MOMENTUM
source = _Source({agent_id: _work(agent_id, NOW)})
_runtime(Path(sys.argv[1]), source, BlockingRunner()).tick(NOW)
"""
    first = subprocess.Popen(
        (sys.executable, "-c", child_source, str(tmp_path)),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert first.stdout is not None
    assert first.stdout.readline().strip() == "science-cycle-active"
    before = ExperimentLedgerReader(tmp_path / "ledger.sqlite3").strategy_research_agent_state(
        ResearchAgentId.INTRADAY_MOMENTUM
    )
    runner = _Runner()

    try:
        # When: a second production tick targets the same durable research state.
        with pytest.raises(StrategyResearchRuntimeBusyError) as captured:
            _ = _runtime(
                tmp_path,
                _Source({ResearchAgentId.INTRADAY_MOMENTUM: _work(ResearchAgentId.INTRADAY_MOMENTUM, NOW)}),
                runner,
            ).tick(NOW + dt.timedelta(seconds=1))

        # Then: the loser reports typed contention without state or science mutation.
        after = ExperimentLedgerReader(tmp_path / "ledger.sqlite3").strategy_research_agent_state(
            ResearchAgentId.INTRADAY_MOMENTUM
        )
        assert captured.value.reason == "heavy_empirical_lease_busy"
        assert after == before
        assert runner.completed == []
    finally:
        assert first.stdin is not None
        first.stdin.close()
        _ = first.wait(timeout=10)


@pytest.mark.parametrize(
    ("rss_bytes", "blocked"),
    [
        (10 * 1024**3 - 1, False),
        (10 * 1024**3, True),
        (10 * 1024**3 + 1, True),
    ],
)
def test_science_cycle_enforces_ten_gib_rss_boundary_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rss_bytes: int,
    *,
    blocked: bool,
) -> None:
    # Given: the process RSS observer reports below, equal to, or above the 10 GiB boundary.
    rss_units = rss_bytes if sys.platform == "darwin" else rss_bytes // 1024
    monkeypatch.setattr(resource, "getrusage", lambda _who: SimpleNamespace(ru_maxrss=rss_units))
    agent_id = ResearchAgentId.INTRADAY_MOMENTUM
    runner = _Runner()
    runtime = _runtime(tmp_path, _Source({agent_id: _work(agent_id, NOW)}), runner)

    # When: the production runtime attempts to start the science cycle.
    if blocked:
        with pytest.raises(StrategyResearchRuntimeBusyError) as captured:
            _ = runtime.tick(NOW)
        assert captured.value.reason == "science_kernel_rss_limit_reached"
    else:
        _ = runtime.tick(NOW)

    # Then: only a strictly below-limit observation may enter or mutate science state.
    states = ExperimentLedgerReader(tmp_path / "ledger.sqlite3").strategy_research_agent_state(agent_id)
    assert (runner.completed, states == ()) == (([] if blocked else [agent_id]), blocked)


def test_duplicate_tick_does_not_repeat_completed_work(tmp_path: Path) -> None:
    # Given: one work item has completed and advanced its evidence cursor.
    source = _Source({ResearchAgentId.INTRADAY_MOMENTUM: _work(ResearchAgentId.INTRADAY_MOMENTUM, NOW)})
    runner = _Runner()
    runtime = _runtime(tmp_path, source, runner)
    _ = runtime.tick(NOW)

    # When: the exact tick is replayed.
    replay = runtime.tick(NOW)

    # Then: no attempt, feedback, report, or heavy invocation is duplicated.
    assert replay.heavy_cycles_started == 0
    assert runner.completed == [ResearchAgentId.INTRADAY_MOMENTUM]


def test_failure_isolated_and_next_agent_runs_before_retry(tmp_path: Path) -> None:
    # Given: the oldest due family fails once and another family is due.
    source = _Source(
        {
            agent_id: _work(agent_id, NOW)
            for agent_id in (ResearchAgentId.INTRADAY_MOMENTUM, ResearchAgentId.INTRADAY_MEAN_REVERSION)
        }
    )
    runner = _Runner(fail_once={ResearchAgentId.INTRADAY_MOMENTUM})

    # When: failure and the next 30-second outer tick are evaluated.
    failed = _runtime(tmp_path, source, runner).tick(NOW)
    progressed = _runtime(tmp_path, source, runner).tick(NOW + dt.timedelta(seconds=1))

    # Then: retry backoff isolates the failure and mean reversion progresses.
    assert failed.slot(ResearchAgentId.INTRADAY_MOMENTUM).state == "recovery_pending"
    assert progressed.heavy_agent_id is ResearchAgentId.INTRADAY_MEAN_REVERSION
    assert runner.completed == [ResearchAgentId.INTRADAY_MEAN_REVERSION]


def test_interrupted_started_work_is_audited_and_retried_idempotently(tmp_path: Path) -> None:
    # Given: the process is interrupted after durable STARTED state.
    source = _Source({ResearchAgentId.INTRADAY_MOMENTUM: _work(ResearchAgentId.INTRADAY_MOMENTUM, NOW)})
    runner = _Runner(interrupt_once={ResearchAgentId.INTRADAY_MOMENTUM})
    with pytest.raises(KeyboardInterrupt):
        _ = _runtime(tmp_path, source, runner).tick(NOW)

    # When: a new runtime instance starts against the same V9 ledger.
    recovered = _runtime(tmp_path, source, runner).tick(NOW + dt.timedelta(minutes=6))

    # Then: interruption is auditable and the immutable work completes once.
    events = ExperimentLedgerReader(tmp_path / "ledger.sqlite3").strategy_research_agent_state(
        ResearchAgentId.INTRADAY_MOMENTUM
    )
    assert "started" in tuple(event.state for event in events)
    assert "recovery_pending" in tuple(event.state for event in events)
    assert recovered.slot(ResearchAgentId.INTRADAY_MOMENTUM).state == "completed"
    assert runner.completed == [ResearchAgentId.INTRADAY_MOMENTUM]


@pytest.mark.parametrize(
    ("holdout", "observations", "purpose", "family_suffix"),
    [
        (0.03, 24, FeedbackWorkPurpose.FUTURE_REPLICATION, "replication"),
        (-0.03, 24, FeedbackWorkPurpose.NEW_LINEAGE_METHOD_CHANGE, "method-change"),
        (0.03, 19, FeedbackWorkPurpose.EVIDENCE_COMPLETION, "evidence"),
    ],
)
def test_terminal_feedback_drives_actual_next_tick_schedule(
    tmp_path: Path,
    holdout: float,
    observations: int,
    purpose: FeedbackWorkPurpose,
    family_suffix: str,
) -> None:
    # Given: one real terminal kernel result and a purpose-specific child work item.
    original = _terminal_work(ResearchAgentId.INTRADAY_MOMENTUM, holdout, observations)
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    _ = ScienceKernelCycleRunner(store).run(original)
    next_work = _work(ResearchAgentId.INTRADAY_MOMENTUM, NOW + dt.timedelta(hours=2))
    next_work = next_work.model_copy(
        update={
            "draft": next_work.draft.model_copy(
                update={
                    "hypothesis_id": f"hypothesis-momentum-{family_suffix}",
                    "parent_hypothesis_id": original.draft.hypothesis_id,
                    "search_family_id": f"family-momentum-{family_suffix}",
                }
            ),
            "feedback_purpose": purpose,
            "maturity_at": NOW + dt.timedelta(hours=3),
        }
    )
    source = _Source({ResearchAgentId.INTRADAY_MOMENTUM: next_work})
    runner = _Runner()

    # When: the production scheduler evaluates the sanitized directive.
    status = StrategyResearchRuntime(store, source, runner).tick(NOW + dt.timedelta(hours=1))
    completed = StrategyResearchRuntime(store, source, runner).tick(NOW + dt.timedelta(hours=4))

    # Then: outcome policy changes the durable slot state and next scheduling boundary.
    assert status.slot(ResearchAgentId.INTRADAY_MOMENTUM).state == "waiting_due"
    if purpose is not FeedbackWorkPurpose.NEW_LINEAGE_METHOD_CHANGE:
        assert status.slot(ResearchAgentId.INTRADAY_MOMENTUM).next_due_at == next_work.maturity_at
    else:
        next_due = status.slot(ResearchAgentId.INTRADAY_MOMENTUM).next_due_at
        assert next_due is not None and next_due < next_work.maturity_at
    assert completed.slot(ResearchAgentId.INTRADAY_MOMENTUM).state == "completed"
    assert runner.completed == [ResearchAgentId.INTRADAY_MOMENTUM]


def test_refuted_feedback_rejects_same_lineage_work_before_runner(tmp_path: Path) -> None:
    # Given: a refuted terminal result followed by an unmarked same-lineage work item.
    original = _terminal_work(ResearchAgentId.INTRADAY_MOMENTUM, -0.03)
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    _ = ScienceKernelCycleRunner(store).run(original)
    source = _Source({ResearchAgentId.INTRADAY_MOMENTUM: _work(ResearchAgentId.INTRADAY_MOMENTUM, NOW)})
    runner = _Runner()

    # When: the next runtime tick applies owner feedback before empirical execution.
    status = StrategyResearchRuntime(store, source, runner).tick(NOW + dt.timedelta(hours=1))

    # Then: the old lineage is durably closed and no heavy runner is invoked.
    assert status.slot(ResearchAgentId.INTRADAY_MOMENTUM).state == "waiting_feedback"
    assert runner.completed == []
    event = ExperimentLedgerReader(store.path).strategy_research_agent_state(ResearchAgentId.INTRADAY_MOMENTUM)[-1]
    assert event.reason == "feedback_refuted_lineage_closed"
