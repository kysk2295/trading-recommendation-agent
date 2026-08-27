# KR Operator Surface and Outcome Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Project the autonomous KR research, decision, and virtual-position lineage to Hermes and Dashboard while appending replay-safe outcome memories and repeated-failure Loop Engineer evidence bundles.

**Architecture:** Reuse schema-v4's durable task, social-signal, trade-event, virtual-position, KIS receipt, and autonomous-memory stores. A deterministic operator cycle reads those immutable sources after each Supervisor tick, appends versioned `market` and `self_improvement` memories, and projects only new domain event IDs to Hermes; Dashboard reads the same stores through a config-derived, query-only path bundle. This release creates Loop Engineer evidence bundles but does not edit code, promote a challenger, add a KR order path, or call a provider.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite append-only stores, existing Hermes delivery ledger, Dashboard v2 projections, KIS stored receipts, pytest, Ruff, basedpyright.

---

## Product and safety boundary

- This plan implements design subproject 12.3 only. Automatic code modification, challenger promotion, and rollback remain the next release described by section 13 of the approved design.
- All KIS access in this slice is query-only against already stored receipts. KIS/LS account, balance, order, and position-changing calls remain absent; Alpaca calls remain zero.
- Outcome memories describe virtual or unexecuted research outcomes and must never be rendered as real fills or profitability.
- Hermes and Dashboard expose bounded summaries, IDs, price levels, state, and next wake only. They never expose raw social text, full HTML, browser credentials, tokens, account identifiers, or authentication responses.
- The existing schema-v4 config remains valid. New stores are derived below `output_root/autonomous-supervisor`; no new required config fields are introduced.
- Every new or modified Python file must remain at or below 250 pure LOC and pass the official no-excuse checker.

## File map

- `trading_agent/kr_autonomous_outcome_models.py`: typed horizon observation and Loop Engineer bundle identities.
- `trading_agent/kr_autonomous_outcome_learning.py`: query-only source reduction into versioned autonomous memories.
- `trading_agent/kr_autonomous_operator_paths.py`: config-derived, query-only state bundle shared by service and Dashboard.
- `trading_agent/kr_autonomous_hermes.py`: state-change-only Hermes records for decisions, virtual positions, memories, and bundles.
- `trading_agent/dashboard_kr_autonomous_operator.py`: KR task/decision/position/learning items and lineage trace nodes.
- `trading_agent/research_agent_service_operations.py`: invoke the operator cycle after schema-v4 Supervisor work and before Hermes projection.
- `trading_agent/dashboard_snapshot_v2.py`: merge KR operator items into Markets, Research, and Paper.
- `run_dashboard_publisher.py`, `trading_agent/dashboard_publisher_runtime.py`, `trading_agent/dashboard_publisher_relay_runtime.py`, `trading_agent/dashboard_publisher_events.py`: carry the config-derived KR operator binding through initial, reconnect, and filesystem-event snapshots.
- `tests/test_kr_autonomous_outcome_learning.py`: outcome versions, horizons, replay, and bundle threshold.
- `tests/test_kr_autonomous_hermes.py`: state-change dedupe, rendered action/levels/next wake, and redaction.
- `tests/test_dashboard_kr_autonomous_operator.py`: same-lineage query-only Dashboard projection.
- `tests/test_research_agent_service_kr_operator_cycle.py`: schema-v4 service hook and schema-v2 no-op.
- `tests/test_dashboard_publisher_kr_autonomous_runtime.py`: initial/reconnect/event relay path propagation.
- `docs/checkpoints/2026-08-27-kr-operator-surface-outcome-learning-ko.md`: exact verification and remaining live-session evidence.

### Task 1: Append versioned KR outcome memories

**Files:**
- Create: `trading_agent/kr_autonomous_outcome_models.py`
- Create: `trading_agent/kr_autonomous_outcome_learning.py`
- Create: `trading_agent/kr_autonomous_operator_paths.py`
- Create: `tests/test_kr_autonomous_outcome_learning.py`

- [x] **Step 1: Write failing typed outcome tests**

Build one recommendation with an `ARMED -> ACTIVE -> STOPPED` virtual chain and stored KIS minute receipts. Assert the first operator cycle appends a `market` memory whose subject refs include symbol, theme digest, source clusters, and verification state; its canonical summary records `virtual_stopped`, entry/stop/targets, and only horizons observable at `now`. Advance `now` to 5, 15, and 30 minutes and assert each newly observable horizon appends exactly one new memory version. Replay the same time and assert zero inserts.

