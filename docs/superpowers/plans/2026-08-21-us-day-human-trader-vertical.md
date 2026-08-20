# US Day Human-Trader Vertical Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one restart-safe US Day Agent that maintains a persistent market thesis, researches themes/catalysts/observable flow with multiple tool steps, publishes entry/stop/target recommendations, routes only eligible recommendations through the existing Alpaca Paper safety kernel, projects the full lifecycle to Dashboard v2, and improves through a Shadow-only Champion–Challenger Loop Engineer.

**Architecture:** Keep provider collection, evidence validation, risk sizing, broker mutation, and promotion as deterministic host services. Add a durable Day ResearchTask loop above existing canonical scanner/news/context/quote inputs; the strong reasoning model may choose research tools and submit a Trade Thesis but cannot price account risk or call a broker. Reuse `TradeSignalEnvelope`, `UsDayOperatingCoordinator`, the Paper mutation/reconciliation stack, Hermes delivery, Dashboard v2, market-close reports, and the US Forward Shadow controller instead of creating parallel execution systems.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite append-only stores, existing Hermes/Claude CLI adapters, Alpaca market data and exact Paper endpoint guard, pytest, Ruff, basedpyright

---

## Scope and prerequisites

This is the first implementation plan under `2026-08-21-specialist-trading-agents-loop-engineering-design.md`. It implements US Day only. Swing, Quant, KR Day Shadow, derivatives execution, fine-tuning, and debate swarms remain out of scope.

Before Task 1:

1. Finish and commit `docs/superpowers/plans/2026-08-20-us-forward-shadow-operating-vertical.md` and its currently uncommitted files.
2. Run its focused tests. The new Loop Engineer depends on `run_us_forward_shadow_tick.py` and `trading_agent/us_forward_shadow_runtime.py`; do not duplicate those files.
3. Execute this plan in an isolated worktree created at execution time. Do not import the current unrelated dirty worktree.

The implementation may read only canonical local evidence during an agent tick. Existing provider collectors retain network authority. The only mutation-capable trading client remains the existing Alpaca Paper stack guarded to `https://paper-api.alpaca.markets`.

## File map

### Create

- `trading_agent/day_agent_task_models.py`: persistent ResearchTask, step, budget, wake, and terminal contracts.
- `trading_agent/day_agent_task_store.py`: private append-only SQLite task/event store with restart recovery.
- `trading_agent/day_agent_tool_models.py`: bounded tool-call and observation protocol.
- `trading_agent/day_agent_tool_runtime.py`: allowlisted read/research tool dispatcher; no broker imports.
- `trading_agent/day_agent_reasoning.py`: strong-model step protocol and role-aware model routing.
- `trading_agent/day_agent_runtime.py`: bounded multi-step ResearchTask controller.
- `trading_agent/day_agent_research_bridge.py`: accepted research hypothesis/code submission to existing Day Discovery and Forward Shadow lineage.
- `trading_agent/us_day_situation_models.py`: current-session market map, theme, catalyst, leader, and observable-flow contracts.
- `trading_agent/us_day_situation_projection.py`: canonical scanner/news/context/quote/completed-bar evidence projection.
- `trading_agent/us_day_thesis_models.py`: Trade Thesis, recommendation decision, and immutable evidence lineage.
- `trading_agent/us_day_thesis_runtime.py`: Day reasoning prompt, validation, and signal projection.
- `trading_agent/us_day_thesis_store.py`: private immutable thesis/change artifacts and exact replay.
- `trading_agent/us_day_recommendation_card.py`: Korean recommendation, update, and no-trade cards.
- `trading_agent/us_day_signal_admission.py`: current-quote-validated thesis to existing Paper admission adapter.
- `trading_agent/us_day_agent_operating.py`: recommendation-to-`UsDayOperatingCoordinator` orchestration.
- `trading_agent/dashboard_us_day_live.py`: Day market map, recommendation, Paper lineage, and agent-version projector.
- `trading_agent/day_agent_version_models.py`: Champion, Challenger, change proposal, and comparison contracts.
- `trading_agent/day_agent_version_store.py`: append-only version and deployment decision store.
- `trading_agent/day_agent_loop_engineer.py`: close-report diagnosis and research-only Challenger generation.
- `trading_agent/day_agent_challenger_evaluation.py`: Champion–Challenger future-Shadow comparison and fixed promotion recommendation.
- `trading_agent/us_day_agent_service.py`: session-phase one-tick application service.
- `run_us_day_agent_tick.py`: scheduler-friendly local tick CLI.
- `tests/day_agent_support.py`: canonical task, situation, thesis, Paper, and version fixtures.
- `tests/test_day_agent_task_store.py`
- `tests/test_day_agent_runtime.py`
- `tests/test_day_agent_research_bridge.py`
- `tests/test_us_day_situation_projection.py`
- `tests/test_us_day_thesis_runtime.py`
- `tests/test_us_day_thesis_store.py`
- `tests/test_us_day_recommendation_card.py`
- `tests/test_us_day_signal_admission.py`
- `tests/test_us_day_agent_operating.py`
- `tests/test_dashboard_us_day_live.py`
- `tests/test_day_agent_loop_engineer.py`
- `tests/test_us_day_agent_service.py`
- `tests/test_us_day_agent_tick_cli.py`
- `tests/test_us_day_human_trader_e2e.py`
- `tests/fixtures/day-agent/stale-situation.json`: canonical malformed/stale CLI fixture.

