# Autonomous Trading Supervisor Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the production one-shot family decision path with a restart-safe Autonomous Trading Supervisor that preserves source lineage, runs multi-step host-tool loops, stores real long-term memory, and always leaves unfinished work with a deterministic wake.

**Architecture:** Add generic autonomous task, memory, reasoning, tool, and runtime contracts beside the proven DayAgent runtime rather than expanding the legacy cycle decision model. A production adapter converts existing `ResearchAgentEvidenceV1` into durable supervisor tasks and projects each bounded supervisor tick back into the existing cycle/audit/Hermes boundary. The current launchd-owned Research OS service remains the single process owner, but `build_service_runtime()` always installs the new supervisor so the legacy one-call client is no longer used in production.

**Tech Stack:** Python 3.12, Pydantic v2 frozen models, SQLite append-only private stores, existing `ResearchAgentEvidenceV1` inbox, existing Hermes/Claude structured completion client, anyio foreground service, launchd, pytest, Ruff, basedpyright

---

## Program decomposition

The approved design spans five independently testable releases. Implement them in this order and write a separate plan before starting each later release.

1. **This plan: Supervisor foundation.** Durable task and memory stores, multi-step tool loop, source-lineage retry, production service wiring, launchd continuity, and status reporting.
2. **KR natural-market vertical.** Market Observer, Opportunity, Trading, Position, Research and Critic tools over current KRX-session evidence plus the internal virtual-fill ledger.
3. **US Alpaca Paper vertical.** Current completed XNYS bar, thesis, Paper admission, order/position reconciliation, and same-day flatten through the existing exact Paper guard.
4. **Loop Engineer.** Evidence-bundle trigger, isolated worktree, code edit, tests/replay, challenger shadow, automatic Paper-only promotion and rollback.
5. **Operator surface and cutover.** Full dashboard lineage, Computer Use/browser bindings, legacy actor shutdown, 24-hour restart soak, and five-session Forward Shadow.

This first release is complete only when production `build_service_runtime()` delegates every primary family to the persistent supervisor, a repeated failure retains the original source evidence, and a no-trade/no-action result produces a future wake instead of a terminal actor.

## File structure

### New production files

- `trading_agent/autonomous_task_models.py`: generic roles, states, wakes, run budgets, task/step identities, and result contract.
- `trading_agent/autonomous_task_store.py`: append-only private SQLite task and step authority with runnable-task queries.
- `trading_agent/autonomous_memory_models.py`: work, market, strategy, and self-improvement memory records.
- `trading_agent/autonomous_memory_store.py`: append-only memory versions and bounded evidence-linked search.
- `trading_agent/autonomous_reasoning.py`: next-step request/response protocol and structured LLM adapter.
- `trading_agent/autonomous_tool_runtime.py`: allowlisted host-tool bindings and bounded observations.
- `trading_agent/autonomous_supervisor_runtime.py`: restart-safe multi-step state machine and retry scheduling.
- `trading_agent/autonomous_supervisor_adapter.py`: existing family evidence-to-task admission and cycle-result projection.
- `trading_agent/autonomous_supervisor_service.py`: production store paths, reasoner, foundation tools, status and builder.

### Existing production files to modify

- `trading_agent/research_agent_runtime.py:28-61,184-273`: replace the Day-only persistence hook with the generic supervisor hook before legacy decisions.
- `trading_agent/research_agent_service_runtime.py:101-180,358-398`: build and report the production supervisor.
- `trading_agent/research_os_runtime.py:32-99`: include supervisor status in each foreground tick and persisted status artifact.
- `trading_agent/research_agent_service_config.py:179-201`: preserve the existing KeepAlive launchd owner and prove no Codex process dependency is introduced.

### New and modified tests

- Create `tests/test_autonomous_task_models.py`
- Create `tests/test_autonomous_task_store.py`
- Create `tests/test_autonomous_memory_store.py`
- Create `tests/test_autonomous_reasoning.py`
- Create `tests/test_autonomous_tool_runtime.py`
- Create `tests/test_autonomous_supervisor_runtime.py`
- Create `tests/test_autonomous_supervisor_adapter.py`
- Create `tests/test_autonomous_supervisor_service.py`
- Modify `tests/test_research_agent_runtime.py`
- Modify `tests/test_research_agent_service_runtime.py`
- Modify `tests/test_research_os_runtime.py`
- Modify `tests/test_research_agent_service_cli.py`

### Files deliberately unchanged in this release

- Alpaca Paper clients, risk kernel, mutation gate, OCO, flatten, and reconciliation code.
- KIS and LS provider adapters.
- KR virtual-fill and US Paper vertical implementations.
- Dashboard React components and public publisher.
- Legacy cycle tables and historic results; they remain the outward audit bridge during migration.

## Task 1: Define generic autonomous task contracts

**Files:**
- Create: `trading_agent/autonomous_task_models.py`
- Create: `tests/test_autonomous_task_models.py`

- [ ] **Step 1: Write failing state, wake, identity and lineage tests**

```python
def test_waiting_and_blocked_tasks_require_a_future_wake() -> None:
    for state in (AutonomousTaskState.WAITING_TIME, AutonomousTaskState.BLOCKED):
        with pytest.raises(InvalidAutonomousTaskFieldError):
            task_fixture(state=state, next_wake_at=None, next_wake_event=None)


def test_no_trade_is_not_a_terminal_task_state() -> None:
    task = task_fixture(state=AutonomousTaskState.WAITING_EVENT, next_wake_event="market_evidence")
    assert task.terminal_reason is None
    assert task.state not in {AutonomousTaskState.COMPLETED, AutonomousTaskState.ABANDONED}


def test_task_identity_binds_root_source_evidence() -> None:
    first = autonomous_task_id("day_trading", "kr_equities", EvidenceId("a" * 64))
    assert first == autonomous_task_id("day_trading", "kr_equities", EvidenceId("a" * 64))
    assert first != autonomous_task_id("day_trading", "kr_equities", EvidenceId("b" * 64))
```

- [ ] **Step 2: Run the focused tests and verify the missing-module failure**

Run: `uv run pytest -q tests/test_autonomous_task_models.py`

