# Six-Agent Persistent Research Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run six independent research actors inside one restart-safe foreground service, wake only the actor whose evidence or market-time condition changed, connect Systematic Quant to the generated-Python experiment loop, and publish isolated Hermes results without granting broker authority.

**Architecture:** Add a small SQLite-backed cycle journal and typed evidence inbox beside the existing autonomous control plane. A fixed actor registry maps each family to its wake policy, evidence adapters and bounded action; the foreground runtime runs one primary decision at a time and delegates only Systematic heavy work to the existing `run_autonomous_research_cycle.py` path. A single private LaunchAgent keeps the runtime alive while existing experiment, Reviewer, market, swing, recommendation and Hermes stores remain authoritative.

**Tech Stack:** Python 3.12, frozen Pydantic v2 models, SQLite query-only readers and single-writer lease, Hermes CLI, existing macOS sandbox generated-strategy runner, launchd, pytest, Ruff and basedpyright.

---

## File structure

Create focused files; keep each production file below 250 logical lines.

- `trading_agent/research_agent_cycle_models.py`: evidence, decision, result, cycle and open-work contracts plus deterministic identities.
- `trading_agent/research_agent_cycle_schema.py`: SQLite schema version and migration-free schema initializer.
- `trading_agent/research_agent_cycle_store.py`: single-writer journal, cursor, recovery and idempotent result publication.
- `trading_agent/research_agent_sources.py`: adapters from existing authoritative stores into typed evidence envelopes.
- `trading_agent/research_agent_wake_policy.py`: six fixed policies, event/schedule eligibility, priority and retry backoff.
- `trading_agent/research_agent_decision.py`: bounded Hermes structured-decision request and response parser.
- `trading_agent/research_agent_actions.py`: family-specific action dispatcher and non-Systematic result builders.
- `trading_agent/research_agent_systematic.py`: adapter from a Systematic decision to the existing generated-strategy one-shot CLI.
- `trading_agent/research_agent_runtime.py`: service lease, one tick, foreground loop and interrupted-cycle recovery.
- `trading_agent/research_agent_hermes.py`: family-isolated `HermesProjectionRecord` publication.
- `trading_agent/research_agent_service_config.py`: private immutable config, LaunchAgent plist and clean-main preflight.
- `run_research_agent_runtime.py`: `provision`, `verify`, `tick`, `run` and `status` CLI surface.
- `tests/test_research_agent_cycle_models.py`
- `tests/test_research_agent_cycle_store.py`
- `tests/test_research_agent_sources.py`
- `tests/test_research_agent_wake_policy.py`
- `tests/test_research_agent_decision.py`
- `tests/test_research_agent_actions.py`
- `tests/test_research_agent_systematic.py`
- `tests/test_research_agent_runtime.py`
- `tests/test_research_agent_hermes.py`
- `tests/test_research_agent_service_cli.py`

Existing files to modify only at their public integration seams:

- `trading_agent/dashboard_agent_runtime.py`: project current actor cycle state instead of publisher-wide synthetic readiness when the new journal exists.
- `trading_agent/dashboard_kr_autonomous_bridge.py`: append the already-authorized KR source trigger to the normalized evidence inbox.
- `run_dashboard_publisher.py`: stop treating dashboard publication itself as six-agent readiness after the new runtime is active.
- `run_autonomous_research_cycle.py`: expose a typed in-process command builder/result parser without changing one-shot behavior.
- `tests/test_dashboard_agent_runtime.py`
- `tests/test_dashboard_kr_autonomous_bridge.py`
- `tests/test_autonomous_research_cycle_cli.py`

### Task 1: Cycle identities and immutable contracts

**Files:**
- Create: `trading_agent/research_agent_cycle_models.py`
- Test: `tests/test_research_agent_cycle_models.py`

- [ ] **Step 1: Write failing model and identity tests**

```python
def test_cycle_identity_binds_actor_trigger_and_cursor() -> None:
    evidence = ResearchAgentEvidenceV1(
        evidence_id="e" * 64,
        agent_family_id="opportunity_manager",
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key="source.kr.cycle.001",
        evidence_refs=("a" * 64,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256="b" * 64,
        market_id="kr_equities",
    )
    first = research_agent_cycle_id(evidence, cursor_before=0)
    assert first == research_agent_cycle_id(evidence, cursor_before=0)
    assert first != research_agent_cycle_id(evidence, cursor_before=1)


def test_result_cannot_claim_order_or_lifecycle_authority() -> None:
    result = ResearchAgentResultV1(
        result_id="c" * 64,
        cycle_id="d" * 64,
        agent_family_id="systematic_quant",
        status=ResearchAgentResultStatus.COMPLETED,
        question="Does the cited mechanism survive conservative costs?",
        summary="The deterministic Reviewer returned HOLD.",
        evidence_refs=("a" * 64,),
        artifact_refs=("b" * 64,),
        occurred_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
    )
    assert result.order_authority is False
    assert result.lifecycle_authority is False
    assert result.allocation_authority is False
```

- [ ] **Step 2: Run the model tests and observe the missing module failure**

Run: `uv run pytest -q tests/test_research_agent_cycle_models.py`

Expected: FAIL during collection with `ModuleNotFoundError: trading_agent.research_agent_cycle_models`.

- [ ] **Step 3: Implement strict frozen models and deterministic hashes**