### Modify

- `trading_agent/dashboard_snapshot_v2.py`: merge the live Day projection into existing Markets and Paper workspaces.
- `trading_agent/dashboard_models_v2.py`: allow Day-specific item kinds without adding another top-level workspace.
- `trading_agent/us_day_operating_models.py`: replace the ORB-only lane default with an explicit approved Day lane in the request.
- `trading_agent/us_day_operating_projection.py`: render recommendation ID, entry, stop, targets, theme, and agent version from safe persisted thesis data.
- `trading_agent/day_learning_report_models.py`: attach stage-level Day decision diagnostics and agent-version lineage.
- `trading_agent/day_learning_reports.py`: build the diagnostics from finalized recommendation/Paper events.
- `trading_agent/research_agent_decision.py`: route `day_trading` away from the legacy single-primary-action client; leave other families unchanged.
- `trading_agent/research_agent_runtime.py`: delegate Day evidence to the persistent Day runtime instead of constructing a one-shot decision request.
- `pyproject.toml`: include the new CLI and modules in basedpyright coverage.

## Non-negotiable boundaries

- A newly generated research hypothesis, strategy capsule, prompt version, tool policy, or Challenger cannot submit Paper orders in the same session.
- Only the deployed Champion version may create a Paper-eligible Trade Thesis.
- The LLM may propose entry, stop, target logic and a thesis. `Risk Kernel` owns quantity and account risk; the Paper executor owns all network mutation.
- Missing current-session completed bar, stale quote, missing spread, incomplete news/source coverage, closed session, or unresolved Paper state produces `NO_TRADE`/blocked output before mutation.
- Natural-language claims use `EvidenceRef`; unobserved flow claims use `inferred`, never `observed`.
- All recommendation, order, fill, protective OCO, exit, close report, and agent-version transitions preserve immutable lineage.

---

### Task 1: Add durable Day ResearchTask state

**Files:**
- Create: `trading_agent/day_agent_task_models.py`
- Create: `trading_agent/day_agent_task_store.py`
- Create: `tests/test_day_agent_task_store.py`
- Create: `tests/day_agent_support.py`

- [ ] **Step 1: Write failing model and restart tests**

Add tests for task creation, ordered evidence refs, append-only steps, exact replay, one open step, budget exhaustion, scheduled wake, terminal tasks, private mode `0600`, symlink/hardlink rejection, and reopen recovery.

```python
def test_research_task_survives_restart_without_duplicate_step(tmp_path: Path) -> None:
    path = tmp_path / "day-agent.sqlite3"
    task = day_task(task_id="task-20260821-NVDA")
    step = day_step(task, sequence=1, action=DayAgentAction.INSPECT_SITUATION)
    with DayAgentTaskStore(path).writer() as writer:
        assert writer.create_task(task) is True
        assert writer.append_step(step) is True
        assert writer.append_step(step) is False
    reopened = DayAgentTaskStore(path).reader()
    assert reopened.task(task.task_id) == task
    assert reopened.steps(task.task_id) == (step,)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run pytest -q tests/test_day_agent_task_store.py`

Expected: FAIL with missing `day_agent_task_models` and `day_agent_task_store` modules.

- [ ] **Step 3: Implement frozen contracts**

Define `DayAgentTaskState = OPEN | WAITING | COMPLETED | BLOCKED`, `DayAgentAction`, `DayAgentBudget`, `DayAgentResearchTask`, and `DayAgentTaskStep`. Use content-derived SHA-256 `step_id`; require timezone-aware UTC-normalizable timestamps, sorted unique evidence refs, sequence starting at one, and nonnegative remaining budgets.

```python
class DayAgentAction(StrEnum):
    INSPECT_SITUATION = "inspect_situation"
    READ_CATALYSTS = "read_catalysts"
    COMPARE_LEADERS = "compare_leaders"
    SEARCH_PAST_CASES = "search_past_cases"
    RUN_LIGHT_EXPERIMENT = "run_light_experiment"
    ASK_CRITIC = "ask_critic"
    SUBMIT_TRADE_THESIS = "submit_trade_thesis"
    SUBMIT_RESEARCH_HYPOTHESIS = "submit_research_hypothesis"
    DEFER = "defer"

class DayAgentBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    remaining_model_calls: int = Field(ge=0, le=12)
    remaining_tool_calls: int = Field(ge=0, le=24)
    remaining_runtime_seconds: int = Field(ge=0, le=300)
```

- [ ] **Step 4: Implement one authoritative SQLite store**

Create `day_tasks` and `day_task_steps` tables in one mode-`0600` database. Use `BEGIN IMMEDIATE` for writes, foreign keys, unique `(task_id, sequence)`, and payload hashes. `append_step` returns `False` only for exact replay and raises `DayAgentTaskConflictError` for divergent replay. Readers open query-only and reject unsafe file metadata.