Expected: collection fails with `ModuleNotFoundError: trading_agent.autonomous_task_models`.

- [ ] **Step 3: Implement strict roles, states, wakes and per-tick budgets**

```python
@unique
class AutonomousAgentRole(StrEnum):
    SUPERVISOR = "supervisor"
    MARKET_OBSERVER = "market_observer"
    OPPORTUNITY = "opportunity"
    TRADING = "trading"
    POSITION = "position"
    RESEARCH = "research"
    CRITIC = "critic"
    LOOP_ENGINEER = "loop_engineer"


@unique
class AutonomousTaskState(StrEnum):
    QUEUED = "queued"
    OBSERVING = "observing"
    RESEARCHING = "researching"
    DELIBERATING = "deliberating"
    ACTING = "acting"
    WAITING_EVENT = "waiting_event"
    WAITING_TIME = "waiting_time"
    BLOCKED = "blocked"
    EVALUATING = "evaluating"
    LEARNING = "learning"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class AutonomousRunBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    remaining_model_calls: int = Field(ge=0, le=12)
    remaining_tool_calls: int = Field(ge=0, le=24)
    remaining_runtime_seconds: int = Field(ge=0, le=300)
```

Use `MarketId` and `EvidenceId` from `research_agent_cycle_models.py`. Define `AutonomousTaskId` and `AutonomousStepId` as `NewType` hashes.

- [ ] **Step 4: Implement task, step and tick-result models**

```python
class AutonomousResearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    task_id: AutonomousTaskId = Field(pattern=r"^[a-f0-9]{64}$")
    goal: str = Field(min_length=8, max_length=2_000)
    owner_role: AutonomousAgentRole
    agent_family_id: AgentFamilyId
    market_scope: MarketId
    state: AutonomousTaskState
    priority: int = Field(ge=0, le=100)
    root_source_evidence_id: EvidenceId = Field(pattern=r"^[a-f0-9]{64}$")
    source_evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1, max_length=128)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    subject_refs: tuple[str, ...] = Field(default=(), max_length=32)
    working_memory_ids: tuple[str, ...] = Field(default=(), max_length=128)
    current_plan: tuple[str, ...] = Field(min_length=1, max_length=32)
    completed_actions: tuple[str, ...] = Field(default=(), max_length=128)
    pending_actions: tuple[str, ...] = Field(default=(), max_length=128)
    next_wake_at: AwareDatetime | None = None
    next_wake_event: str | None = Field(default=None, max_length=160)
    blocked_reason: str | None = Field(default=None, max_length=160)
    retry_count: int = Field(default=0, ge=0)
    agent_version: str = Field(min_length=1, max_length=160)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    terminal_reason: str | None = Field(default=None, max_length=160)
```

Define `AutonomousTaskStep` with `sequence`, `role`, `state`, canonical `payload_json`, source/evidence/memory refs, per-tick budget, occurred time and wake fields. Define `AutonomousSupervisorTickResult` with `status: Literal["idle", "waiting", "completed", "blocked", "failed"]`, task ID, family, market, model/tool call counts and next wake.

Model invariants must enforce sorted unique references, aware UTC times, immutable root evidence, exactly one wake selector for waiting/blocked tasks, no wake for completed/abandoned tasks, a terminal reason only for terminal states, and no terminalization for a no-trade/no-action artifact.

- [ ] **Step 5: Add deterministic identity helpers and pass tests**

```python
def autonomous_task_id(family: AgentFamilyId, market: MarketId, root: EvidenceId) -> AutonomousTaskId:
    material = f"{family}:{market}:{root}:autonomous-task-v1"
    return AutonomousTaskId(hashlib.sha256(material.encode()).hexdigest())


def autonomous_step_id(step: AutonomousTaskStep) -> AutonomousStepId:
    return AutonomousStepId(hashlib.sha256(autonomous_step_payload(step).encode()).hexdigest())
```

Run: `uv run pytest -q tests/test_autonomous_task_models.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add trading_agent/autonomous_task_models.py tests/test_autonomous_task_models.py
git commit -m "feat: define autonomous supervisor task contracts"
```

## Task 2: Persist restart-safe tasks and exact retry lineage

**Files:**
- Create: `trading_agent/autonomous_task_store.py`
- Create: `tests/test_autonomous_task_store.py`

- [ ] **Step 1: Write failing append-only, runnable and retry tests**

```python
def test_retry_keeps_root_source_evidence_after_multiple_failures(tmp_path: Path) -> None:
    store = AutonomousTaskStore(tmp_path / "tasks.sqlite3")
    task = task_fixture(root_source_evidence_id=EvidenceId("a" * 64))
    with store.writer() as writer:
        assert writer.create_task(task)
        assert writer.append_step(failure_step(task, sequence=1, retry_count=1))
        assert writer.append_step(failure_step(task, sequence=2, retry_count=2))
    projected = store.reader().task(task.task_id)
    assert projected is not None
    assert projected.root_source_evidence_id == EvidenceId("a" * 64)
    assert projected.source_evidence_ids[0] == EvidenceId("a" * 64)


def test_waiting_task_becomes_runnable_only_at_its_wake(tmp_path: Path) -> None:
    store = seeded_store(tmp_path, state=AutonomousTaskState.WAITING_TIME, wake=NOW)
    assert store.reader().runnable(NOW - dt.timedelta(microseconds=1), events=()) == ()
    assert len(store.reader().runnable(NOW, events=())) == 1
```

Also cover exact replay, changed-payload conflict, step sequence conflicts, terminal append rejection, mode `600`, symlink rejection, second-writer lease rejection, restart projection and event wake matching.

- [ ] **Step 2: Run tests and verify they fail because the store is absent**

Run: `uv run pytest -q tests/test_autonomous_task_store.py`

Expected: collection fails with `ModuleNotFoundError: trading_agent.autonomous_task_store`.

- [ ] **Step 3: Implement schema version 1 and append-only triggers**