```python
class ResearchAgentTriggerKind(StrEnum):
    NEW_DATA = "new_data"
    MARKET_EVENT = "market_event"
    EXPERIMENT_RESULT = "experiment_result"
    REVIEWER_FEEDBACK = "reviewer_feedback"
    SCHEDULED_WAKE = "scheduled_wake"
    OPEN_WORK = "open_work"


class ResearchAgentDecisionKind(StrEnum):
    INVESTIGATE_CANDIDATE = "investigate_candidate"
    PROPOSE_HYPOTHESIS = "propose_hypothesis"
    RUN_LIGHT_EXPERIMENT = "run_light_experiment"
    REQUEST_HEAVY_EXPERIMENT = "request_heavy_experiment"
    PUBLISH_CONTEXT = "publish_context"
    PUBLISH_RECOMMENDATION = "publish_recommendation"
    REVIEW_OPEN_STATE = "review_open_state"
    NO_ACTION = "no_action"


class ResearchAgentEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    agent_family_id: AgentFamilyId
    trigger_kind: ResearchAgentTriggerKind
    source_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    observed_at: AwareDatetime
    available_at: AwareDatetime
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    market_id: Literal["us_equities", "kr_equities", "cross_market", "none"]


def research_agent_cycle_id(evidence: ResearchAgentEvidenceV1, *, cursor_before: int) -> str:
    material = f"{evidence.agent_family_id}:{evidence.evidence_id}:{cursor_before}:cycle-v1"
    return hashlib.sha256(material.encode()).hexdigest()
```

Also define `ResearchAgentDecisionV1`, `ResearchAgentResultV1`, `ResearchAgentCycleV1`,
`ResearchAgentOpenWorkV1`, `ResearchAgentWakeKind`, `ResearchAgentCycleState`, and identity helpers
`research_agent_action_id` and `research_agent_result_id`. Require aware UTC-compatible timestamps,
sorted unique references, exactly one primary decision, and a reason plus continuation for `NO_ACTION`.

- [ ] **Step 4: Run the model tests**

Run: `uv run pytest -q tests/test_research_agent_cycle_models.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add trading_agent/research_agent_cycle_models.py tests/test_research_agent_cycle_models.py
git commit -m "feat: define persistent research agent cycle contracts"
```

### Task 2: Append-only cycle journal, cursor and crash recovery

**Files:**
- Create: `trading_agent/research_agent_cycle_schema.py`
- Create: `trading_agent/research_agent_cycle_store.py`
- Test: `tests/test_research_agent_cycle_store.py`

- [ ] **Step 1: Write failing journal tests**

```python
def test_terminal_cycle_advances_only_its_actor_cursor(tmp_path: Path) -> None:
    store = ResearchAgentCycleStore(tmp_path / "cycles.sqlite3")
    evidence = evidence_fixture("opportunity_manager", sequence=1)
    assert store.append_evidence(evidence)
    started = store.start_cycle(evidence, started_at=NOW)
    assert store.cursor("opportunity_manager") == 0
    result = result_fixture(started.cycle_id, "opportunity_manager")
    store.finish_cycle(started, result)
    assert store.cursor("opportunity_manager") == 1
    assert store.cursor("systematic_quant") == 0


def test_restart_interrupts_started_cycle_without_duplicate_action(tmp_path: Path) -> None:
    store = ResearchAgentCycleStore(tmp_path / "cycles.sqlite3")
    evidence = evidence_fixture("systematic_quant", sequence=1)
    store.append_evidence(evidence)
    started = store.start_cycle(evidence, started_at=NOW)
    recovered = store.recover_interrupted(NOW + dt.timedelta(minutes=1))
    assert recovered == (started.cycle_id,)
    replay = store.start_cycle(evidence, started_at=NOW + dt.timedelta(minutes=2))
    assert replay.action_request_id == started.action_request_id
```

- [ ] **Step 2: Verify the tests fail**

Run: `uv run pytest -q tests/test_research_agent_cycle_store.py`

Expected: FAIL because the store and schema do not exist.

- [ ] **Step 3: Implement schema version 1**

```python
RESEARCH_AGENT_CYCLE_SCHEMA_VERSION: Final = 1

SCHEMA = (
    "CREATE TABLE evidence (sequence INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id TEXT UNIQUE NOT NULL, "
    "agent_family_id TEXT NOT NULL, available_at TEXT NOT NULL, payload_json TEXT NOT NULL)",
    "CREATE TABLE cycles (cycle_id TEXT PRIMARY KEY, agent_family_id TEXT NOT NULL, evidence_sequence INTEGER "
    "NOT NULL, action_request_id TEXT NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL, "
    "terminal_at TEXT, payload_json TEXT NOT NULL)",
    "CREATE UNIQUE INDEX cycles_action_request ON cycles(action_request_id)",
    "CREATE TABLE results (result_id TEXT PRIMARY KEY, cycle_id TEXT UNIQUE NOT NULL, payload_json TEXT NOT NULL)",
    "CREATE TABLE cursors (agent_family_id TEXT PRIMARY KEY, evidence_sequence INTEGER NOT NULL)",
    "CREATE TABLE open_work (open_work_id TEXT PRIMARY KEY, agent_family_id TEXT NOT NULL, state TEXT NOT NULL, "
    "payload_json TEXT NOT NULL)",
)
```

Set `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `synchronous=FULL` and `user_version=1`. Reject any
other existing schema version; do not add an upgrade path for an unshipped schema.

- [ ] **Step 4: Implement the leased store**

```python
def finish_cycle(
    connection: sqlite3.Connection,
    cycle: ResearchAgentCycleV1,
    result: ResearchAgentResultV1,
) -> None:
    terminal = cycle.model_copy(
        update={
            "state": "completed",
            "terminal_at": result.occurred_at,
            "result_id": result.result_id,
        }
    )
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO results VALUES (?, ?, ?)",
        (result.result_id, cycle.cycle_id, result.model_dump_json()),
    )
    updated = connection.execute(
        "UPDATE cycles SET state = ?, terminal_at = ?, payload_json = ? "
        "WHERE cycle_id = ? AND state = ?",
        ("completed", result.occurred_at.isoformat(), terminal.model_dump_json(),
         cycle.cycle_id, "running"),
    )
    if updated.rowcount != 1:
        connection.rollback()
        raise InvalidResearchAgentCycleStoreError
    connection.execute(
        "INSERT INTO cursors VALUES (?, ?) ON CONFLICT(agent_family_id) DO UPDATE "
        "SET evidence_sequence = excluded.evidence_sequence",
        (cycle.agent_family_id, cycle.evidence_sequence),
    )
    connection.commit()