- [ ] **Step 5: Run focused verification and commit**

Run: `uv run pytest -q tests/test_day_agent_task_store.py`

Expected: PASS.

Commit:

```bash
git add trading_agent/day_agent_task_models.py trading_agent/day_agent_task_store.py tests/day_agent_support.py tests/test_day_agent_task_store.py
git commit -m "feat(day-agent): add durable research tasks"
```

---

### Task 2: Replace Day's one-shot decision with a bounded tool loop

**Files:**
- Create: `trading_agent/day_agent_tool_models.py`
- Create: `trading_agent/day_agent_tool_runtime.py`
- Create: `trading_agent/day_agent_reasoning.py`
- Create: `trading_agent/day_agent_runtime.py`
- Create: `tests/test_day_agent_runtime.py`
- Modify: `trading_agent/research_agent_decision.py`
- Modify: `trading_agent/research_agent_runtime.py`

- [ ] **Step 1: Write failing multi-step and authority tests**

Prove that a Day task can execute `inspect_situation → read_catalysts → compare_leaders → submit_trade_thesis` across four model calls, persists after every step, resumes after process restart, stops at budget, and cannot dispatch unknown, provider, credential, account, position, order, sizing, or mutation tools.

```python
def test_day_agent_chooses_multiple_tools_and_resumes_after_restart(tmp_path: Path) -> None:
    client = ScriptedDayReasoner((inspect_call(), catalyst_call(), leader_call(), thesis_call()))
    first = run_day_agent_task(runtime(tmp_path, client, max_steps=2), open_task())
    assert first.state is DayAgentTaskState.WAITING
    resumed = run_day_agent_task(runtime(tmp_path, client, max_steps=4), first.task)
    assert resumed.state is DayAgentTaskState.COMPLETED
    assert tuple(step.action for step in resumed.steps) == (
        DayAgentAction.INSPECT_SITUATION,
        DayAgentAction.READ_CATALYSTS,
        DayAgentAction.COMPARE_LEADERS,
        DayAgentAction.SUBMIT_TRADE_THESIS,
    )
```

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run pytest -q tests/test_day_agent_runtime.py`

Expected: FAIL because the tool protocol and runtime do not exist.

- [ ] **Step 3: Implement the bounded model protocol**

Define a response union containing exactly one `DayAgentToolCall`, `DayAgentThesisSubmission`, `DayAgentHypothesisSubmission`, or `DayAgentDefer`. The request includes current task, prior steps, bounded observations, allowed tool names, and remaining budget. Model-role routing must use `reasoning` for Trade Thesis and `coding` for experiment code; extraction-only models cannot submit theses.

```python
class DayAgentToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["tool_call"] = "tool_call"
    action: DayAgentAction
    arguments: dict[str, str] = Field(max_length=8)
    reason: str = Field(min_length=8, max_length=500)

class DayAgentReasoningClient(Protocol):
    @property
    def role(self) -> Literal["reasoning", "coding"]: ...
    def next_step(self, request: DayAgentReasoningRequest) -> DayAgentReasoningResponse: ...
```

- [ ] **Step 4: Implement the allowlisted host dispatcher**

`DayAgentToolRuntime` maps enum members to injected read-only callables. Reject arguments not declared by each tool. Return `DayAgentToolObservation` with bounded JSON, evidence refs, observed time, and content hash. Assert in tests that the module has no imports whose names contain `alpaca_paper`, `paper_mutation`, `order`, `account`, `position`, `balance`, or `credential`.

- [ ] **Step 5: Implement the restart-safe controller**

Loop until submission, defer, budget exhaustion, or `max_steps`. Persist the model decision before tool execution and the observation after execution as distinct steps. On restart, re-dispatch only a decision with no recorded observation; exact observation replay must be idempotent. Convert model/schema/tool failures to a blocked task with stable reason code while preserving the Champion.

- [ ] **Step 6: Route only Day away from the legacy one-call client**

In `research_agent_decision.py`, preserve `max_model_calls: Literal[1]` for legacy families. Add an explicit guard that rejects constructing a legacy `ResearchAgentDecisionRequest` for `day_trading` once `DayAgentRuntime` is enabled. In `research_agent_runtime.py`, inject a `DayAgentRuntime` protocol and delegate selected Day evidence to its persistent task tick before the one-shot decision request is constructed. Convert the Day terminal result back to the existing cycle/result projection only after the task reaches `COMPLETED`, `BLOCKED`, or a scheduled `WAITING` boundary. Do not change Opportunity, Swing, Quant, Derivatives, or Market Context behavior in this plan.

- [ ] **Step 7: Verify and commit**

Run:

```bash
uv run pytest -q tests/test_day_agent_runtime.py tests/test_research_agent_decision.py tests/test_research_agent_runtime.py
```

Expected: PASS; legacy family tests remain unchanged.

Commit: `feat(day-agent): run persistent research tool loop`

---

### Task 3: Connect agent-generated hypotheses to Day Discovery and future Shadow

**Files:**
- Create: `trading_agent/day_agent_research_bridge.py`
- Create: `tests/test_day_agent_research_bridge.py`

- [ ] **Step 1: Write failing research-lineage tests**

Prove a `SUBMIT_RESEARCH_HYPOTHESIS` response containing falsifiable text, mechanism, baseline, source citations, general Python source, and at most four free parameters becomes one `ProposedHypothesis`, runs through the existing `DayDiscoveryLoop`, publishes a research-only Strategy Capsule, and is first eligible only on a later completed bar. Reject same-session Paper eligibility, missing citations, unverifiable data requests, unsafe Python, and duplicate semantic hypotheses.

```python
def test_agent_hypothesis_enters_existing_future_only_discovery(tmp_path: Path) -> None:
    result = submit_day_agent_hypothesis(
        accepted_hypothesis_submission(),
        discovery_bridge_services(tmp_path),
    )
    assert result.accepted is True
    assert result.capsule_id is not None
    assert result.first_shadow_eligible_at > accepted_hypothesis_submission().submitted_at
    assert result.order_authority is False