```python
_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE autonomous_tasks (
  task_id TEXT PRIMARY KEY,
  root_source_evidence_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE autonomous_task_steps (
  step_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES autonomous_tasks(task_id),
  sequence INTEGER NOT NULL,
  occurred_at TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(task_id, sequence)
);
CREATE TRIGGER autonomous_tasks_no_update BEFORE UPDATE ON autonomous_tasks
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER autonomous_tasks_no_delete BEFORE DELETE ON autonomous_tasks
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER autonomous_steps_no_update BEFORE UPDATE ON autonomous_task_steps
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER autonomous_steps_no_delete BEFORE DELETE ON autonomous_task_steps
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
```

Use the same private-directory, generation replacement, query-only reader and `fcntl` lease invariants as `day_agent_task_store.py`; do not import or mutate its Day-specific schema.

- [ ] **Step 4: Implement the reader and writer API**

```python
@final
class AutonomousTaskReader:
    def task(self, task_id: AutonomousTaskId) -> AutonomousResearchTask | None:
        raise NotImplementedError

    def steps(self, task_id: AutonomousTaskId) -> tuple[AutonomousTaskStep, ...]:
        raise NotImplementedError

    def tasks(self) -> tuple[AutonomousResearchTask, ...]:
        raise NotImplementedError

    def matching_open_tasks(
        self,
        family: AgentFamilyId,
        market: MarketId,
        subject_refs: tuple[str, ...],
    ) -> tuple[AutonomousResearchTask, ...]:
        raise NotImplementedError

    def runnable(
        self,
        now: dt.datetime,
        *,
        events: tuple[str, ...],
    ) -> tuple[AutonomousResearchTask, ...]:
        raise NotImplementedError


@final
class AutonomousTaskWriter:
    def create_task(self, task: AutonomousResearchTask) -> bool:
        raise NotImplementedError

    def append_step(self, step: AutonomousTaskStep) -> bool:
        raise NotImplementedError
```

`matching_open_tasks()` returns nonterminal tasks with the same family and market and a nonempty intersection with `subject_refs`, ordered by newest `updated_at` then `task_id`. An empty incoming subject set never attaches to an existing task. `runnable()` projects every task from its immutable root plus append-only steps, selects queued/active tasks and matching time/event wakes, then sorts by `(-priority, next_wake_at or created_at, task_id)`. Projection may add source evidence and memory references but must reject any step that changes `root_source_evidence_id`, family, market or agent version retroactively.

- [ ] **Step 5: Pass the complete task-store test matrix**

Run: `uv run pytest -q tests/test_autonomous_task_store.py tests/test_day_agent_task_store.py`

Expected: PASS; the new store does not regress the existing Day store.

- [ ] **Step 6: Commit Task 2**

```bash
git add trading_agent/autonomous_task_store.py tests/test_autonomous_task_store.py
git commit -m "feat: persist autonomous supervisor tasks"
```

## Task 3: Add evidence-linked long-term memory

**Files:**
- Create: `trading_agent/autonomous_memory_models.py`
- Create: `trading_agent/autonomous_memory_store.py`
- Create: `tests/test_autonomous_memory_store.py`

- [ ] **Step 1: Write failing versioning and search tests**

```python
def test_memory_versions_are_append_only_and_source_linked(tmp_path: Path) -> None:
    store = AutonomousMemoryStore(tmp_path / "memory.sqlite3")
    first = memory_fixture(scope=AutonomousMemoryScope.MARKET, version=1, evidence_refs=("evidence.kr.1",))
    second = memory_fixture(scope=AutonomousMemoryScope.MARKET, version=2, evidence_refs=("evidence.kr.2",))
    with store.writer() as writer:
        assert writer.append(first)
        assert writer.append(second)
    assert store.reader().history(first.memory_key) == (first, second)


def test_search_is_bounded_by_scope_subject_and_limit(tmp_path: Path) -> None:
    store = seeded_memory_store(tmp_path)
    found = store.reader().search(
        scope=AutonomousMemoryScope.STRATEGY,
        subject_refs=("strategy.momentum",),
        limit=3,
    )
    assert len(found) <= 3
    assert all(item.scope is AutonomousMemoryScope.STRATEGY for item in found)
```

- [ ] **Step 2: Verify the memory tests are red**

Run: `uv run pytest -q tests/test_autonomous_memory_store.py`

Expected: collection fails because the memory modules do not exist.

- [ ] **Step 3: Implement four memory scopes and deterministic version IDs**

```python
@unique
class AutonomousMemoryScope(StrEnum):
    WORK = "work"
    MARKET = "market"
    STRATEGY = "strategy"
    SELF_IMPROVEMENT = "self_improvement"


class AutonomousMemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    memory_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    memory_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")
    version: int = Field(ge=1)
    scope: AutonomousMemoryScope
    summary: str = Field(min_length=8, max_length=4_000)
    fact_refs: tuple[str, ...] = Field(default=(), max_length=64)
    inference_refs: tuple[str, ...] = Field(default=(), max_length=64)
    subject_refs: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    source_task_ids: tuple[AutonomousTaskId, ...] = Field(min_length=1, max_length=32)
    recorded_at: AwareDatetime
```

Require at least one `fact_ref` or `inference_ref`, sorted unique refs, sequential versions per key, and a content hash covering every field except `memory_id`.

- [ ] **Step 4: Implement the private append-only memory store**

Use tables `autonomous_memories(memory_id, memory_key, version, scope, recorded_at, payload_sha256, payload_json)` and unique `(memory_key, version)`, plus no-update/no-delete triggers. Expose `append`, `latest`, `history`, and `search(scope, subject_refs, limit)`; validate all persisted JSON before returning it and cap search at 32 rows.

- [ ] **Step 5: Pass tests and commit**

Run: `uv run pytest -q tests/test_autonomous_memory_store.py`

Expected: PASS.

```bash
git add trading_agent/autonomous_memory_models.py trading_agent/autonomous_memory_store.py tests/test_autonomous_memory_store.py
git commit -m "feat: add autonomous agent long-term memory"
```

## Task 4: Define structured reasoning and allowlisted host tools

**Files:**
- Create: `trading_agent/autonomous_reasoning.py`
- Create: `trading_agent/autonomous_tool_runtime.py`
- Create: `tests/test_autonomous_reasoning.py`
- Create: `tests/test_autonomous_tool_runtime.py`