```

Wrap this transaction in `ResearchAgentCycleStore.finish_cycle`. Implement the other public methods with
these exact signatures: constructor `(path: Path)`, `append_evidence(evidence) -> bool`,
`runnable_evidence(family, now) -> tuple[StoredEvidence, ...]`, `start_cycle(evidence, started_at)`,
`fail_cycle(cycle, result)`, `recover_interrupted(recovered_at)`, `cursor(family)`, `latest_cycles()`,
`results()` and `upsert_open_work(item)`. Every read validates stored JSON back into the frozen model before
returning it.

Use a mode-600 `fcntl.LOCK_EX | LOCK_NB` writer lock. Readers open SQLite with URI `mode=ro` and
`PRAGMA query_only=ON`. `finish_cycle` inserts the immutable result, terminalizes the cycle and advances
only that actor cursor in one `BEGIN IMMEDIATE` transaction.

- [ ] **Step 5: Run store tests**

Run: `uv run pytest -q tests/test_research_agent_cycle_store.py`

Expected: PASS, including conflict, schema, mode-600, symlink and writer-lease cases.

- [ ] **Step 6: Commit Task 2**

```bash
git add trading_agent/research_agent_cycle_schema.py trading_agent/research_agent_cycle_store.py tests/test_research_agent_cycle_store.py
git commit -m "feat: persist research agent cycles and cursors"
```

### Task 3: Project current authoritative sources into six actor inboxes

**Files:**
- Create: `trading_agent/research_agent_sources.py`
- Modify: `trading_agent/dashboard_kr_autonomous_bridge.py`
- Test: `tests/test_research_agent_sources.py`
- Test: `tests/test_dashboard_kr_autonomous_bridge.py`

- [ ] **Step 1: Write failing adapter tests for all six families**

```python
def test_source_projection_routes_evidence_without_cross_family_leakage(tmp_path: Path) -> None:
    sources = source_fixture(tmp_path)
    projected = collect_research_agent_evidence(sources, now=NOW)
    by_family = {family: tuple(item for item in projected if item.agent_family_id == family)
                 for family in PRIMARY_AGENT_FAMILIES}
    assert {family for family, items in by_family.items() if items} == set(PRIMARY_AGENT_FAMILIES)
    assert all(item.payload_sha256 in item.evidence_refs for item in by_family["systematic_quant"])
    assert all(item.market_id == "none" for item in by_family["systematic_quant"])
    assert all(item.trigger_kind is ResearchAgentTriggerKind.MARKET_EVENT
               for item in by_family["market_context"])


def test_missing_derivatives_entitlement_is_explicit_evidence(tmp_path: Path) -> None:
    projected = collect_research_agent_evidence(source_fixture(tmp_path, derivatives=False), now=NOW)
    derivative = next(item for item in projected if item.agent_family_id == "derivatives_research")
    assert derivative.source_key.endswith("blocked.current_quote_not_licensed")