```

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run pytest -q tests/test_day_agent_research_bridge.py`

Expected: FAIL because the bridge does not exist.

- [ ] **Step 3: Translate the persisted submission into existing research contracts**

Build `ResearchHypothesisCard`, `ResearchSource`, `LlmCallReceipt`, `CandidateStrategyDraft`, and `ProposedHypothesis` from the persisted Day task step and model receipt. Use a `FixedHypothesisGenerator` in a `ResearcherPipeline` so the host does not ask a second model to rewrite the same hypothesis. Preserve the task ID, step ID, prompt hash, response hash, source refs, model ID, and Agent version in experiment-ledger lineage.

- [ ] **Step 4: Reuse Day Discovery and Forward Shadow**

Call `DayDiscoveryLoop.run` with the current bounded `DayDiscoveryEvidenceView` and a `ResearcherContext` derived from the task. Let existing criticism, sandbox, capsule verification, budget, holdout, exploration policy, and future-only admission own acceptance. If accepted, queue the capsule for the next effective XNYS exploration policy; never write execution eligibility or Paper authority from this bridge.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest -q tests/test_day_agent_research_bridge.py tests/test_day_discovery_loop.py \
  tests/test_day_strategy_capsule.py tests/test_day_forward_probe_bridge.py \
  tests/test_us_forward_shadow_runtime.py
```

Expected: PASS.

Commit: `feat(day-agent): bind research tasks to future discovery`

---

### Task 4: Build the canonical human-trader situation map

**Files:**
- Create: `trading_agent/us_day_situation_models.py`
- Create: `trading_agent/us_day_situation_projection.py`
- Create: `tests/test_us_day_situation_projection.py`

- [ ] **Step 1: Write failing evidence and market-time tests**

Cover exact current XNYS session, latest completed bar, scanner/news/context/quote freshness, complete source coverage, candidate identity, headline timestamps, inferred-vs-observed flow, deterministic theme membership, leader ranking, and fail-closed behavior for stale/missing inputs.

```python
def test_situation_map_links_theme_catalyst_flow_and_leader_evidence() -> None:
    situation = project_us_day_situation(canonical_situation_inputs())
    theme = situation.themes[0]
    assert theme.state is ThemeState.EMERGING
    assert theme.catalysts[0].headline == "Semiconductor equipment demand accelerates"
    assert theme.leaders[0].symbol == "NVDA"
    assert theme.leaders[0].flow.observation_kind is FlowObservationKind.OBSERVED
    assert all(claim.evidence_refs for claim in theme.claims)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run pytest -q tests/test_us_day_situation_projection.py`

Expected: FAIL because situation modules do not exist.

- [ ] **Step 3: Implement strict situation contracts**

Define `ThemeState`, `FlowObservationKind`, `CatalystEvidence`, `ObservableFlow`, `LeaderCandidate`, `ThemeMap`, and `UsDaySituationMap`. Flow fields are limited to observed quote sizes/spread, relative volume, dollar volume, VWAP relation, breakout absorption proxy, and cross-symbol relative strength. `inferred` claims require an explicit inference rule and observed source refs.

```python
class ObservableFlow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    observation_kind: FlowObservationKind
    relative_volume: Decimal = Field(ge=0)
    dollar_volume: Decimal = Field(ge=0)
    spread_bps: Decimal = Field(ge=0)
    bid_size: int = Field(ge=0)
    ask_size: int = Field(ge=0)
    vwap_relation: Literal["above", "below", "crossing", "unavailable"]
    inference_rule: str | None = Field(default=None, max_length=500)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