- [ ] **Step 1: Write failing response and authority tests**

```python
def test_reasoner_can_request_multiple_host_actions_across_steps() -> None:
    response = AutonomousToolCall(
        tool_name="evidence.read",
        arguments={"evidence_id": "a" * 64},
        reason="The root evidence must be inspected before a conclusion.",
    )
    assert response.kind == "tool_call"


def test_unregistered_or_extra_argument_tool_call_is_denied() -> None:
    runtime = AutonomousToolRuntime(bindings=(evidence_binding(),), clock=lambda: NOW)
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_authority_denied"):
        runtime.dispatch(tool_call("browser.control", {"url": "https://example.com"}))
```

Also test bounded JSON, output hashing, secret-key name rejection, duplicate binding rejection, role authority, invalid structured responses, explicit defer wake and completion requiring evidence.

- [ ] **Step 2: Verify the new tests fail**

Run: `uv run pytest -q tests/test_autonomous_reasoning.py tests/test_autonomous_tool_runtime.py`

Expected: collection fails because both modules are absent.

- [ ] **Step 3: Implement the discriminated next-step contract**

```python
class AutonomousToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: Literal["tool_call"] = "tool_call"
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,63}$")
    arguments: dict[str, str] = Field(max_length=8)
    reason: str = Field(min_length=8, max_length=500)


class AutonomousDelegate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: Literal["delegate"] = "delegate"
    role: AutonomousAgentRole
    objective: str = Field(min_length=8, max_length=2_000)
    reason: str = Field(min_length=8, max_length=500)


class AutonomousSubmitArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: Literal["submit_artifact"] = "submit_artifact"
    artifact_kind: Literal["context", "hypothesis", "recommendation", "no_trade", "review"]
    artifact_json: str = Field(min_length=2, max_length=16_384)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    next_wake_at: AwareDatetime | None = None
    next_wake_event: str | None = None
    reason: str = Field(min_length=8, max_length=500)
```

Add `AutonomousRecordMemory`, `AutonomousDefer`, and `AutonomousComplete`. `AutonomousComplete` requires `completion_evidence_refs`; `no_trade` requires a time or event wake and can never complete a task. Define `AutonomousReasoningRequest` with task, last 32 steps, last 16 observations, up to 16 retrieved memories, allowed tools, and current per-tick budget.

- [ ] **Step 4: Implement the host-tool runtime**

```python
@dataclass(frozen=True, slots=True)
class AutonomousToolBinding:
    name: str
    allowed_roles: frozenset[AutonomousAgentRole]
    allowed_arguments: frozenset[str]
    invoke: Callable[[Mapping[str, str]], str]


@final
class AutonomousToolRuntime:
    @property
    def allowed_tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._bindings))

    def dispatch(
        self,
        role: AutonomousAgentRole,
        call: AutonomousToolCall,
    ) -> AutonomousToolObservation:
        binding = self._authorized_binding(role, call)
        return self._bounded_observation(call, binding.invoke(call.arguments))
```

Canonicalize and hash every observation, cap it at 16 KiB, and reject argument keys matching `key`, `secret`, `token`, `password`, `authorization`, `account`, or `credential` before invoking the binding. Tool failures return a typed runtime error without exposing raw exception text.

- [ ] **Step 5: Implement the structured LLM adapter**

`AutonomousStructuredReasoner` accepts the existing `LlmProposalClient`, renders one canonical prompt containing the task, prior steps, bounded memories, allowed tool names and the Pydantic response schema, and parses exactly one `AutonomousReasoningResponse`. It must not depend on Claude session persistence because durable state is supplied from the stores on every call.

```python
@dataclass(frozen=True, slots=True)
class AutonomousStructuredReasoner:
    client: LlmProposalClient

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        prompt = canonical_reasoning_prompt(request, _RESPONSE_ADAPTER.json_schema())
        return _RESPONSE_ADAPTER.validate_json(self.client.complete(prompt))
```

- [ ] **Step 6: Pass tests and commit**

Run: `uv run pytest -q tests/test_autonomous_reasoning.py tests/test_autonomous_tool_runtime.py`

Expected: PASS.

```bash
git add trading_agent/autonomous_reasoning.py trading_agent/autonomous_tool_runtime.py tests/test_autonomous_reasoning.py tests/test_autonomous_tool_runtime.py
git commit -m "feat: add autonomous reasoning and host tools"
```

## Task 5: Implement the restart-safe multi-step supervisor runtime

**Files:**
- Create: `trading_agent/autonomous_supervisor_runtime.py`
- Create: `tests/test_autonomous_supervisor_runtime.py`

- [ ] **Step 1: Write failing multi-step, defer and recovery tests**

```python
def test_one_tick_runs_observe_research_critic_and_defer_steps(tmp_path: Path) -> None:
    runtime = runtime_fixture(
        tmp_path,
        responses=(
            tool_response("evidence.read"),
            delegate_response(AutonomousAgentRole.RESEARCH),
            memory_response(AutonomousMemoryScope.WORK),
            delegate_response(AutonomousAgentRole.CRITIC),
            defer_response(wake=NOW + dt.timedelta(minutes=5)),
        ),
    )
    result = runtime.tick(task_fixture(), NOW)
    assert result.status == "waiting"
    assert result.model_calls == 5
    assert result.tool_calls == 1
    assert result.next_wake_at == NOW + dt.timedelta(minutes=5)


def test_model_failure_preserves_task_and_schedules_retry(tmp_path: Path) -> None:
    runtime = failing_reasoner_runtime(tmp_path)
    original = task_fixture(root_source_evidence_id=EvidenceId("a" * 64))
    result = runtime.tick(original, NOW)
    stored = runtime.tasks.reader().task(original.task_id)
    assert result.status == "blocked"
    assert stored is not None
    assert stored.root_source_evidence_id == EvidenceId("a" * 64)
    assert stored.next_wake_at == NOW + dt.timedelta(minutes=15)
```