```python
result = observe_kr_autonomous_outcomes(paths, now=NOW + dt.timedelta(minutes=5))
history = AutonomousMemoryStore(paths.memory_database).reader().history(result.memory_keys[0])
payload = KrAutonomousOutcomeMemory.model_validate_json(history[-1].summary)
assert result.inserted_memories == 1
assert payload.execution_state is KrOutcomeExecutionState.VIRTUAL_STOPPED
assert tuple(item.horizon for item in payload.horizons) == (KrOutcomeHorizon.MINUTES_5,)
assert history[-1].scope is AutonomousMemoryScope.MARKET
```

- [x] **Step 2: Run the focused test and confirm RED**

Run: `uv run pytest -q tests/test_kr_autonomous_outcome_learning.py`

Expected: collection fails because the outcome modules do not exist.

- [x] **Step 3: Implement typed identities and query-only observation**

Define exhaustive enums for `NO_TRADE`, `REJECTED`, `VIRTUAL_ARMED`, `VIRTUAL_ACTIVE`, `VIRTUAL_STOPPED`, `VIRTUAL_TARGETED`, `VIRTUAL_EXPIRED`, and `VIRTUAL_CENSORED`; define `5m`, `15m`, `30m`, and `close` horizons. `KrAutonomousOutcomeMemory` carries the source trade event, task, symbol, theme, verification state, independent clusters, price levels when present, terminal reason, bounded horizon returns in bps, exact evidence refs, and an identity hash.

`observe_kr_autonomous_outcomes()` reads only existing stores. For every immutable trade decision, it finds the task-bound social signal and latest matching virtual event, projects stored minute receipts no later than `now`, and appends an `AutonomousMemoryRecord` under `market.kr.<symbol>.<trade-event-prefix>`. The version is `latest.version + 1`; identical summaries replay without mutation. Missing or incomplete KIS receipts leave horizons absent rather than inventing data.

```python
record = AutonomousMemoryRecord(
    memory_key=outcome_memory_key(outcome),
    version=1 if latest is None else latest.version + 1,
    scope=AutonomousMemoryScope.MARKET,
    summary=canonical_kr_autonomous_outcome_json(outcome),
    fact_refs=tuple(sorted(outcome.fact_refs)),
    inference_refs=(),
    subject_refs=outcome.subject_refs,
    evidence_refs=outcome.evidence_refs,
    source_task_ids=(AutonomousTaskId(outcome.task_id),),
    recorded_at=now,
)
```

- [x] **Step 4: Add three-event Loop Engineer bundle threshold**

Group the latest outcome memories by deterministic failure class. `STOPPED`, repeated chronology/cluster Critic rejection, repeated stale/missing market evidence, and censored bar gaps qualify; duplicate-exposure and ordinary no-trade do not. At three distinct source outcomes, append a `SELF_IMPROVEMENT` memory whose summary is a typed `KrLoopEngineerEvidenceBundle` containing source memory IDs, task IDs, evidence IDs, failure classification, affected subject refs, and a bounded change hypothesis. Further distinct failures append versions; exact replay inserts nothing.

```python
assert first_two.inserted_bundles == 0
assert third.inserted_bundles == 1
bundle = KrLoopEngineerEvidenceBundle.model_validate_json(
    memory.reader().latest("self_improvement.kr.virtual_stop.symbol-005930").summary
)
assert len(bundle.source_memory_ids) == 3
assert bundle.code_mutation_authority is False
```

- [x] **Step 5: Run Task 1 GREEN**

Run: `uv run pytest -q tests/test_kr_autonomous_outcome_learning.py`

Expected: all Task 1 tests pass, including replay and three-event threshold.

### Task 2: Publish KR state changes through Hermes

**Files:**
- Create: `trading_agent/kr_autonomous_hermes.py`
- Create: `tests/test_kr_autonomous_hermes.py`
- Modify: `trading_agent/research_agent_service_operations.py`
- Create: `tests/test_research_agent_service_kr_operator_cycle.py`

- [x] **Step 1: Write failing Hermes and service-hook tests**