```

- [ ] **Step 4: Project only from existing canonical sources**

Accept `UsOpportunityScannerBundle`, parsed `AlpacaNewsArticle` records whose receipts are in `AlpacaNewsOpportunityEvidenceBundle`, `MarketContextSnapshot`, `UsQuotePolicyEvidence`, and the current completed-bar bundle already used by US Forward Shadow. Group headlines by shared symbols and normalized bounded keywords; compute deterministic theme and leader features. Do not ask the LLM to invent raw measurements. The reasoning model may label theme meaning later from this evidence.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest -q tests/test_us_day_situation_projection.py tests/test_alpaca_news_opportunity_evidence.py tests/test_us_scanner_research_evidence.py tests/test_us_quote_actionability_evidence.py tests/test_market_context_models.py
```

Expected: PASS.

Commit: `feat(day-agent): project US themes and observable flow`

---

### Task 5: Generate evidence-bound Trade Theses and recommendations

**Files:**
- Create: `trading_agent/us_day_thesis_models.py`
- Create: `trading_agent/us_day_thesis_runtime.py`
- Create: `trading_agent/us_day_thesis_store.py`
- Create: `trading_agent/us_day_recommendation_card.py`
- Create: `tests/test_us_day_thesis_runtime.py`
- Create: `tests/test_us_day_thesis_store.py`
- Create: `tests/test_us_day_recommendation_card.py`

- [ ] **Step 1: Write failing thesis tests**

Test `RECOMMEND`, `WATCH`, `NO_TRADE`, and `INSUFFICIENT_EVIDENCE`. A recommendation must contain theme, catalyst, leader rationale, observable-flow rationale, entry, stop, targets, invalidation, confidence, Champion version, Playbook, and exact evidence refs. Reject fabricated refs, stale situation IDs, non-Champion versions, invalid price ordering, claims longer than limits, and recommendation timestamps preceding evidence availability.

```python
def test_reasoner_emits_evidence_bound_human_trader_thesis() -> None:
    result = reason_trade_thesis(reasoner_response(), champion(), situation())
    assert result.decision is DayTradeDecision.RECOMMEND
    assert result.theme_name == "semiconductor_infrastructure"
    assert result.symbol == "NVDA"
    assert result.stop_price < result.entry_price < result.targets[0].price
    assert result.agent_version_id == champion().version_id
    assert set(result.evidence_refs) <= set(situation().evidence_refs)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run pytest -q tests/test_us_day_thesis_runtime.py`

Expected: FAIL because thesis modules do not exist.

- [ ] **Step 3: Implement the Trade Thesis schema**

Use `Decimal` prices and a content-derived `thesis_id`. Represent change events append-only; never update the original thesis. `RECOMMEND` requires exactly one symbol and at least two targets. Other decisions forbid entry/stop/targets and require a reason code.

```python
class DayTradeDecision(StrEnum):
    RECOMMEND = "recommend"
    WATCH = "watch"
    NO_TRADE = "no_trade"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"

class UsDayTradeThesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    thesis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: DayTradeDecision
    situation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    theme_name: str
    symbol: str | None
    entry_price: Decimal | None
    stop_price: Decimal | None
    targets: tuple[TradeTarget, ...]
    invalidation_rule: str
    rationale: str
    confidence_bps: int = Field(ge=0, le=10_000)
    evidence_refs: tuple[EvidenceRef, ...]
    observed_at: AwareDatetime
    valid_until: AwareDatetime
```

- [ ] **Step 4: Implement reasoning and host validation**

The prompt contains only the bounded situation map, current Champion Playbooks, past analogous outcomes returned by tools, and explicit instructions to distinguish observed from inferred flow. Parse the model response, then host-validate every evidence ref and number against the situation map. The model can select prices from current bars/quote and explain structure; it cannot output quantity, notional, account risk, broker, or endpoint fields.

- [ ] **Step 5: Project the accepted thesis to existing signal contracts**

For `RECOMMEND`, create `TradeSignalEnvelope` with `AgentFamily.DAY_TRADING`, `MarketId.US_EQUITIES`, the Champion strategy lane/version, `CURRENT_QUOTE_VALIDATED`, and exact `QuoteValidation`. Use `thesis_id` as `signal_id` and recommendation lineage ID. For other decisions create a terminal thesis artifact without a signal.

- [ ] **Step 6: Persist immutable history and queue the user card**

Publish the original thesis at `theses/<thesis_id>.json` and every later `유지`, `진입 취소`, `논리 무효화`, `부분 청산`, and `종료` change as a content-addressed child artifact with `parent_event_id`. Exact replay returns `False`; divergent replay, unsafe permissions, symlinks, and hardlinks fail closed. For `RECOMMEND`, save the compatibility `Recommendation` in the existing session `PaperStore` and queue one `RecommendationAlert` containing a Korean card with symbol, theme, catalyst, leader/flow evidence, entry, stop, targets, invalidation, confidence, and Agent version. Queue `NO_TRADE` and `INSUFFICIENT_EVIDENCE` as explicit terminal cards in the thesis store without creating a Paper recommendation.

- [ ] **Step 7: Verify and commit**

Run:

```bash
uv run pytest -q tests/test_us_day_thesis_runtime.py tests/test_us_day_thesis_store.py \
  tests/test_us_day_recommendation_card.py tests/test_recommendation_signal_projection.py \
  tests/test_trade_signal_publication.py tests/test_alert_outbox.py
```