Cover crash after persisting a decision but before tool dispatch, exact replay without a duplicate model decision or durable observation, per-tick budget boundary, event wake, explicit completion, no-trade continuation, memory retrieval/write, role delegation and 12-step maximum. Foundation tools are read-only and may be re-invoked after a process crash in the narrow window before their observation is durable; their canonical observation hash prevents duplicate durable history. Paper mutations are outside this release and must later use broker idempotency plus reconciliation.

- [ ] **Step 2: Verify the runtime tests fail**

Run: `uv run pytest -q tests/test_autonomous_supervisor_runtime.py`

Expected: collection fails because the runtime is absent.

- [ ] **Step 3: Implement the runtime services and bounded tick loop**

```python
@dataclass(frozen=True, slots=True)
class AutonomousSupervisorServices:
    tasks: AutonomousTaskStore
    memories: AutonomousMemoryStore
    reasoner: AutonomousReasoningClient
    tools: AutonomousToolRuntime
    clock: Callable[[], dt.datetime]


@final
class AutonomousSupervisorRuntime:
    def __init__(self, services: AutonomousSupervisorServices, *, max_steps: int = 12) -> None:
        self._services = services
        self._max_steps = max_steps

    @property
    def tasks(self) -> AutonomousTaskStore:
        return self._services.tasks

    @property
    def memories(self) -> AutonomousMemoryStore:
        return self._services.memories

    def admit_evidence(
        self,
        task_id: AutonomousTaskId,
        evidence: ResearchAgentEvidenceV1,
        now: dt.datetime,
    ) -> bool:
        task = self._services.tasks.reader().task(task_id)
        if task is None:
            raise InvalidAutonomousSupervisorError(reason="autonomous_task_missing")
        if evidence.evidence_id in task.source_evidence_ids:
            return False
        step = source_admission_step(task, evidence, now)
        with self._services.tasks.writer() as writer:
            return writer.append_step(step)

    def tick(
        self,
        task: AutonomousResearchTask,
        now: dt.datetime,
    ) -> AutonomousSupervisorTickResult:
        return self._run_task(task, now)

    def run_due(
        self,
        now: dt.datetime,
        *,
        events: tuple[str, ...] = (),
    ) -> tuple[AutonomousSupervisorTickResult, ...]:
        return tuple(self.tick(task, now) for task in self._services.tasks.reader().runnable(now, events=events))
```

Persist a decision step before dispatching a tool. On restart, inspect the last step: if it is an unapplied decision, parse and apply it rather than calling the model again. A tool call produces a separate observation step. A delegate changes the projected owner role, a memory response appends a memory record and step, and an artifact response records the artifact then moves to evaluating/learning or a waiting state.

- [ ] **Step 4: Implement retry and budget rules without terminal exhaustion**

Each `tick()` receives a fresh `AutonomousRunBudget(remaining_model_calls=8, remaining_tool_calls=16, remaining_runtime_seconds=120)`. Exhausting that slice appends `WAITING_TIME` with a one-minute wake; it does not consume the lifetime of the ResearchTask. Model/tool failures use delays `(15 minutes, 1 hour, 4 hours, 12 hours)` and then remain `BLOCKED` with a 24-hour diagnostic wake instead of becoming terminal.

```python
_RETRY_DELAYS: Final = (
    dt.timedelta(minutes=15),
    dt.timedelta(hours=1),
    dt.timedelta(hours=4),
    dt.timedelta(hours=12),
)


def retry_wake(now: dt.datetime, retry_count: int) -> dt.datetime:
    delay = _RETRY_DELAYS[min(retry_count - 1, len(_RETRY_DELAYS) - 1)]
    return now + delay if retry_count <= len(_RETRY_DELAYS) else now + dt.timedelta(hours=24)
```

- [ ] **Step 5: Pass runtime tests and commit**

Run: `uv run pytest -q tests/test_autonomous_supervisor_runtime.py tests/test_autonomous_task_store.py tests/test_autonomous_memory_store.py`

Expected: PASS.

```bash
git add trading_agent/autonomous_supervisor_runtime.py tests/test_autonomous_supervisor_runtime.py
git commit -m "feat: run persistent autonomous supervisor tasks"
```

## Task 6: Admit existing family evidence without losing source lineage

**Files:**
- Create: `trading_agent/autonomous_supervisor_adapter.py`
- Create: `tests/test_autonomous_supervisor_adapter.py`
- Modify: `trading_agent/research_agent_runtime.py:28-61,184-273`
- Modify: `tests/test_research_agent_runtime.py:171-242,424-545`

- [ ] **Step 1: Write failing all-family delegation and regression tests**

```python
@pytest.mark.parametrize("family", PRIMARY_AGENT_FAMILIES)
def test_every_family_delegates_to_persistent_supervisor_before_legacy_decision(
    tmp_path: Path,
    family: AgentFamilyId,
) -> None:
    decisions: list[AgentFamilyId] = []
    delegated: list[ResearchAgentEvidenceV1] = []
    runtime = research_runtime_fixture(
        tmp_path,
        legacy_decisions=RecordingDecisionClient(decisions),
        supervisor=RecordingSupervisor(delegated),
    )
    runtime.ingest((evidence_fixture(family),))
    runtime.tick(NOW)
    assert [item.agent_family_id for item in delegated] == [family]
    assert decisions == []


def test_retry_resolves_original_authority_evidence_not_retry_envelope(tmp_path: Path) -> None:
    root = evidence_fixture("day_trading", evidence_id=EvidenceId("a" * 64))
    supervisor = supervisor_adapter_fixture(tmp_path, first_result="failed", second_result="waiting")
    supervisor.tick(root, NOW)
    supervisor.run_due(NOW + dt.timedelta(minutes=15))
    requests = supervisor.reasoner.requests
    assert requests[-1].task.root_source_evidence_id == root.evidence_id
    assert requests[-1].task.source_evidence_ids == (root.evidence_id,)


def test_related_new_evidence_appends_without_changing_root(tmp_path: Path) -> None:
    first = evidence_fixture("day_trading", evidence_id=EvidenceId("a" * 64), subject_refs=("005930",))
    second = evidence_fixture("day_trading", evidence_id=EvidenceId("b" * 64), subject_refs=("005930",))
    supervisor = supervisor_adapter_fixture(tmp_path, first_result="waiting", second_result="waiting")
    first_result = supervisor.tick(first, NOW)
    second_result = supervisor.tick(second, NOW + dt.timedelta(minutes=1))
    assert second_result.task_id == first_result.task_id
    task = supervisor.runtime.tasks.reader().task(first_result.task_id)
    assert task is not None
    assert task.root_source_evidence_id == first.evidence_id
    assert task.source_evidence_ids == (first.evidence_id, second.evidence_id)
```