```

- [ ] **Step 2: Run the source tests and observe failure**

Run: `uv run pytest -q tests/test_research_agent_sources.py tests/test_dashboard_kr_autonomous_bridge.py`

Expected: the new source module and KR inbox integration are missing.

- [ ] **Step 3: Implement source path configuration and the adapter protocol**

```python
class ResearchAgentSourcePaths(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    outputs_root: Path
    market_context_root: Path
    day_session_root: Path
    swing_shadow_database: Path
    swing_review_database: Path
    experiment_ledger: Path
    lane_review_database: Path


class ResearchAgentSourceAdapter(Protocol):
    def collect(
        self,
        paths: ResearchAgentSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        raise NotImplementedError
```

Reject relative, symlinked or non-current-user source paths at this boundary. Missing optional sources yield
typed capability evidence; malformed existing sources fail the tick rather than being silently skipped.
The Opportunity adapter discovers at most the two newest `live_sessions/YYYYMMDD/opportunities.v1.jsonl`
files on every tick, so a config written before the next session does not freeze the source list.

- [ ] **Step 4: Implement one adapter per family using existing readers**

```python
ADAPTERS: Final[tuple[ResearchAgentSourceAdapter, ...]] = (
    OpportunitySourceAdapter(),       # read_opportunity_snapshots + authorized KR triggers
    MarketContextSourceAdapter(),     # MarketContextSnapshot private JSON artifacts
    DaySourceAdapter(),               # PaperStore recommendations/events + latest completed session
    SwingSourceAdapter(),             # SwingShadowReader + SwingShadowReviewReader
    SystematicSourceAdapter(),        # ExperimentLedgerReader sources/trials + LaneReviewReader
    DerivativesSourceAdapter(),       # project_derivatives source nodes/capability blockers
)
```

Canonicalize each source payload before hashing; persist only references and hashes in the inbox. Do not copy
raw news, account data, credentials or local paths into a cycle/result.

- [ ] **Step 5: Connect the existing KR bridge to the inbox**

Extend `publish_kr_autonomous_triggers` with an optional `cycle_store: ResearchAgentCycleStore | None`.
When present, convert the already-authorized trigger into one Opportunity evidence envelope and append it
after authority/evidence publication succeeds. Preserve the existing return type and replay behavior.

- [ ] **Step 6: Run source tests**

Run: `uv run pytest -q tests/test_research_agent_sources.py tests/test_dashboard_kr_autonomous_bridge.py`

Expected: PASS with exactly six routed source families and no duplicate KR evidence.

- [ ] **Step 7: Commit Task 3**

```bash
git add trading_agent/research_agent_sources.py trading_agent/dashboard_kr_autonomous_bridge.py tests/test_research_agent_sources.py tests/test_dashboard_kr_autonomous_bridge.py
git commit -m "feat: route authoritative evidence to research actors"
```

### Task 4: Actor-specific wake, priority and backoff policies

**Files:**
- Create: `trading_agent/research_agent_wake_policy.py`
- Test: `tests/test_research_agent_wake_policy.py`

- [ ] **Step 1: Write failing wake-policy tests**

```python
def test_no_new_evidence_means_no_runnable_actor() -> None:
    assert runnable_actors((), (), now=NOW) == ()


def test_opportunity_debounces_while_systematic_feedback_runs_immediately() -> None:
    opportunity = stored_evidence("opportunity_manager", available_at=NOW)
    feedback = stored_evidence("systematic_quant", trigger="reviewer_feedback", available_at=NOW)
    selected = runnable_actors((opportunity, feedback), (), now=NOW + dt.timedelta(seconds=30))
    assert tuple(item.agent_family_id for item in selected) == ("systematic_quant",)
    later = runnable_actors((opportunity, feedback), (), now=NOW + dt.timedelta(minutes=2))
    assert tuple(item.agent_family_id for item in later) == ("systematic_quant", "opportunity_manager")


def test_failure_backoff_is_15_minutes_then_1_hour_then_4_hours() -> None:
    assert tuple(retry_delay(count) for count in range(1, 5)) == (
        dt.timedelta(minutes=15), dt.timedelta(hours=1), dt.timedelta(hours=4), None
    )
```

- [ ] **Step 2: Verify failures**

Run: `uv run pytest -q tests/test_research_agent_wake_policy.py`

Expected: FAIL because policy functions do not exist.

- [ ] **Step 3: Implement the exact fixed registry**

```python
@dataclass(frozen=True, slots=True)
class ActorWakePolicy:
    family_id: AgentFamilyId
    debounce: dt.timedelta
    scheduled_interval: dt.timedelta | None
    priority: int
    max_model_calls_per_cycle: Literal[1] = 1


ACTOR_WAKE_POLICIES: Final = (
    ActorWakePolicy("opportunity_manager", dt.timedelta(minutes=2), None, 30),
    ActorWakePolicy("market_context", dt.timedelta(0), dt.timedelta(minutes=30), 40),
    ActorWakePolicy("day_trading", dt.timedelta(0), None, 10),
    ActorWakePolicy("swing_trading", dt.timedelta(0), None, 40),
    ActorWakePolicy("systematic_quant", dt.timedelta(0), None, 20),
    ActorWakePolicy("derivatives_research", dt.timedelta(0), dt.timedelta(minutes=15), 40),
)
```

`runnable_actors` must honor available time, open-work deadline, cooldown, debounce and the design priority:
current-session/open-state, Reviewer, source event, scheduled context/post-close, retry. Round-robin equal
priority families by the oldest terminal cycle so one actor cannot starve another.

- [ ] **Step 4: Run wake-policy tests**

Run: `uv run pytest -q tests/test_research_agent_wake_policy.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add trading_agent/research_agent_wake_policy.py tests/test_research_agent_wake_policy.py
git commit -m "feat: schedule independent research actor wakes"
```

### Task 5: One structured Hermes decision per actor cycle

**Files:**
- Create: `trading_agent/research_agent_decision.py`
- Test: `tests/test_research_agent_decision.py`

- [ ] **Step 1: Write failing prompt and parser tests**

```python
def test_decision_prompt_binds_family_memory_and_evidence() -> None:
    request = decision_request_fixture("market_context")
    prompt = render_research_agent_prompt(request)
    assert "<agent-family>market_context</agent-family>" in prompt
    assert "research-family:market_context:memory-v1" in prompt
    assert request.evidence[0].evidence_id in prompt
    assert "order authority: false" in prompt


def test_parser_rejects_second_action_or_authority_claim() -> None:
    with pytest.raises(InvalidResearchAgentDecisionError):
        parse_research_agent_decision(b'{"schema_version":1,"primary_decision":"publish_context",'
                                      b'"secondary_decision":"publish_recommendation"}')
```

- [ ] **Step 2: Verify the tests fail**

Run: `uv run pytest -q tests/test_research_agent_decision.py`

Expected: FAIL because the decision client is missing.

- [ ] **Step 3: Implement a bounded Hermes client and strict JSON response**

```python
class ResearchAgentDecisionClient(Protocol):
    def decide(self, request: ResearchAgentDecisionRequest) -> ResearchAgentDecisionV1:
        raise NotImplementedError


class HermesCliResearchAgentDecisionClient:
    def __init__(self, executable: Path, model_id: str) -> None:
        self._executable = require_private_executable(executable)
        self._model_id = require_model_id(model_id)
    def decide(self, request: ResearchAgentDecisionRequest) -> ResearchAgentDecisionV1:
        completed = subprocess.run(
            (str(self._executable), "--ignore-user-config", "--ignore-rules", "-m", self._model_id,
             "-t", "", "-z", render_research_agent_prompt(request)),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=request.max_runtime_seconds,
            env={"PATH": "/usr/bin:/bin"},
        )
        return parse_research_agent_decision(completed.stdout)
```

The response fields are `primary_decision`, `question`, `summary`, `reason`, `open_work_ref`,
`requested_action`, `next_wake_kind` and `next_wake_at`. It contains no argv, path, credential, account,
price calculation, Reviewer verdict, lifecycle or order instruction. Record prompt/response SHA-256 and
model ID but never write raw prompt/response to the result envelope.

- [ ] **Step 4: Run decision tests**

Run: `uv run pytest -q tests/test_research_agent_decision.py`

Expected: PASS for fixture client, valid Hermes stdout, non-zero exit, timeout, malformed JSON and
multi-action rejection.

- [ ] **Step 5: Commit Task 5**

```bash
git add trading_agent/research_agent_decision.py tests/test_research_agent_decision.py
git commit -m "feat: request bounded research actor decisions"
```

### Task 6: Family actions and the generated-strategy Systematic adapter

**Files:**
- Create: `trading_agent/research_agent_actions.py`
- Create: `trading_agent/research_agent_systematic.py`
- Modify: `run_autonomous_research_cycle.py`
- Test: `tests/test_research_agent_actions.py`
- Test: `tests/test_research_agent_systematic.py`
- Test: `tests/test_autonomous_research_cycle_cli.py`

- [ ] **Step 1: Write failing action-boundary tests**

```python
def test_generated_strategy_action_is_systematic_only() -> None:
    executor = ResearchAgentActionExecutor(action_config_fixture())
    with pytest.raises(InvalidResearchAgentActionError):
        executor.execute(cycle_fixture("day_trading"), decision_fixture("request_heavy_experiment"))


def test_non_systematic_actions_never_start_broker_or_shell() -> None:
    calls: list[tuple[str, ...]] = []
    executor = ResearchAgentActionExecutor(action_config_fixture(), runner=lambda argv: calls.append(argv) or 0)
    result = executor.execute(cycle_fixture("market_context"), decision_fixture("publish_context"))
    assert calls == []
    assert result.order_authority is False


def test_systematic_command_uses_unique_cycle_output_and_existing_guarded_cli(tmp_path: Path) -> None:
    command = systematic_cycle_command(systematic_config(tmp_path), cycle_fixture("systematic_quant"))
    assert command[0].name == "uv"
    assert "run_autonomous_research_cycle.py" in command
    assert str(tmp_path / "runs" / cycle_fixture("systematic_quant").cycle_id) in command
```

- [ ] **Step 2: Verify action tests fail**

Run: `uv run pytest -q tests/test_research_agent_actions.py tests/test_research_agent_systematic.py tests/test_autonomous_research_cycle_cli.py`

Expected: FAIL because action modules and reusable one-shot report parsing are missing.

- [ ] **Step 3: Expose the existing one-shot command/report contract**

In `run_autonomous_research_cycle.py`, add pure helpers without changing `main` behavior:

```python
class AutonomousCycleCliResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["complete", "blocked"]
    strategy_artifact_id: str | None
    trial_id: str | None
    experiment_artifact_id: str | None
    review_artifact_id: str | None
    reviewer_decision: str | None


def load_autonomous_cycle_cli_result(output_dir: Path) -> AutonomousCycleCliResult:
    text = read_private_text_query_only(output_dir / REPORT_NAME)
    fields = dict(
        line[2:].split(": ", 1)
        for line in text.splitlines()
        if line.startswith("- ") and ": " in line
    )
    status = fields.get("result")
    if status not in {"complete", "blocked"}:
        raise InvalidAutonomousCycleCliResultError
    return AutonomousCycleCliResult(
        status=status,
        strategy_artifact_id=fields.get("strategy_artifact_id"),
        trial_id=fields.get("trial_id"),
        experiment_artifact_id=fields.get("experiment_artifact_id"),
        review_artifact_id=fields.get("review_artifact_id"),
        reviewer_decision=fields.get("reviewer_decision"),
    )
```

Keep standalone PEP 723 metadata and existing `--help`, fixture and Hermes provider behavior intact.

- [ ] **Step 4: Implement family action dispatch**

```python
class ResearchAgentActionExecutor:
    def execute(
        self,
        cycle: ResearchAgentCycleV1,
        decision: ResearchAgentDecisionV1,
    ) -> ResearchAgentResultV1:
        if decision.primary_decision is ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT:
            if cycle.agent_family_id != "systematic_quant":
                raise InvalidResearchAgentActionError
            return self._systematic.execute(cycle, decision)
        return result_from_decision(cycle, decision)
```

Opportunity, Context, Day, Swing and Derivatives results summarize verified input and open work only.
`PUBLISH_RECOMMENDATION` is accepted only when an existing `TradeSignalEnvelope` reference already contains
timestamp, entry, stop, targets, rationale and outcome-history reference; the LLM may not create prices.

- [ ] **Step 5: Implement Systematic subprocess binding**

Build an absolute `uv run --offline python {config.project_root}/run_autonomous_research_cycle.py` command from a
frozen `SystematicResearchActionConfig`. Give every cycle its own `runs/{cycle_id}` output directory,
set stdin/stdout/stderr to `DEVNULL`, cap runtime, and parse the private report. A blocked one-shot becomes a
terminal failed/censored actor result with exact reason and 15-minute retry; it never becomes `completed`.

- [ ] **Step 6: Run action tests**

Run: `uv run pytest -q tests/test_research_agent_actions.py tests/test_research_agent_systematic.py tests/test_autonomous_research_cycle_cli.py`

Expected: PASS, including non-Systematic rejection, unique output, blocked result and Reviewer feedback refs.

- [ ] **Step 7: Commit Task 6**

```bash
git add trading_agent/research_agent_actions.py trading_agent/research_agent_systematic.py run_autonomous_research_cycle.py tests/test_research_agent_actions.py tests/test_research_agent_systematic.py tests/test_autonomous_research_cycle_cli.py
git commit -m "feat: execute family-bound research agent actions"
```

### Task 7: One runtime tick, foreground loop and restart-safe lease

**Files:**
- Create: `trading_agent/research_agent_runtime.py`
- Test: `tests/test_research_agent_runtime.py`

- [ ] **Step 1: Write failing runtime scenarios**

```python
def test_idle_ticks_do_not_call_the_model(tmp_path: Path) -> None:
    calls: list[str] = []
    runtime = runtime_fixture(tmp_path, model=lambda request: calls.append(request.agent_family_id))
    first = runtime.tick(NOW)
    second = runtime.tick(NOW + dt.timedelta(seconds=30))
    assert first.status == second.status == "idle"
    assert calls == []


def test_two_families_run_separate_cycles_and_restart_without_duplicates(tmp_path: Path) -> None:
    runtime = runtime_fixture(tmp_path)
    runtime.ingest((evidence_fixture("opportunity_manager", 1), evidence_fixture("systematic_quant", 1)))
    first = runtime.tick(NOW + dt.timedelta(minutes=2))
    second = runtime.tick(NOW + dt.timedelta(minutes=2, seconds=30))
    restarted = runtime_fixture(tmp_path)
    third = restarted.tick(NOW + dt.timedelta(minutes=3))
    assert {first.agent_family_id, second.agent_family_id} == {"opportunity_manager", "systematic_quant"}
    assert third.status == "idle"
    assert len(restarted.store.results()) == 2
```

- [ ] **Step 2: Verify runtime tests fail**

Run: `uv run pytest -q tests/test_research_agent_runtime.py`

Expected: FAIL because the runtime does not exist.

- [ ] **Step 3: Implement one tick**

```python
class ResearchAgentRuntime:
    def ingest(self, evidence: tuple[ResearchAgentEvidenceV1, ...]) -> int:
        return sum(self._store.append_evidence(item) for item in evidence)
    def tick(self, now: dt.datetime) -> ResearchAgentTickResult:
        self._store.recover_interrupted(now)
        self.ingest(collect_research_agent_evidence(self._sources, now=now))
        runnable = runnable_actors(self._store.pending_evidence(), self._store.open_work(), now=now)
        if not runnable:
            return ResearchAgentTickResult(status="idle", agent_family_id=None, cycle_id=None, model_calls=0)
        evidence = runnable[0]
        cycle = self._store.start_cycle(evidence, started_at=now)
        decision = self._decisions.decide(self._request(cycle))
        result = self._actions.execute(cycle, decision)
        self._store.finish_cycle(cycle, result)
        return ResearchAgentTickResult(status=result.status.value, agent_family_id=cycle.agent_family_id,
                                       cycle_id=cycle.cycle_id, model_calls=1)
```

On typed source, model or action failure, create a terminal result and backoff wake before advancing the
cursor. Do not catch unexpected `BaseException`; the foreground service must exit so launchd can restart it.

- [ ] **Step 4: Implement non-blocking singleton service lease and foreground loop**

```python
@contextmanager
def research_agent_runtime_lease(path: Path) -> Iterator[None]:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ResearchAgentRuntimeLeaseUnavailableError from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_research_agent_foreground(runtime: ResearchAgentRuntime, clock: Clock, wait: Wait) -> None:
    while True:
        runtime.tick(clock())
        wait(30.0)
```

The lease file is mode `600`, `O_NOFOLLOW`, current-user-owned and locked with `LOCK_EX | LOCK_NB`.
Cancellation/termination occurs between bounded ticks; no model process remains resident.

- [ ] **Step 5: Run runtime tests**

Run: `uv run pytest -q tests/test_research_agent_runtime.py`

Expected: PASS for idle, two-family sequencing, failure isolation, interrupted recovery, lease rejection and
global heavy serialization.

- [ ] **Step 6: Commit Task 7**

```bash
git add trading_agent/research_agent_runtime.py tests/test_research_agent_runtime.py
git commit -m "feat: run persistent research actors sequentially"
```

### Task 8: Family-isolated Hermes results and dashboard truth

**Files:**
- Create: `trading_agent/research_agent_hermes.py`
- Modify: `trading_agent/dashboard_agent_runtime.py`
- Modify: `run_dashboard_publisher.py`
- Test: `tests/test_research_agent_hermes.py`
- Test: `tests/test_dashboard_agent_runtime.py`

- [ ] **Step 1: Write failing projection tests**

```python
def test_results_project_as_separate_agent_families_and_replay_once(tmp_path: Path) -> None:
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    results = (result_fixture("opportunity_manager"), result_fixture("systematic_quant"))
    with store.writer() as writer:
        first = project_research_agent_results(results, writer)
        replay = project_research_agent_results(results, writer)
    assert first.inserted == 2
    assert replay.inserted == 0
    assert {event.agent_family for event in HermesDeliveryReader(store.path).events()} == {
        "opportunity_manager", "systematic_quant"
    }


def test_dashboard_readiness_comes_from_real_actor_cycles(tmp_path: Path) -> None:
    seed_terminal_cycle(tmp_path, "opportunity_manager")
    projection, agents = project_agent_runtime(tmp_path, now=NOW)
    assert next(item for item in agents if item.agent_id == "opportunity_manager").runtime_state == "idle"
    assert next(item for item in agents if item.agent_id == "day_trading").runtime_state == "unavailable"
```

- [ ] **Step 2: Verify projection tests fail**

Run: `uv run pytest -q tests/test_research_agent_hermes.py tests/test_dashboard_agent_runtime.py`

Expected: FAIL because actor results are not projected and readiness is still publisher-generated.

- [ ] **Step 3: Implement result-to-Hermes projection**

```python
def project_research_agent_results(
    results: tuple[ResearchAgentResultV1, ...],
    writer: HermesDeliveryWriter,
) -> HermesProjectionResult:
    records = tuple(
        HermesProjectionRecord(
            source_event_id=result.result_id,
            root_source_event_id=None,
            kind=HermesDeliveryKind.RESEARCH,
            market_id=result.market_id,
            agent_family=result.agent_family_id,
            lane_id=None,
            strategy_version=result.strategy_version,
            instrument_id=result.instrument_id,
            occurred_at=result.occurred_at,
            status=result.status.value,
            evidence_refs=result.evidence_refs,
            rendered_text=render_research_agent_result(result),
            payload_sha256=hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
        ) for result in results
    )
    return project_outcomes(records, writer)
```

Cap rendered text at 4,096 characters and use existing outbound redaction. Never combine families into one
delivery or verdict.

- [ ] **Step 4: Make dashboard runtime projection read the new journal**

Add an optional `cycle_database` parameter to `project_agent_runtime`. If initialized, derive each family's
latest `running`, `idle`, `failed` or `armed` state from real cycles/open work. Preserve the legacy receipt
projection only when the database does not yet exist. Remove `append_agent_runtime_readiness` from the
publisher's normal success path after the runtime service is configured.

- [ ] **Step 5: Run projection tests**

Run: `uv run pytest -q tests/test_research_agent_hermes.py tests/test_dashboard_agent_runtime.py tests/test_hermes_query_service.py`

Expected: PASS with two insertions, zero replay insertions and no blended verdict.

- [ ] **Step 6: Commit Task 8**

```bash
git add trading_agent/research_agent_hermes.py trading_agent/dashboard_agent_runtime.py run_dashboard_publisher.py tests/test_research_agent_hermes.py tests/test_dashboard_agent_runtime.py
git commit -m "feat: publish isolated research actor results"
```

### Task 9: Private config, CLI and single LaunchAgent

**Files:**
- Create: `trading_agent/research_agent_service_config.py`
- Create: `run_research_agent_runtime.py`
- Test: `tests/test_research_agent_service_cli.py`

- [ ] **Step 1: Write failing service and clean-main tests**

```python
def test_plist_contains_one_keepalive_service_and_no_secrets(tmp_path: Path) -> None:
    config, config_path, plist = provision_fixture(tmp_path)
    payload = plistlib.loads(plist.read_bytes())
    assert payload["Label"] == "ai.trading-agent.research-agent-runtime"
    assert payload["KeepAlive"] is True
    assert payload["RunAtLoad"] is True
    assert payload["StandardOutPath"] == payload["StandardErrorPath"] == "/dev/null"
    text = plist.read_text()
    assert "API_KEY" not in text and "TOKEN" not in text and "account" not in text.lower()


def test_activation_rejects_worktree_before_launchctl(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    code = main(("activate", "--config", str(config_fixture(tmp_path, project_root=WORKTREE)),
                 "--plist", str(tmp_path / "job.plist")), runner=lambda argv: calls.append(argv) or 0)
    assert code == 2
    assert calls == []
```

- [ ] **Step 2: Verify CLI tests fail**

Run: `uv run pytest -q tests/test_research_agent_service_cli.py`

Expected: FAIL because the config and CLI do not exist.

- [ ] **Step 3: Implement private immutable config and LaunchAgent bytes**

```python
class ResearchAgentServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    label: Literal["ai.trading-agent.research-agent-runtime"]
    project_root: Path
    uv_path: Path
    hermes_executable: Path
    model_id: str
    cycle_database: Path
    output_root: Path
    hermes_database: Path
    source_paths: ResearchAgentSourcePaths
    systematic: SystematicResearchActionConfig
```

Config and plist are current-user-owned, non-symlink regular files with exact mode `600`. The plist calls
absolute `uv run --offline python {project_root}/run_research_agent_runtime.py run --config {config_path}`,
uses one
label, `KeepAlive`, `RunAtLoad`, `ThrottleInterval=30`, `Umask=077` and `/dev/null` logs.

- [ ] **Step 4: Implement `provision`, `verify`, `tick`, `run`, `status`, `activate`**

`tick` runs exactly one cycle and writes a private report including actor, cycle, result, model calls and
`broker_mutation: 0`. `run` holds the service lease and loops. `status` reads only the journal and launchd
contract. `activate` first proves absolute main checkout, branch `main`, tracked-clean and
`HEAD == origin/main`; only then call `launchctl bootstrap gui/{uid} {plist_path}` and
`launchctl kickstart gui/{uid}/ai.trading-agent.research-agent-runtime`.
The `provision` parser exposes the Systematic one-shot paths as explicit `--systematic-context`,
`--systematic-receipt-root`, `--systematic-strategy-root`, `--systematic-manifest-root`,
`--systematic-queue-root`, `--systematic-input-csv`, `--systematic-foundation-manifest`,
`--systematic-artifact-root` and `--systematic-review-root` arguments; no path is guessed from credentials
or environment variables.

- [ ] **Step 5: Run CLI tests**

Run: `uv run pytest -q tests/test_research_agent_service_cli.py`

Expected: PASS for `--help`, bad config, provision/verify, idle tick, worktree activation rejection and
captured clean-main launchctl argv.

- [ ] **Step 6: Commit Task 9**

```bash
git add trading_agent/research_agent_service_config.py run_research_agent_runtime.py tests/test_research_agent_service_cli.py
git commit -m "feat: provision persistent research agent service"
```

### Task 10: End-to-end verification, manual QA and activation evidence

**Files:**
- Create: `docs/checkpoints/2026-08-02-six-agent-persistent-runtime-ko.md`
- Update tests only when a real defect found during this task requires a regression test.

- [ ] **Step 1: Run the focused suite**

```bash
uv run pytest -q \
  tests/test_research_agent_cycle_models.py \
  tests/test_research_agent_cycle_store.py \
  tests/test_research_agent_sources.py \
  tests/test_research_agent_wake_policy.py \
  tests/test_research_agent_decision.py \
  tests/test_research_agent_actions.py \
  tests/test_research_agent_systematic.py \
  tests/test_research_agent_runtime.py \
  tests/test_research_agent_hermes.py \
  tests/test_research_agent_service_cli.py \
  tests/test_dashboard_agent_runtime.py \
  tests/test_dashboard_kr_autonomous_bridge.py \
  tests/test_autonomous_research_cycle_cli.py \
  tests/test_hermes_query_service.py
```

Expected: PASS.

- [ ] **Step 2: Run Ruff and basedpyright on every changed Python file**

Run:

```bash
uv run ruff check \
  trading_agent/research_agent_*.py \
  trading_agent/dashboard_agent_runtime.py \
  trading_agent/dashboard_kr_autonomous_bridge.py \
  run_research_agent_runtime.py run_autonomous_research_cycle.py run_dashboard_publisher.py \
  tests/test_research_agent_*.py
uv run basedpyright \
  trading_agent/research_agent_*.py \
  trading_agent/dashboard_agent_runtime.py \
  trading_agent/dashboard_kr_autonomous_bridge.py \
  run_research_agent_runtime.py run_autonomous_research_cycle.py run_dashboard_publisher.py
```

Expected: both exit 0.

- [ ] **Step 3: Run the required broker-boundary regression tests**

Run: `uv run pytest -q tests/test_alpaca_trading_http.py tests/test_alpaca_paper_execution.py tests/test_paper_mutation_source_validation.py`

Expected: PASS, including rejection of any non-`https://paper-api.alpaca.markets` trading URL before HTTP.

- [ ] **Step 4: Manually exercise the CLI surface**

Run:

```bash
qa_root="$(mktemp -d)"
chmod 700 "$qa_root"
uv run python run_research_agent_runtime.py --help
uv run python run_research_agent_runtime.py tick --config /tmp/nonexistent-research-agent.json
uv run python run_research_agent_runtime.py provision \
  --project-root "$PWD" \
  --uv-path /Users/goyunseo/.local/bin/uv \
  --hermes-executable /Users/goyunseo/.local/bin/hermes \
  --cycle-database "$qa_root/cycles.sqlite3" \
  --runtime-output-root "$qa_root/runtime" \
  --hermes-database /Users/goyunseo/work/trading-recommendation-agent/outputs/hermes/delivery.sqlite3 \
  --experiment-ledger /Users/goyunseo/work/trading-recommendation-agent/outputs/experiment_control/experiment_ledger.sqlite3 \
  --lane-review-database /Users/goyunseo/work/trading-recommendation-agent/outputs/lane_control/lane_review.sqlite3 \
  --source-outputs-root /Users/goyunseo/work/trading-recommendation-agent/outputs \
  --market-context-root /Users/goyunseo/work/trading-recommendation-agent/outputs/market_context \
  --day-session-root /Users/goyunseo/work/trading-recommendation-agent/outputs/live_sessions \
  --swing-shadow-database /Users/goyunseo/work/trading-recommendation-agent/outputs/us_swing_shadow/operating/swing_shadow.sqlite3 \
  --swing-review-database /Users/goyunseo/work/trading-recommendation-agent/outputs/us_swing_shadow/operating/swing_review.sqlite3 \
  --systematic-context "$PWD/examples/research/researcher-context-v1.json" \
  --systematic-receipt-root "$qa_root/systematic/receipts" \
  --systematic-strategy-root "$qa_root/systematic/strategies" \
  --systematic-manifest-root "$qa_root/systematic/manifests" \
  --systematic-queue-root "$qa_root/systematic/queue" \
  --systematic-input-csv "$PWD/examples/example_intraday.csv" \
  --systematic-foundation-manifest "$PWD/examples/data/us-orb-data-foundation-v1.json" \
  --systematic-artifact-root "$qa_root/systematic/artifacts" \
  --systematic-review-root "$qa_root/systematic/reviews" \
  --config "$qa_root/research-agent.json" \
  --plist "$qa_root/ai.trading-agent.research-agent-runtime.plist"
uv run python run_research_agent_runtime.py verify \
  --config "$qa_root/research-agent.json" \
  --plist "$qa_root/ai.trading-agent.research-agent-runtime.plist"
```

Expected: help exit 0, nonexistent config exit 2 with redacted blocked report, provision/verify exit 0 and
mode-600 artifacts.

- [ ] **Step 5: Run the foreground Manual QA Gate**

Use a `mktemp -d` private root and the actual Hermes executable. Seed two typed evidence records from
existing project production outputs, one Opportunity and one Systematic/Reviewer source. Run successive
`tick` commands until both terminal results exist, run two more idle ticks, terminate and restart the
foreground service, then run `status`.

Expected observations:

- two distinct actor families and cursor sequences;
- Systematic generated artifact, sandbox trial, Reviewer result and next-context reference;
- two idle ticks with `model_calls: 0`;
- restart returns the same result count and zero duplicate Hermes deliveries;
- `order_authority: false`, `allocation_authority: false`, `lifecycle_authority: false`, broker mutation 0.

- [ ] **Step 6: Run the full suite once**

Run: `uv run pytest -q`

Expected: no new failures. If the five known `tests/test_dashboard_publisher_system_authority.py` baseline
failures remain unchanged, record their exact names and confirm the same failures on the branch base; do not
alter those unrelated tests.

- [ ] **Step 7: Write the checkpoint and commit verified implementation**

Record exact commit SHA, commands, counts, manual artifact paths, real-versus-fixture evidence distinction,
idle model-call count, restart result, launchd state and broker mutation count in
`docs/checkpoints/2026-08-02-six-agent-persistent-runtime-ko.md`.

```bash
git add docs/checkpoints/2026-08-02-six-agent-persistent-runtime-ko.md
git commit -m "docs: record persistent research runtime verification"
```

- [ ] **Step 8: Activate only after the verified commit reaches clean `origin/main`**

On clean main, run `provision`, `verify`, `activate`, then `launchctl print gui/$(id -u)/ai.trading-agent.research-agent-runtime` and the CLI `status`. Do not activate from this worktree. If main integration is not yet authorized or completed, leave the goal active and report `waiting_for_clean_main_activation`; do not claim always-on operation.

Expected operational acceptance:

- LaunchAgent is running and restarts after one `launchctl kickstart -k`;
- real production evidence closes two different actor cycles;
- idle tick makes no model call;
- family delivery IDs remain unique and replay inserts zero;
- broker mutation remains 0.