Expected: PASS.

Commit: `feat(day-agent): generate evidence-bound trade theses`

---

### Task 6: Connect eligible Champion recommendations to Alpaca Paper

**Files:**
- Create: `trading_agent/us_day_signal_admission.py`
- Create: `trading_agent/us_day_agent_operating.py`
- Create: `tests/test_us_day_signal_admission.py`
- Create: `tests/test_us_day_agent_operating.py`
- Modify: `trading_agent/us_day_operating_models.py`
- Modify: `trading_agent/us_day_operating_projection.py`

- [ ] **Step 1: Write failing eligibility and endpoint tests**

Cover Champion-only eligibility, current XNYS session, latest completed bar, quote validity/spread, strategy promotion record, execution eligibility, Paper auto-arm, fixed risk config, exact endpoint rejection before HTTP, recommendation/order ID equality, protective OCO, partial fill, cancel, rejection, reconciliation, EOD flat, and exact replay.

```python
def test_champion_thesis_runs_complete_paper_lifecycle(tmp_path: Path) -> None:
    request = eligible_operating_request(tmp_path)
    result = operate_us_day_agent(request, operating_services(tmp_path))
    assert result.status is UsDayOperatingStatus.COMPLETED
    assert str(result.parent_intent_id) == request.thesis.thesis_id
    assert result.transitions == (
        UsDayOperatingTransition.ACTIONABLE,
        UsDayOperatingTransition.ENTRY_ACKNOWLEDGED,
        UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED,
        UsDayOperatingTransition.FLAT,
        UsDayOperatingTransition.RECONCILED,
        UsDayOperatingTransition.HERMES_RESULT_PROJECTED,
    )
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_us_day_signal_admission.py tests/test_us_day_agent_operating.py`

Expected: FAIL because the admission and operating adapters do not exist.

- [ ] **Step 3: Build the signal-to-admission adapter**

Convert only `CURRENT_QUOTE_VALIDATED` Champion signals into `PaperOrderAdmissionRequest`. Bind `LatestCompletedBar` from the same situation map; copy entry/stop/targets into `PaperOrderIntent`; set `IntentId(signal.signal_id)`; obtain `liquidity_allowed_quantity` from host market-liquidity policy; use the existing intraday pilot risk config. Fail before building a coordinator request when any lineage differs.

- [ ] **Step 4: Remove the ORB lane default from operating requests**

Require `lane_id` explicitly in `UsDayOperatingRequest`; accept only reviewed US Day lanes whose promotion and execution-eligibility records match the Champion version. Update existing ORB callers to pass `LaneId.INTRADAY_MOMENTUM` explicitly. Do not broaden accepted markets or execution authorities.

- [ ] **Step 5: Reuse the existing operating coordinator**

`operate_us_day_agent` constructs `UsDayOperatingRequest`, consumes the existing Paper arm, and calls `UsDayOperatingCoordinator`. It does not instantiate a broker client. Extend the safe Hermes actionable text using persisted thesis fields: theme, entry, stop, targets, invalidation, and agent version. Never include credentials, account ID, headers, or raw provider payloads.

After every acknowledged lifecycle transition, append the corresponding child thesis event and update the compatibility `PaperStore` recommendation state. The append-only thesis history remains authoritative; the compatibility row exists only for existing alert and dashboard readers.

- [ ] **Step 6: Verify Paper safety and commit**

Run:

```bash
uv run pytest -q tests/test_us_day_signal_admission.py tests/test_us_day_agent_operating.py \
  tests/test_us_day_operating_vertical_e2e.py tests/test_paper_promotion_boundary.py \
  tests/test_alpaca_paper_mutation_client.py tests/test_paper_protective_oco_lifecycle.py \
  tests/test_paper_reconciliation.py
```

Expected: PASS; the live Alpaca URL test fails closed before a transport call.

Commit: `feat(day-agent): route Champion theses to Paper safety kernel`

---

### Task 7: Project live themes, recommendations, and Paper lineage to Dashboard v2

**Files:**
- Create: `trading_agent/dashboard_us_day_live.py`
- Create: `tests/test_dashboard_us_day_live.py`
- Modify: `trading_agent/dashboard_models_v2.py`
- Modify: `trading_agent/dashboard_snapshot_v2.py`

- [ ] **Step 1: Write failing dashboard projection tests**

Test current market regime, top themes, leaders, active thesis, entry/stop/targets, thesis changes, `NO_TRADE`, current Champion, Shadow Challengers, Paper order/fill/exit, close review, trace edges, stale-source blocking, redaction, and deterministic item truncation.

```python
def test_dashboard_shows_recommendation_to_paper_lineage(tmp_path: Path) -> None:
    snapshot = dashboard_with_completed_day_trade(tmp_path)
    values = {item.item_id: item.value for item in snapshot.workspaces.markets.items}
    assert values["day.theme.1"] == "semiconductor_infrastructure · leading"
    assert values["day.recommendation.NVDA"] == "entry 121.00 · stop 118.50 · targets 123.50/126.00"
    paper = {item.item_id: item.value for item in snapshot.workspaces.paper.items}
    assert paper["day.paper.NVDA"] == "filled · protected · reconciled"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_dashboard_us_day_live.py`