Add tests that KR and US Day evidence create separate tasks, repeated identical evidence is idempotent, new evidence appends to a matching open task only when family/market/subject match, and waiting/no-trade maps to a scheduled nonterminal cycle result.

- [ ] **Step 2: Verify the adapter and integration tests are red**

Run: `uv run pytest -q tests/test_autonomous_supervisor_adapter.py tests/test_research_agent_runtime.py`

Expected: FAIL because only the Day-specific runtime hook exists.

- [ ] **Step 3: Implement deterministic evidence admission**

```python
@dataclass(frozen=True, slots=True)
class AutonomousSupervisorAdapter:
    runtime: AutonomousSupervisorRuntime

    def tick(
        self,
        evidence: ResearchAgentEvidenceV1,
        now: dt.datetime,
    ) -> AutonomousSupervisorTickResult:
        matching = self.runtime.tasks.reader().matching_open_tasks(
            evidence.agent_family_id,
            evidence.market_id,
            evidence.subject_refs,
        )
        task_id = autonomous_task_id(evidence.agent_family_id, evidence.market_id, evidence.evidence_id)
        exact = self.runtime.tasks.reader().task(task_id)
        task = exact or (matching[0] if matching else task_from_evidence(evidence, now))
        if evidence.evidence_id not in task.source_evidence_ids:
            self.runtime.admit_evidence(task.task_id, evidence, now)
        return self.runtime.tick(task, now)
```

`task_from_evidence()` uses the evidence ID as `root_source_evidence_id`, carries `evidence.evidence_refs` and `evidence.subject_refs`, stores the bounded payload hash/reference, sets `owner_role=SUPERVISOR`, and seeds the plan `("inspect root evidence", "delegate specialist analysis", "ask critic", "schedule continuation")`. `admit_evidence()` appends a source-admission step containing the new evidence ID, refs, subjects, payload hash and observed time; it never rewrites the task root. Evidence with no subjects or a different family/market always starts its own task.

- [ ] **Step 4: Replace the Day-only protocol with a generic supervisor protocol**

In `research_agent_runtime.py`, replace `PersistentDayAgentRuntime` and `day_runtime` with:

```python
class PersistentResearchSupervisor(Protocol):
    def tick(
        self,
        evidence: ResearchAgentEvidenceV1,
        now: dt.datetime,
    ) -> AutonomousSupervisorTickResult:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ResearchAgentRuntimeServices:
    store: ResearchAgentCycleStore
    collector: ResearchAgentEvidenceCollector
    decisions: ResearchAgentDecisionClient
    actions: ResearchAgentActionClient
    supervisor_runtime: PersistentResearchSupervisor | None = None
```

After deterministic source admission and before the legacy `ResearchAgentDecisionRequest`, delegate every family when `supervisor_runtime` is installed. Keep the legacy branch only for isolated tests and rollback until the final cutover plan; production construction in Task 7 must always provide the supervisor.

- [ ] **Step 5: Project bounded supervisor outcomes to the existing audit boundary**

Implement `_project_supervisor_result(cycle, result, now)` with these mappings:

- `waiting` → `NO_ACTION`, reason `autonomous_task_waiting`, `open_work_ref=str(result.task_id)`, `artifact_refs=()`, and the exact scheduled/event wake.
- `blocked`/`failed` → `BLOCKED`/`FAILED`, `open_work_ref=str(result.task_id)`, `artifact_refs=()`, preserved source evidence refs, and the supervisor retry wake.
- `completed` → `COMPLETED` only when a submitted artifact exists and contains required evidence.
- `idle` is invalid after a cycle has started.

Do not call `retry_evidence()` for supervisor work. The durable task store owns retry lineage and the existing cycle store records only the bounded tick result.

- [ ] **Step 6: Pass integration tests and commit**

Run: `uv run pytest -q tests/test_autonomous_supervisor_adapter.py tests/test_research_agent_runtime.py`

Expected: PASS and `RecordingDecisionClient` has zero production-supervisor calls for all six families.

```bash
git add trading_agent/autonomous_supervisor_adapter.py trading_agent/research_agent_runtime.py tests/test_autonomous_supervisor_adapter.py tests/test_research_agent_runtime.py
git commit -m "feat: route research families through autonomous supervisor"
```

## Task 7: Wire the supervisor into the 24-hour Local Agent Computer service

**Files:**
- Create: `trading_agent/autonomous_supervisor_service.py`
- Create: `tests/test_autonomous_supervisor_service.py`
- Modify: `trading_agent/research_agent_service_runtime.py:101-180,358-398`
- Modify: `trading_agent/research_os_runtime.py:32-99`
- Modify: `tests/test_research_agent_service_runtime.py`
- Modify: `tests/test_research_os_runtime.py`
- Modify: `tests/test_research_agent_service_cli.py`

- [ ] **Step 1: Write failing production-builder, status and restart tests**