Assert recommendation text includes the symbol, explicit `가상`, entry, stop, both targets, rationale, and next validity/wake; no-trade includes reasons and next wake; virtual events include state and virtual fill/exit; learning includes horizon/terminal classification; bundle includes the repeated-failure trigger and no code authority. Assert a second projection inserts zero events. Assert schema v4 runs outcome observation before projection while schema v2 keeps the existing behavior.

```python
first = project_kr_autonomous_state(paths, writer, projected_source_ids=frozenset())
second = project_kr_autonomous_state(
    paths,
    writer,
    projected_source_ids=frozenset(event.source_event_id for event in reader.events()),
)
assert first.inserted == 4
assert second.inserted == 0
assert "가상" in reader.events()[0].rendered_text
```

- [x] **Step 2: Run Task 2 tests and confirm RED**

Run: `uv run pytest -q tests/test_kr_autonomous_hermes.py tests/test_research_agent_service_kr_operator_cycle.py`

Expected: collection fails because the Hermes projector is absent.

- [x] **Step 3: Implement state-change records and service orchestration**

Build one `HermesProjectionRecord` per trade event, virtual-position event, and autonomous outcome/bundle memory. Use the immutable domain event or memory ID as `source_event_id`; this makes the existing delivery ledger the dedupe authority. Render bounded Korean action summaries through `redact_outbound_text()` and `require_safe_outbound_text()`. Use `HermesDeliveryKind.NO_RECOMMENDATION` for no-trade/rejected, `ACTIONABLE` for virtual recommendations, `EXIT` for terminal virtual positions, `RESEARCH` for learning, and `INCIDENT` for Loop Engineer bundles.

In `_project_results()`, schema v4 first calls `observe_kr_autonomous_outcomes(configured_paths, now)` and then projects both existing research results and KR state under one short Hermes writer lease. Return the sum of inserted deliveries. Keep schema v2/v3 behavior byte-for-byte equivalent.

- [x] **Step 4: Run Task 2 GREEN**

Run: `uv run pytest -q tests/test_kr_autonomous_hermes.py tests/test_research_agent_service_kr_operator_cycle.py tests/test_research_agent_hermes.py tests/test_research_agent_service_runtime.py`

Expected: all selected tests pass and replay inserts zero KR deliveries.

### Task 3: Show the same lineage in Dashboard v2

**Files:**
- Create: `trading_agent/dashboard_kr_autonomous_operator.py`
- Create: `tests/test_dashboard_kr_autonomous_operator.py`
- Modify: `trading_agent/dashboard_snapshot_v2.py`
- Modify: `run_dashboard_publisher.py`
- Modify: `trading_agent/dashboard_publisher_runtime.py`
- Modify: `trading_agent/dashboard_publisher_relay_runtime.py`
- Modify: `trading_agent/dashboard_publisher_events.py`
- Create: `tests/test_dashboard_publisher_kr_autonomous_runtime.py`

- [x] **Step 1: Write failing projection and runtime-binding tests**

Seed one task, recommendation, active and terminal virtual events, outcome memory, and Loop Engineer bundle. Assert Markets shows the recommendation with entry/stop/targets and verification state; Paper shows explicitly virtual position state/result; Research shows current task/next wake, outcome memory, and bundle. Assert trace edges connect task -> decision -> position -> outcome -> bundle. Assert missing paths produce no fabricated populated state and unsafe/corrupt stores fail closed.

```python
projection = project_kr_autonomous_operator(paths, now=NOW)
assert {item.item_id for item in projection.markets.items} == {f"kr-decision-{RECOMMENDATION.event_id[:24]}"}
assert "entry=" in projection.markets.items[0].value
assert projection.paper.items[0].label.startswith("KR 가상")
assert any(edge.kind == "executed_as" for edge in projection.edges)
assert any(edge.kind == "evaluated_in" for edge in projection.edges)
```

- [x] **Step 2: Run Task 3 tests and confirm RED**

Run: `uv run pytest -q tests/test_dashboard_kr_autonomous_operator.py tests/test_dashboard_publisher_kr_autonomous_runtime.py`

Expected: collection fails because the Dashboard projector and binding do not exist.

- [x] **Step 3: Implement bounded items and lineage trace**

Create a frozen `KrAutonomousDashboardProjection` containing Markets, Research, Paper items plus trace nodes/edges. Cap each view deterministically, preserve total/projected counts, prefix all virtual values with `virtual`, and include only safe digests in trace refs. Merge into existing workspace states without adding a top-level route or frontend schema variant.