Expected: FAIL because the live Day projector does not exist.

- [ ] **Step 3: Implement a query-only redacted projector**

Read the Day task/version store, immutable thesis artifacts, Hermes delivery store, and existing finalized Paper ledger through query-only readers. Emit at most 24 Markets items and 24 Paper items, newest actionable records first. Add `day_theme`, `day_recommendation`, and `day_agent_version` to `WorkspaceItemV2.kind`; do not add another top-level workspace.

- [ ] **Step 4: Merge without weakening existing dashboard authority**

In `collect_dashboard_snapshot_v2`, merge the Day projection after `project_session_terminals` for Markets and after `_paper_projection` for Paper. If the Day source is corrupt, block only the Day-derived portion with an explicit trace; do not erase valid calendar or finalized Paper evidence. Add `day-agent-live-reader-v1` to `reader_versions`.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest -q tests/test_dashboard_us_day_live.py tests/test_dashboard_models_v2.py \
  tests/test_dashboard_projection_system.py tests/test_dashboard_provider_execution.py \
  tests/test_dashboard_outbound_redaction.py
```

Expected: PASS.

Commit: `feat(dashboard): show live Day thesis and Paper lifecycle`

---

### Task 8: Add close review and Loop Engineer Champion–Challenger learning

**Files:**
- Create: `trading_agent/day_agent_version_models.py`
- Create: `trading_agent/day_agent_version_store.py`
- Create: `trading_agent/day_agent_loop_engineer.py`
- Create: `trading_agent/day_agent_challenger_evaluation.py`
- Create: `tests/test_day_agent_loop_engineer.py`
- Modify: `trading_agent/day_learning_report_models.py`
- Modify: `trading_agent/day_learning_reports.py`

- [ ] **Step 1: Write failing diagnostic and version tests**

Cover separate scores for market recognition, theme selection, catalyst interpretation, leader selection, flow interpretation, entry, exit, and execution quality. Prove the Loop Engineer proposes one bounded change set, creates a research-only Challenger, cannot alter safety/risk/broker/promotion policy, binds it to future Shadow, compares against the Champion, and records promote/reject/rollback recommendations without deploying itself.

```python
def test_loop_engineer_turns_leader_error_into_shadow_challenger(tmp_path: Path) -> None:
    proposal = run_loop_engineer(close_report_with_leader_error(), champion(), loop_services(tmp_path))
    assert proposal.problem_stage is DayDecisionStage.LEADER_SELECTION
    assert proposal.allowed_changes == (AgentChangeKind.LEADER_RANKING_POLICY,)
    challenger = DayAgentVersionStore(tmp_path / "versions.sqlite3").reader().challenger(proposal.version_id)
    assert challenger.order_authority is False
    assert challenger.deployment_state is AgentDeploymentState.SHADOW
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_day_agent_loop_engineer.py`

Expected: FAIL because version and Loop Engineer modules do not exist.

- [ ] **Step 3: Extend the close report with stage diagnostics**

Add `DayDecisionStage`, `DayDecisionDiagnostic`, and agent-version IDs. Diagnostics contain outcome, evidence IDs, reason codes, and calibrated scores; they do not contain a free-form profitability claim. Build them from finalized recommendation events, market data, and reconciled Paper events only after the market finalization watermark.

- [ ] **Step 4: Implement immutable Agent versions**

An `AgentVersion` contains model-role bindings, prompt hash, tool-policy hash, memory-retrieval policy hash, Playbook IDs, parent version, creation evidence, and deployment state. Initial Champion registration is explicit. Only a deterministic deployment function may change `SHADOW → CHAMPION`; the Loop Engineer produces a recommendation artifact, not deployment authority.

- [ ] **Step 5: Implement one-change Challenger generation**

Map the worst supported diagnostic stage to one allowlisted `AgentChangeKind`. The reasoning/coding model supplies bounded new prompt/tool/Playbook content; the host hashes and stores it. Reject changes mentioning endpoint, credential, account risk, order quantity, safety gates, promotion thresholds, or audit deletion.

- [ ] **Step 6: Evaluate on future Shadow**

Bind Champion and Challenger to the same future situation snapshots and use the completed US Forward Shadow controller for generated Playbook capsules. Compare theme timing, leader rank, recommendation calibration, MFE/MAE, cost-adjusted modeled result, `NO_TRADE` quality, and evidence fidelity. Require the configured multi-session minimum; do not let a same-session Challenger become Champion.

- [ ] **Step 7: Verify and commit**

Run:

```bash
uv run pytest -q tests/test_day_agent_loop_engineer.py tests/test_day_learning_report_models.py \
  tests/test_day_learning_reports.py tests/test_us_forward_shadow_runtime.py