```python
def test_production_builder_always_installs_autonomous_supervisor(tmp_path: Path) -> None:
    runtime = build_service_runtime(config_fixture(tmp_path))
    try:
        assert runtime.supervisor_enabled is True
    finally:
        runtime.close()


def test_research_os_status_reports_open_tasks_and_next_wake(tmp_path: Path) -> None:
    config = config_fixture(tmp_path)
    adapter = build_autonomous_supervisor(config, client=fixture_client())
    adapter.tick(evidence_fixture("day_trading"), NOW)
    status = autonomous_supervisor_status(adapter.runtime.tasks, NOW)
    assert status.enabled is True
    assert status.nonterminal_tasks == 1
    assert status.next_wake_at is not None


def test_research_os_report_persists_supervisor_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = config_fixture(tmp_path)
    seed_waiting_supervisor_task(config, NOW)
    monkeypatch.setattr(research_os_runtime, "run_service_tick", lambda config, now: service_report_fixture(now))
    report = run_research_os_tick(config, NOW)
    persisted = ResearchOsRuntimeReport.model_validate_json(
        (config.output_root / "research-os-runtime-status.json").read_text()
    )
    assert report.autonomous_supervisor.nonterminal_tasks == 1
    assert persisted.autonomous_supervisor == report.autonomous_supervisor


def test_restart_reopens_existing_task_database_without_duplicate_task(tmp_path: Path) -> None:
    config = config_fixture(tmp_path)
    first = run_research_os_tick(config, NOW)
    second = run_research_os_tick(config, NOW + dt.timedelta(minutes=1))
    assert second.autonomous_supervisor.total_tasks == first.autonomous_supervisor.total_tasks
```

Also test private path modes, no broker imports in the new service module, missing LLM executable failure reporting without task loss, and launchd plist arguments containing no Codex app/session dependency.

- [ ] **Step 2: Verify the service tests fail**

Run: `uv run pytest -q tests/test_autonomous_supervisor_service.py tests/test_research_agent_service_runtime.py tests/test_research_os_runtime.py tests/test_research_agent_service_cli.py`

Expected: FAIL because the production builder and report do not expose the supervisor.

- [ ] **Step 3: Implement derived private paths and foundation tools**

```python
@dataclass(frozen=True, slots=True)
class AutonomousSupervisorPaths:
    task_database: Path
    memory_database: Path


def autonomous_supervisor_paths(config: ResearchAgentServiceConfig) -> AutonomousSupervisorPaths:
    root = config.output_root / "autonomous-supervisor"
    return AutonomousSupervisorPaths(root / "tasks.sqlite3", root / "memory.sqlite3")
```

Build allowlisted foundation tools:

- `evidence.read`: returns the current task's stored bounded source evidence.
- `memory.search`: returns at most 16 matching records.
- `memory.record`: is handled by the structured memory response, not arbitrary shell execution.
- `task.history`: returns at most 32 prior steps.

No shell, browser, broker, KIS mutation, LS mutation, account, credential or Paper order tool is part of this release.

Implement the provider and tool factories with the existing validated configuration types:

```python
def configured_proposal_client(config: SystematicResearchActionConfig) -> LlmProposalClient:
    if config.response_fixture is not None:
        return FixtureLlmProposalClient(load_private_canonical_llm_response(config.response_fixture))
    if config.hermes_executable is None:
        raise InvalidAutonomousSupervisorServiceError(reason="autonomous_llm_provider_missing")
    return HermesCliProposalClient(config.hermes_executable, config.model_id, config.provider_id)


def build_foundation_tool_runtime(
    tasks: AutonomousTaskStore,
    memories: AutonomousMemoryStore,
) -> AutonomousToolRuntime:
    return AutonomousToolRuntime(
        bindings=(
            evidence_read_binding(tasks),
            memory_search_binding(memories),
            task_history_binding(tasks),
        ),
        clock=lambda: dt.datetime.now(dt.UTC),
    )
```

The three binding constructors accept only their declared string arguments, return canonical JSON, apply the 16-record/32-step bounds above, and expose no mutation authority.

- [ ] **Step 4: Implement the production supervisor builder and status model**

```python
class AutonomousSupervisorStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    enabled: Literal[True] = True
    total_tasks: int = Field(ge=0)
    nonterminal_tasks: int = Field(ge=0)
    blocked_tasks: int = Field(ge=0)
    next_wake_at: AwareDatetime | None
    last_task_id: AutonomousTaskId | None


def build_autonomous_supervisor(
    config: ResearchAgentServiceConfig,
    *,
    client: LlmProposalClient | None = None,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> AutonomousSupervisorAdapter:
    paths = autonomous_supervisor_paths(config)
    proposal_client = client or configured_proposal_client(config.systematic)
    tasks = AutonomousTaskStore(paths.task_database)
    memories = AutonomousMemoryStore(paths.memory_database)
    tools = build_foundation_tool_runtime(tasks, memories)
    reasoner = AutonomousStructuredReasoner(proposal_client)
    runtime = AutonomousSupervisorRuntime(
        AutonomousSupervisorServices(tasks, memories, reasoner, tools, clock)
    )
    return AutonomousSupervisorAdapter(runtime)
```

Use `HermesCliProposalClient` or `FixtureLlmProposalClient` through the existing provider/config rules, `AutonomousStructuredReasoner`, the two new stores, and the foundation tool bindings. The builder creates private parent directories but never creates or reads credential files itself.

- [ ] **Step 5: Install the supervisor in production and persist its status**

In `build_service_runtime()`, create one supervisor and pass it as `supervisor_runtime=`. Expose a read-only `supervisor_enabled` property for verification. Add `autonomous_supervisor: AutonomousSupervisorStatus` to `ResearchOsRuntimeReport`; compute it from the task store after `run_service_tick()` and before writing `research-os-runtime-status.json`.

The existing `run_research_os_forever()` loop and `research_agent_runtime_lease` remain the single 30-second owner. Do not add a second launchd label or competing writer in this release.

- [ ] **Step 6: Prove launchd remains independent of Codex and production output is restart-safe**

Update service CLI/config tests to assert the plist still has `KeepAlive=True`, `RunAtLoad=True`, the repository `run_research_agent_runtime.py run` command, and no `Codex`, task/thread ID, terminal session, browser token or chat dependency.

- [ ] **Step 7: Pass service tests and commit**

Run: `uv run pytest -q tests/test_autonomous_supervisor_service.py tests/test_research_agent_service_runtime.py tests/test_research_os_runtime.py tests/test_research_agent_service_cli.py`

Expected: PASS.

```bash
git add trading_agent/autonomous_supervisor_service.py trading_agent/research_agent_service_runtime.py trading_agent/research_os_runtime.py tests/test_autonomous_supervisor_service.py tests/test_research_agent_service_runtime.py tests/test_research_os_runtime.py tests/test_research_agent_service_cli.py
git commit -m "feat: run autonomous supervisor in research service"
```