`run_dashboard_publisher.py` loads the selected Research Agent config once and derives both `cycle_database` and optional schema-v4 `KrAutonomousOperatorPaths`. Thread that single typed binding through initial snapshot, reconnect, and filesystem-event snapshots. Add the schema-v4 output root to `watch_roots()` so state changes trigger a snapshot without polling.

- [x] **Step 4: Run Task 3 GREEN**

Run: `uv run pytest -q tests/test_dashboard_kr_autonomous_operator.py tests/test_dashboard_publisher_kr_autonomous_runtime.py tests/test_dashboard_snapshot_v2.py tests/test_dashboard_publisher_kr_state_root.py tests/test_dashboard_publisher_cli.py`

Expected: all selected tests pass; snapshot JSON validates against the existing v2 model.

### Task 4: Complete vertical verification and checkpoint

**Files:**
- Create: `tests/test_kr_autonomous_operator_vertical.py`
- Create: `docs/checkpoints/2026-08-27-kr-operator-surface-outcome-learning-ko.md`
- Modify: `docs/superpowers/plans/2026-08-27-kr-operator-surface-outcome-learning.md`

- [x] **Step 1: Add one complete fixture vertical**

Run the existing 12.2 fixture through recommendation, future virtual fill, terminal outcome, operator cycle, Hermes projection, Dashboard snapshot, and process-restart replay. Assert the exact recommendation/task/position/memory IDs join across all surfaces, restart duplicates are zero, the third repeated failure creates one Loop Engineer bundle, and KIS/LS/Alpaca mutation calls remain zero.

- [x] **Step 2: Run focused and broad automated gates**

Run:

```bash
uv run pytest -q tests/test_kr_autonomous_operator_vertical.py tests/test_kr_autonomous_outcome_learning.py tests/test_kr_autonomous_hermes.py tests/test_dashboard_kr_autonomous_operator.py tests/test_research_agent_service_kr_operator_cycle.py tests/test_dashboard_publisher_kr_autonomous_runtime.py
uv run pytest -q tests/test_kr_* tests/test_autonomous_kr_* tests/test_research_agent_service_kr_* tests/test_dashboard_* tests/test_research_agent_hermes.py
uv run ruff format --check <changed-python-files>
uv run ruff check <changed-python-files>
uv run basedpyright <changed-python-files>
uv run scripts/python/check-no-excuse-rules.py <changed-python-files>
git diff --check
```

Expected: all new and relevant regression tests pass. Any unchanged baseline failure must be reproduced at `ff1cf6b` before being classified as pre-existing.

- [ ] **Step 3: Run the manual user surface**

Run CLI help, one nonexistent config, and a fixture-backed schema-v4 tick. Then run Dashboard `--dry-run` against the fixture config and query the temporary Hermes delivery DB. Confirm the same safe IDs, action, price levels, position state, learning version, Loop Engineer trigger, and next wake are visible; inspect the output for credential/token/cookie/account/full-HTML patterns and require zero matches.

- [ ] **Step 4: Record honest operating evidence**

Write the checkpoint with exact SHA, test commands, redacted sample states, replay counts, broker/trading mutations `0`, and secrets `0`. State explicitly that fixture/replay/virtual output is not profitability and that a natural open-KRX-session recommendation/no-trade remains an external operating observation until it occurs.

## Self-review record

- Spec coverage: Task 1 covers outcome memory and repeated-failure bundles; Task 2 covers state-change Hermes; Task 3 covers same-lineage Dashboard and live event watching; Task 4 covers restart replay, user surfaces, and the operating checkpoint.
- Scope separation: automatic code editing, challenger shadow, promotion, and rollback are not silently pulled into this release because the approved design explicitly sequences them after the KR operator/outcome vertical.
- Type consistency: the same `task_id`, trade `event_id`, position `event_id`, autonomous `memory_id`, and bundle `memory_id` are the join keys across stores, Hermes, Dashboard, and tests.
- Safety: stored KIS receipts are query-only; KIS/LS mutation and all Alpaca calls remain zero; every rendered trade state says virtual.
- Placeholder scan: the plan contains no deferred implementation markers; each task names exact files, RED/GREEN commands, identities, and observable acceptance behavior.