```

Expected: PASS.

Commit: `feat(day-agent): learn with Shadow Champion challengers`

---

### Task 9: Add the session service, tick CLI, and end-to-end acceptance

**Files:**
- Create: `trading_agent/us_day_agent_service.py`
- Create: `run_us_day_agent_tick.py`
- Create: `tests/test_us_day_agent_service.py`
- Create: `tests/test_us_day_agent_tick_cli.py`
- Create: `tests/test_us_day_human_trader_e2e.py`
- Create: `tests/fixtures/day-agent/stale-situation.json`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing session-phase tests**

Test premarket map creation, regular-session completed-bar ticks, no duplicate recommendation on replay, stale input blocking, model failure preserving Champion, Paper recovery before new entry, cutoff behavior, EOD flat, close review, Challenger creation, restart recovery, compact CLI output, and zero network authority in help/bad-input paths.

```python
def test_natural_session_vertical_recommends_executes_reviews_and_learns(tmp_path: Path) -> None:
    service = prepared_human_trader_service(tmp_path)
    pre = service.tick(premarket_tick())
    live = service.tick(actionable_regular_tick())
    close = service.tick(close_tick())
    assert pre.market_map_id is not None
    assert live.recommendation_id is not None
    assert live.paper_status == "completed"
    assert close.market_close_report_id is not None
    assert close.challenger_version_id is not None
    assert service.tick(actionable_regular_tick()) == live
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_us_day_agent_service.py tests/test_us_day_agent_tick_cli.py tests/test_us_day_human_trader_e2e.py`

Expected: FAIL because the service and CLI do not exist.

- [ ] **Step 3: Implement one session-aware application service**

The service accepts canonical local source paths and an injected clock. It selects premarket, regular, entry-cutoff, EOD, or post-close behavior from the official XNYS calendar. A regular tick projects the situation, resumes/creates a task, runs the bounded tool loop, persists the thesis, publishes dashboard/Hermes output, and calls Paper only for an eligible Champion recommendation. Post-close finalizes Paper, writes the close report, then invokes Loop Engineer.

- [ ] **Step 4: Implement a scheduler-safe CLI**

Expose only private local artifact/store paths, executable/model bindings, and session configuration. Never accept a broker base URL argument; the existing Paper config loader owns the exact endpoint. Output compact JSON IDs and statuses, not prompts, headlines, account identifiers, headers, tokens, or raw broker responses.

Required manual commands:

```bash
uv run python run_us_day_agent_tick.py --help
uv run python run_us_day_agent_tick.py --situation tests/fixtures/day-agent/stale-situation.json
```

Expected: help exits `0`; stale input exits nonzero with `blocked` and a stable reason, before provider/model/broker calls.

- [ ] **Step 5: Run the complete focused verification**

Run:

```bash
uv run pytest -q tests/test_day_agent_*.py tests/test_us_day_situation_projection.py \
  tests/test_us_day_thesis_runtime.py tests/test_us_day_signal_admission.py \
  tests/test_us_day_agent_*.py tests/test_dashboard_us_day_live.py \
  tests/test_us_day_human_trader_e2e.py tests/test_us_day_operating_vertical_e2e.py \
  tests/test_paper_*.py tests/test_alpaca_paper_*.py
uv run ruff check trading_agent/day_agent_*.py trading_agent/us_day_*.py \
  trading_agent/dashboard_us_day_live.py run_us_day_agent_tick.py tests/test_day_agent_*.py \
  tests/test_us_day_*.py tests/test_dashboard_us_day_live.py
uv run basedpyright trading_agent/day_agent_*.py trading_agent/us_day_*.py \
  trading_agent/dashboard_us_day_live.py run_us_day_agent_tick.py
```

Expected: all targeted tests, Ruff, and basedpyright pass.

- [ ] **Step 6: Perform manual Paper safety QA**

Use local fakes for the first manual run. For a real Alpaca Paper smoke test, use only credentials from `~/.config/trading-agent/alpaca-paper.env` with exact mode `0600`, only after confirming the endpoint guard. Observe one natural setup; do not manufacture a recommendation. Verify recommendation card, Paper order, fill/partial fill or terminal rejection, protective OCO, cancellation/exit, final open-order/position reconciliation, Dashboard update, close report, and restart idempotency. Redact every identifier and secret from evidence.

- [ ] **Step 7: Commit the operating vertical**

```bash
git add trading_agent/us_day_agent_service.py run_us_day_agent_tick.py \
  tests/test_us_day_agent_service.py tests/test_us_day_agent_tick_cli.py \
  tests/test_us_day_human_trader_e2e.py pyproject.toml
git commit -m "feat(day-agent): run human-trader Paper vertical"
```

## Completion gate

The plan is complete only after a natural XNYS session produces one of these evidence-backed terminal outcomes:

1. An eligible Champion recommendation shows theme, catalyst, leader/flow evidence, entry, stop, targets, Paper lifecycle, EOD flat, close review, and Challenger result in Dashboard v2; or
2. `NO_TRADE`/blocked is correctly produced because the natural session had no eligible setup or a required safety input was missing, with no Paper mutation.

Synthetic and replay tests prove mechanics only. They do not satisfy the natural-session product gate and cannot support profitability claims.