## Task 8: Run the foundation completion gate

**Files:**
- Modify only files required to fix failures caused by Tasks 1-7.

- [ ] **Step 1: Run all targeted autonomous and legacy regression tests**

Run:

```bash
uv run pytest -q \
  tests/test_autonomous_task_models.py \
  tests/test_autonomous_task_store.py \
  tests/test_autonomous_memory_store.py \
  tests/test_autonomous_reasoning.py \
  tests/test_autonomous_tool_runtime.py \
  tests/test_autonomous_supervisor_runtime.py \
  tests/test_autonomous_supervisor_adapter.py \
  tests/test_autonomous_supervisor_service.py \
  tests/test_day_agent_runtime.py \
  tests/test_day_agent_task_store.py \
  tests/test_research_agent_runtime.py \
  tests/test_research_agent_service_runtime.py \
  tests/test_research_os_runtime.py \
  tests/test_research_agent_service_cli.py
```

Expected: PASS with no skipped autonomous-supervisor tests.

- [ ] **Step 2: Run formatting, lint and type checks for every changed Python file**

```bash
uv run ruff check \
  trading_agent/autonomous_task_models.py \
  trading_agent/autonomous_task_store.py \
  trading_agent/autonomous_memory_models.py \
  trading_agent/autonomous_memory_store.py \
  trading_agent/autonomous_reasoning.py \
  trading_agent/autonomous_tool_runtime.py \
  trading_agent/autonomous_supervisor_runtime.py \
  trading_agent/autonomous_supervisor_adapter.py \
  trading_agent/autonomous_supervisor_service.py \
  trading_agent/research_agent_runtime.py \
  trading_agent/research_agent_service_runtime.py \
  trading_agent/research_os_runtime.py \
  tests/test_autonomous_task_models.py \
  tests/test_autonomous_task_store.py \
  tests/test_autonomous_memory_store.py \
  tests/test_autonomous_reasoning.py \
  tests/test_autonomous_tool_runtime.py \
  tests/test_autonomous_supervisor_runtime.py \
  tests/test_autonomous_supervisor_adapter.py \
  tests/test_autonomous_supervisor_service.py

uv run basedpyright \
  trading_agent/autonomous_task_models.py \
  trading_agent/autonomous_task_store.py \
  trading_agent/autonomous_memory_models.py \
  trading_agent/autonomous_memory_store.py \
  trading_agent/autonomous_reasoning.py \
  trading_agent/autonomous_tool_runtime.py \
  trading_agent/autonomous_supervisor_runtime.py \
  trading_agent/autonomous_supervisor_adapter.py \
  trading_agent/autonomous_supervisor_service.py \
  trading_agent/research_agent_runtime.py \
  trading_agent/research_agent_service_runtime.py \
  trading_agent/research_os_runtime.py
```

Expected: both commands exit 0.

- [ ] **Step 3: Manually exercise CLI help and one bad input**

```bash
uv run --offline python run_research_agent_runtime.py --help
uv run --offline python run_research_agent_runtime.py status \
  --config /tmp/trading-agent-missing-config.json \
  --plist /tmp/trading-agent-missing.plist
```

Expected: help exits 0 and lists `run`, `tick`, `cycle`, `status`; the missing-input command exits 2 without printing secrets or a traceback.

- [ ] **Step 4: Manually exercise the real read-only status surface**

```bash
uv run --offline python run_research_agent_runtime.py status \
  --config /Users/goyunseo/.config/trading-agent/research-agent-runtime-v11.json \
  --plist /Users/goyunseo/Library/LaunchAgents/ai.trading-agent.research-agent-runtime.plist
```

Expected: exit 0; JSON includes `autonomous_supervisor.enabled=true`, task counts, and a next wake or zero nonterminal tasks. It must contain `broker_mutation:0` and no credentials, headers, account identifiers or raw authentication response.

- [ ] **Step 5: Prove non-Paper URLs are still rejected before HTTP**

Run:

```bash
uv run pytest -q \
  tests/test_alpaca_paper_client.py \
  tests/test_alpaca_paper_mutation_client.py \
  tests/test_paper_operating_mutation_execution.py
```

Expected: PASS, including opener/transport spies showing zero calls for invalid or live Alpaca URLs.

- [ ] **Step 6: Commit any completion-gate-only fixes, then record the release evidence**

If Tasks 1-7 already pass unchanged, do not create an empty commit. Otherwise commit only the minimal fixes:

List the actual changed paths with `git diff --name-only`, inspect them, and use `git add -p` to stage only completion-gate hunks owned by this plan; never use `git add -A` or stage the user's unrelated files. Then commit with `git commit -m "fix: complete autonomous supervisor foundation gate"`.

Record the final commit SHA, targeted test pass line, Ruff exit, basedpyright exit, CLI outputs, supervisor status artifact and Paper pre-network rejection evidence in `docs/checkpoints/2026-08-26-autonomous-supervisor-foundation-ko.md` and commit that checkpoint separately:

```bash
git add docs/checkpoints/2026-08-26-autonomous-supervisor-foundation-ko.md
git commit -m "docs: record autonomous supervisor foundation gate"
```

## Completion boundary

This plan does not claim that the full approved design is finished. It completes the shared runtime prerequisite when all of the following are observed:

- Production `build_service_runtime()` has a non-null autonomous supervisor for every family.
- A task performs at least two model decisions with an intervening host-tool observation in one tick.
- Restart resumes a persisted unapplied decision without a duplicate model decision or durable observation; foundation read-only tool replay is explicitly safe.
- A no-trade/no-action artifact remains nonterminal and owns a future time/event wake.
- Five repeated failures retain the original `root_source_evidence_id` and remain diagnosable rather than terminalizing after four.
- The persisted Research OS status exposes supervisor task counts and next wake.
- The launchd service remains independent of Codex or an open conversation.
- Existing exact Alpaca Paper pre-network rejection tests remain green.

After this gate passes, write and execute the KR natural-market vertical plan before adding the US Paper and Loop Engineer releases.
