# Shared Day Research/Capsule Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the market-safe, append-only contracts that let the Day Trading Agent create unrestricted bounded hypotheses and generated Python capsules, start research-only Forward Probes on the next completed bar, preserve every attempt, and derive market-local review, report, and next-session policy artifacts without granting trading authority.

**Architecture:** Extend the existing experiment ledger to schema v10 instead of creating a second authority store. `HypothesisFamily` is the only market-neutral identity; every version, attempt binding, capsule, trial, outcome, review, report, policy, and eligibility reference is market-keyed and same-market validated. Generated code remains inside the existing sandbox and emits only a candidate; a deterministic host bridge creates `TradeSignalEnvelope`. Promotion and execution eligibility are separate immutable facts, and the shared layer cannot submit orders.

**Tech Stack:** Python 3.12, Pydantic v2 frozen models, SQLite append-only experiment ledger, existing generated-strategy sandbox/frame protocol, pytest, Ruff, basedpyright

---

## Scope and safety contract

Source of truth: `docs/superpowers/specs/2026-08-20-dual-market-autonomous-day-trading-three-loop-design.md`.

This plan delivers contracts and research-only services. It does not add an Alpaca client, a KIS/LS mutation adapter, live credentials, or a broker interface. The following invariants are release blockers:

- Only `HypothesisFamily` may cross markets. Raw returns, confidence intervals, trials, review seals, promotion, and eligibility never do.
- A new `HypothesisVersion` may observe only a completed bar strictly later than the bar/evidence used to register it.
- Generated code cannot choose a provider, risk, size, target calculation, or order request.
- Failed, rejected, aborted, timed-out, cancelled, and censored attempts remain in the multiple-testing count.
- A `PromotionDecision` is not execution authority; an `ExecutionEligibility` is not an order and has no mutation method.
- Reports and policies are immutable, market/session-keyed, and never combine US/KR P&L.
- Proposal breadth does not relax compute limits: no full-universe backtest, at most one heavy empirical process, a 10 GiB hard stop, and at most three active probe/shadow capsules per market.

## Dependency map

Extend these existing surfaces:

- `trading_agent/experiment_ledger_schema.py` and `trading_agent/experiment_ledger_store.py`: sole authority ledger and schema migration path.
- `trading_agent/strategy_research_results.py`: existing terminal `ResearchAttempt` identity/status.
- `trading_agent/generated_strategy_artifact.py`, `generated_strategy_runtime.py`, `generated_strategy_execution.py`, `generated_strategy_session.py`: artifact and sandbox receipts.
- `trading_agent/generated_strategy_protocol.py`: untrusted candidate frame boundary.
- `trading_agent/signal_contract_models.py`: host-owned `TradeSignalEnvelope`.
- `trading_agent/multi_market_experiment_models.py` and `multi_market_lifecycle_models.py`: market/agent/lifecycle vocabulary only; do not inherit the fixed-weekday trial behavior in `multi_market_trial_models.py`.
- `trading_agent/strategy_research_science_kernel.py` and `strategy_research_policy.py`: selection-bias and feedback-firewall inputs.

New files stay focused by invariant rather than forming a generic `day_loop` service:

- `trading_agent/day_hypothesis_models.py`
- `trading_agent/day_strategy_capsule_models.py`
- `trading_agent/day_forward_trial_models.py`
- `trading_agent/day_research_review_models.py`
- `trading_agent/day_learning_report_models.py`
- `trading_agent/day_research_ledger_schema.py`
- `trading_agent/day_research_ledger.py`
- `trading_agent/day_research_ledger_reader.py`
- `trading_agent/day_research_attempt_binding.py`
- `trading_agent/day_strategy_capsule.py`
- `trading_agent/day_discovery_loop.py`
- `trading_agent/day_forward_probe_bridge.py`
- `trading_agent/day_forward_probe_admission.py`
- `trading_agent/day_historical_evidence.py`
- `trading_agent/day_historical_evidence_store.py`
- `trading_agent/day_learning_policy.py`
- `trading_agent/day_learning_reports.py`
- `trading_agent/day_learning_report_store.py`
- `run_day_research_contract_smoke.py`

## Task 1: Define family and market-version identities

**Files:**
- Create: `trading_agent/day_hypothesis_models.py`
- Modify: `trading_agent/experiment_ledger_keys.py`
- Create: `tests/test_day_hypothesis_models.py`

- [ ] **Step 1: Write failing identity and boundary tests**

Cover exact canonical identity, parent lineage, aware timestamps, non-empty point-in-time evidence, and immutable market separation:

```python
def test_same_family_can_have_distinct_us_and_kr_versions() -> None:
    us = version_fixture(market_id=MarketId.US_EQUITIES)
    kr = version_fixture(market_id=MarketId.KR_EQUITIES)
    assert us.family_id == kr.family_id
    assert us.hypothesis_version_id != kr.hypothesis_version_id


def test_version_rejects_authority_or_profitability_claim() -> None:
    with pytest.raises(ValidationError, match="hypothesis_version_cannot_grant_authority"):
        version_fixture(trading_authority=True)
```

- [ ] **Step 2: Prove the test is red**

Run: `uv run pytest -q tests/test_day_hypothesis_models.py`

Expected: FAIL because `day_hypothesis_models` does not exist.

- [ ] **Step 3: Implement frozen contracts**

Add `HypothesisFamily`, `HypothesisVersion`, `MethodologyDeclaration`, `CostModelDeclaration`, and `SearchBudget`. Use `ConfigDict(frozen=True, extra="forbid")`, aware UTC datetimes, `Decimal`, sorted unique refs, open `methodology_tags: tuple[str, ...]`, and literal false fields:

```python
class HypothesisVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    hypothesis_version_id: str
    family_id: str
    parent_version_id: str | None
    market_id: MarketId
    registration_completed_bar_at: datetime
    first_shadow_eligible_at: datetime
    source_refs: tuple[str, ...]
    methodology_tags: tuple[str, ...]
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False
```

The validator must require `first_shadow_eligible_at > registration_completed_bar_at`; changing predictor, target, threshold, parameters, cost model, code hash, protocol hash, or data-manifest hash changes `hypothesis_version_id`.

- [ ] **Step 4: Add canonical key helpers and pass tests**

Add `day_hypothesis_family_key()` and `day_hypothesis_version_key()` to `experiment_ledger_keys.py`, based on its existing canonical JSON helper.

Run: `uv run pytest -q tests/test_day_hypothesis_models.py`

Expected: PASS.

- [ ] **Step 5: Commit the contract slice**

```bash
git add trading_agent/day_hypothesis_models.py trading_agent/experiment_ledger_keys.py tests/test_day_hypothesis_models.py
git commit -m "feat(day-research): add hypothesis family and version contracts"
```

## Task 2: Add the v10 append-only Day research ledger

**Files:**
- Create: `trading_agent/day_research_ledger_schema.py`
- Create: `trading_agent/day_research_ledger.py`
- Create: `trading_agent/day_research_ledger_reader.py`
- Modify: `trading_agent/experiment_ledger_schema.py`
- Modify: `trading_agent/experiment_ledger_store.py`
- Modify: `tests/test_experiment_ledger_store.py`
- Create: `tests/test_day_research_ledger.py`

- [ ] **Step 1: Write failing schema, replay, and same-market tests**

Tests must prove:

- a fresh store and every v1-v9 fixture migrate to v10;
- each new table has update/delete rejection triggers;
- identical registration returns `False`, conflicting content under one identity raises `ExperimentLedgerConflictError`;
- a version parent family exists;
- any market-keyed child whose market differs from its version is rejected before insert;
- the read-only reader opens SQLite with query-only mode and cannot write.

```python
def test_version_with_cross_market_parent_is_rejected(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiments.sqlite3")
    with store.writer() as writer:
        writer.register_day_hypothesis_family(family_fixture())
        with pytest.raises(InvalidExperimentLedgerSourceError):
            writer.register_day_hypothesis_version(version_fixture_with_mixed_parent())
```

- [ ] **Step 2: Prove the migration tests are red**

Run: `uv run pytest -q tests/test_experiment_ledger_store.py tests/test_day_research_ledger.py`

Expected: FAIL because schema version is 9 and Day tables/APIs are absent.

- [ ] **Step 3: Add schema v10 and append-only triggers**

Create normalized tables for:

1. `day_hypothesis_families`
2. `day_hypothesis_versions`
3. `day_research_attempt_bindings`
4. `day_strategy_capsules`
5. `day_forward_trials`
6. `day_forward_trial_events`
7. `day_promotion_decisions`
8. `day_execution_eligibility_events`
9. `day_exploration_policies`

Each table stores the canonical payload plus indexed identity/market/session columns. Add `CREATE_DAY_RESEARCH_SCHEMA_V10` to `CREATE_EXPERIMENT_LEDGER_SCHEMA`, set `EXPERIMENT_LEDGER_SCHEMA_VERSION = 10`, and add a v9→v10 migration branch without rewriting old rows.

- [ ] **Step 4: Add focused store functions and facade methods**

Keep SQL/canonical conflict logic in `day_research_ledger.py`; expose thin methods from `ExperimentLedgerWriter`. Put query-only lineage/session reads in `day_research_ledger_reader.py` and delegate from `ExperimentLedgerReader`. Centralize the rule:

```python
def require_same_market(*, parent_market: MarketId, child_market: MarketId) -> None:
    if child_market is not parent_market:
        raise InvalidDayResearchLedgerSourceError("day_research_cross_market_reference")
```

Do not add a second SQLite database or mutable status column.

- [ ] **Step 5: Run migration and append-only tests**

Run: `uv run pytest -q tests/test_experiment_ledger_store.py tests/test_day_research_ledger.py`

Expected: PASS, including `EXPERIMENT_LEDGER_SCHEMA_VERSION == 10`.

- [ ] **Step 6: Commit the ledger slice**

```bash
git add trading_agent/day_research_ledger_schema.py trading_agent/day_research_ledger.py trading_agent/day_research_ledger_reader.py trading_agent/experiment_ledger_schema.py trading_agent/experiment_ledger_store.py tests/test_experiment_ledger_store.py tests/test_day_research_ledger.py
git commit -m "feat(day-research): extend experiment ledger for day lineage"
```

## Task 3: Bind every research attempt without changing its scientific record

**Files:**
- Create: `trading_agent/day_research_attempt_binding.py`
- Modify: `trading_agent/day_research_ledger.py`
- Modify: `trading_agent/day_research_ledger_reader.py`
- Create: `tests/test_day_research_attempt_binding.py`
- Modify: `tests/test_experiment_ledger_store.py`

- [ ] **Step 1: Write failing all-attempt accounting tests**

Create attempts for `SUCCEEDED`, `FAILED`, `ABORTED`, `TIMED_OUT`, `CANCELLED`, and `CENSORED`. Prove each existing `ResearchAttempt.attempt_id` can be bound exactly once to `market_id`, `hypothesis_version_id`, and generated/builtin artifact ref; all six appear in `attempted_variants`.

- [ ] **Step 2: Prove the tests are red**

Run: `uv run pytest -q tests/test_day_research_attempt_binding.py tests/test_experiment_ledger_store.py`

Expected: FAIL because no binding contract/store method exists.

- [ ] **Step 3: Implement `DayResearchAttemptBinding`**

Reference the existing `ResearchAttempt`; do not duplicate or silently mutate it. A Day attempt is admissible only after both records exist. The binding must include attempt ID, market, hypothesis version, artifact ref, multiple-testing family, search-budget debit, and bound timestamp. Reject missing attempt/version, cross-market links, negative debit, and content conflicts.

- [ ] **Step 4: Add reader aggregation and pass tests**

Add `read_day_attempts_for_review(market_id, hypothesis_version_id)` returning all terminal statuses, never just successful trials.

Run: `uv run pytest -q tests/test_day_research_attempt_binding.py tests/test_experiment_ledger_store.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/day_research_attempt_binding.py trading_agent/day_research_ledger.py trading_agent/day_research_ledger_reader.py tests/test_day_research_attempt_binding.py tests/test_experiment_ledger_store.py
git commit -m "feat(day-research): bind all attempts to market versions"
```

## Task 4: Publish immutable Strategy Capsules and preflight receipts

**Files:**
- Create: `trading_agent/day_strategy_capsule_models.py`
- Create: `trading_agent/day_strategy_capsule.py`
- Modify: `trading_agent/day_research_ledger.py`
- Modify: `trading_agent/day_research_ledger_reader.py`
- Create: `tests/test_day_strategy_capsule.py`
- Create: `tests/test_day_strategy_capsule_store.py`
- Modify: `tests/test_generated_strategy_sandbox.py`

- [ ] **Step 1: Write failing capsule and authority-ceiling tests**

Test builtin and generated artifacts, exact hashes, deterministic replay receipt, market/cadence/evidence schema, cost/resource declarations, and these blockers:

```python
def test_kr_capsule_cannot_be_paper_capable() -> None:
    with pytest.raises(ValidationError, match="kr_capsule_authority_ceiling"):
        capsule_fixture(
            market_id=MarketId.KR_EQUITIES,
            authority_ceiling=CapsuleAuthorityCeiling.US_ALPACA_PAPER_CAPABLE,
        )


def test_capsule_never_contains_current_trading_authority() -> None:
    assert capsule_fixture().trading_authority is False
```

- [ ] **Step 2: Prove the tests are red**

Run: `uv run pytest -q tests/test_day_strategy_capsule.py tests/test_day_strategy_capsule_store.py`

Expected: FAIL because capsule modules are absent.

- [ ] **Step 3: Implement capsule build and publication**

Define `CapsuleArtifactKind`, `CapsuleAuthorityCeiling`, `StrategyCapsule`, `CapsulePreflightReceipt`, and `build_strategy_capsule()`. For generated code, verify references through `GeneratedStrategyArtifactStore`, runtime fingerprint, sandbox profile, protocol version, evaluator hash, resource limits, and deterministic two-run replay digest. The capsule contains declarations, not an executable provider handle.

- [ ] **Step 4: Persist only verified capsules**

Require an existing same-market hypothesis version and successful attempt binding. Exact replay is idempotent; a changed payload under the same capsule ID conflicts. Reject a generated capsule without a successful sandbox/determinism receipt.

- [ ] **Step 5: Pass focused tests and commit**

Run: `uv run pytest -q tests/test_day_strategy_capsule.py tests/test_day_strategy_capsule_store.py tests/test_generated_strategy_runtime.py tests/test_generated_strategy_sandbox.py`

Expected: PASS, including file/network/process escape and CPU/wall/RSS/output-limit rejection.

```bash
git add trading_agent/day_strategy_capsule_models.py trading_agent/day_strategy_capsule.py trading_agent/day_research_ledger.py trading_agent/day_research_ledger_reader.py tests/test_day_strategy_capsule.py tests/test_day_strategy_capsule_store.py
git commit -m "feat(day-research): publish verified strategy capsules"
```

## Task 5: Wire the AI Discovery cycle into the existing Research OS

**Files:**
- Create: `trading_agent/day_discovery_loop.py`
- Modify: `trading_agent/researcher_pipeline.py`
- Modify: `trading_agent/strategy_research_hypothesis_factory.py`
- Modify: `trading_agent/research_agent_decision.py`
- Modify: `trading_agent/research_agent_actions.py`
- Modify: `trading_agent/research_agent_day_actions.py`
- Modify: `trading_agent/research_agent_source_adapters_primary.py`
- Modify: `trading_agent/research_agent_service_runtime.py`
- Modify: `trading_agent/research_os_runtime.py`
- Create: `run_day_discovery_cycle.py`
- Create: `tests/test_day_discovery_loop.py`
- Create: `tests/test_day_discovery_cycle_cli.py`
- Create: `tests/fixtures/day-research/discovery-evidence.json`
- Create: `tests/fixtures/day-research/calendar-snapshot.json`
- Modify: `tests/test_researcher_pipeline_e2e.py`
- Modify: `tests/test_research_agent_service_runtime.py`
- Modify: `tests/test_research_os_runtime.py`

- [ ] **Step 1: Write failing trigger, freedom, and budget tests**

Exercise cycles triggered by a new completed bar, new point-in-time evidence, terminal trial/integrity event, fixed review close, and due exploration-policy item. Prove one bounded evidence view may generate/criticize at most three drafts and publish at most one primary proposal. A novel open methodology tag and general Python source not present in any strategy enum must be accepted when constructible and safe; category is not a rejection reason.

Also prove duplicate/leaky/unconstructible/budget-exhausted proposals, critic rejection, compile failure, sandbox failure, and nondeterminism each create a terminal attempt/budget debit rather than disappearing.

- [ ] **Step 2: Prove the tests are red**

Run: `uv run pytest -q tests/test_day_discovery_loop.py tests/test_day_discovery_cycle_cli.py tests/test_researcher_pipeline_e2e.py tests/test_research_agent_service_runtime.py tests/test_research_os_runtime.py`

Expected: FAIL because Day decisions currently select existing recommendations and cannot propose a hypothesis.

- [ ] **Step 3: Admit `PROPOSE_HYPOTHESIS` for the Day family**

Extend the Day branch in `research_agent_decision.py` and `research_agent_actions.py`; keep one primary action and the existing model-call/output budgets. `DayResearchActionExecutor` delegates hypothesis creation to `DayDiscoveryLoop`, while existing recommendation/open-state review remains available. AI output supplies a question, mechanism, methodology tags, falsification/baseline and source proposal; it never publishes authority, risk, size, provider, or order fields.

- [ ] **Step 4: Build propose → critic → preregister → artifact → preflight**

Reuse `ResearcherPipeline`, but make its accepted Day result register `HypothesisFamily`/market `HypothesisVersion`, bind every `ResearchAttempt`, publish the generated artifact/capsule, run sandbox plus deterministic replay, and request a future-only Forward Probe slot. The critic must deterministically check source constructibility, point-in-time leakage, semantic duplication, preregistered failure/baseline, multiple-testing/search budget, and runtime/resource limits.

- [ ] **Step 5: Add bounded feedback and independent market cursors**

`research_agent_source_adapters_primary.py` exposes only family/version identity, safe outcome class, preregistered bounded metrics, integrity/data/runtime reasons, novelty/duplicate information, remaining budget, next review date, and policy priority. Exclude exact sealed-holdout metrics, symbol contribution, account identity, raw provider/auth data, and credentials. `research_agent_service_runtime.py`/`research_os_runtime.py` keep US/KR cursors and failures independent and acquire the existing sole heavy-process lease before empirical work.

- [ ] **Step 6: Implement the one-cycle operational CLI**

`run_day_discovery_cycle.py` accepts an explicit `--market`, bounded private evidence view, calendar snapshot, experiment ledger, generated-artifact/receipt roots, and `--max-drafts` in `1..3`. It reuses the configured Research OS model client; it accepts no credential, broker, risk, size, order, or arbitrary endpoint option. Emit sanitized canonical JSON containing cycle/attempt/family/version/capsule/admission IDs and terminal reason only. Invalid input/publication exits non-zero; an accepted or fully criticized/rejected terminal cycle exits zero because both are valid research outcomes.

- [ ] **Step 7: Pass and commit**

Run: `uv run pytest -q tests/test_day_discovery_loop.py tests/test_day_discovery_cycle_cli.py tests/test_researcher_pipeline_e2e.py tests/test_research_agent_service_runtime.py tests/test_research_os_runtime.py tests/test_strategy_research_science_kernel.py`

Expected: PASS, including a novel non-enum strategy artifact entering research-only future admission.

```bash
git add trading_agent/day_discovery_loop.py trading_agent/researcher_pipeline.py trading_agent/strategy_research_hypothesis_factory.py trading_agent/research_agent_decision.py trading_agent/research_agent_actions.py trading_agent/research_agent_day_actions.py trading_agent/research_agent_source_adapters_primary.py trading_agent/research_agent_service_runtime.py trading_agent/research_os_runtime.py run_day_discovery_cycle.py tests/test_day_discovery_loop.py tests/test_day_discovery_cycle_cli.py tests/fixtures/day-research/discovery-evidence.json tests/fixtures/day-research/calendar-snapshot.json tests/test_researcher_pipeline_e2e.py tests/test_research_agent_service_runtime.py tests/test_research_os_runtime.py
git commit -m "feat(day-research): let AI propose bounded capsule hypotheses"
```

## Task 6: Convert untrusted candidates into host-owned signals

**Files:**
- Modify: `trading_agent/generated_strategy_protocol.py`
- Modify: `trading_agent/generated_strategy_runner.py`
- Create: `trading_agent/day_forward_probe_bridge.py`
- Create: `tests/test_day_forward_probe_bridge.py`
- Modify: `tests/test_generated_strategy_protocol.py`
- Modify: `tests/test_trade_signal_publication.py`

- [ ] **Step 1: Write failing trust-boundary tests**

Prove generated output is rejected for future/stale/mismatched bars, non-finite prices, wrong entry/stop direction, unknown symbol, extra fields, and any attempt to supply targets, position size, provider, or authority. Prove the host adds sorted targets, validity, rationale, cost and completed-bar `EvidenceRef` values before constructing `TradeSignalEnvelope`.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_generated_strategy_protocol.py tests/test_day_forward_probe_bridge.py tests/test_trade_signal_publication.py`

Expected: FAIL because no market-generic host bridge exists.

- [ ] **Step 3: Version the candidate protocol without giving it more authority**

Keep the runner response limited to signal candidate values. Add side only if required for long/short validation, but keep target, size, risk, market, broker, and authority host-owned. Reject unknown JSON keys in `generated_strategy_runner.py`; maintain frame-size/resource limits.

- [ ] **Step 4: Implement `project_day_trade_signal()`**

Inputs must include the verified capsule, current completed-bar lineage, current quote/spread validation, target projection policy, and evidence refs. It must return `TradeSignalEnvelope` or a deterministic blocked reason and must never import Paper/KIS/LS clients.

- [ ] **Step 5: Pass tests and commit**

Run: `uv run pytest -q tests/test_generated_strategy_protocol.py tests/test_generated_strategy_runtime.py tests/test_day_forward_probe_bridge.py tests/test_trade_signal_publication.py`

Expected: PASS.

```bash
git add trading_agent/generated_strategy_protocol.py trading_agent/generated_strategy_runner.py trading_agent/day_forward_probe_bridge.py tests/test_day_forward_probe_bridge.py tests/test_generated_strategy_protocol.py tests/test_trade_signal_publication.py
git commit -m "feat(day-research): add host-owned generated signal bridge"
```

## Task 7: Register future-only Forward Trials and bounded active slots

**Files:**
- Create: `trading_agent/day_forward_trial_models.py`
- Create: `trading_agent/day_forward_probe_admission.py`
- Modify: `trading_agent/day_research_ledger.py`
- Modify: `trading_agent/day_research_ledger_reader.py`
- Create: `tests/test_day_forward_trial.py`
- Create: `tests/test_day_forward_probe_admission.py`
- Modify: `tests/test_forward_outcomes.py`

- [ ] **Step 1: Write failing temporal, event-chain, and slot tests**

Test `SIGNAL`, `ENTRY`, `EXIT`, `NO_SIGNAL`, `BLOCKED`, `FAILED`, and `CENSORED` events, monotonic sequence/previous-event keys, immutable outcome refs, exact replay, and at most three active Forward Probe/Shadow capsules per market. The queue must remain unbounded by strategy category but deterministic by policy order.

```python
def test_first_eligible_bar_must_follow_registration_bar() -> None:
    with pytest.raises(ValidationError, match="forward_trial_not_future"):
        trial_fixture(first_eligible_completed_bar=REGISTRATION_BAR)
```

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_day_forward_trial.py tests/test_day_forward_probe_admission.py tests/test_forward_outcomes.py`

Expected: FAIL because Day Forward Trial contracts are absent.

- [ ] **Step 3: Implement registration and admission**

Add `DayForwardTrial`, `DayForwardTrialEvent`, `DayForwardOutcomeRef`, `ForwardExecutionLane` (`FORWARD_PROBE`, `SHADOW` only in the shared layer), and deterministic `select_active_probe_slots()`. Require capsule/version/trial market equality, official calendar snapshot/session IDs, cost-model hash, source/evidence hashes, preregistration time, and `first_eligible_completed_bar` strictly after the registration bar.

- [ ] **Step 4: Implement restart-safe event append/read**

Derive current state from the event chain. A duplicate canonical bar/event returns the stored event; same identity with changed content conflicts. A gap produces `CENSORED`, never a favorable inferred exit. Same-bar target/stop resolution remains host policy and resolves to stop.

- [ ] **Step 5: Pass tests and commit**

Run: `uv run pytest -q tests/test_day_forward_trial.py tests/test_day_forward_probe_admission.py tests/test_forward_outcomes.py tests/test_day_research_ledger.py`

Expected: PASS.

```bash
git add trading_agent/day_forward_trial_models.py trading_agent/day_forward_probe_admission.py trading_agent/day_research_ledger.py trading_agent/day_research_ledger_reader.py tests/test_day_forward_trial.py tests/test_day_forward_probe_admission.py tests/test_forward_outcomes.py
git commit -m "feat(day-research): add future-only forward probe trials"
```

## Task 8: Build market-scoped historical, holdout, and selection diagnostics

**Files:**
- Create: `trading_agent/day_historical_evidence.py`
- Create: `trading_agent/day_historical_evidence_store.py`
- Modify: `trading_agent/generated_intraday_evaluator.py`
- Modify: `trading_agent/strategy_research_science_kernel.py`
- Modify: `trading_agent/strategy_research_policy.py`
- Modify: `trading_agent/intraday_overfit_diagnostics.py`
- Create: `tests/test_day_historical_evidence.py`
- Modify: `tests/test_strategy_research_science_kernel.py`
- Modify: `tests/test_intraday_overfit_diagnostics.py`

- [ ] **Step 1: Write failing evidence-seal tests**

Test preregistered train/validation/sealed-holdout windows, purge/embargo, point-in-time data manifest, market cost/slippage evaluator, one holdout reveal per lineage, and `SUPPORTED|REFUTED|INCONCLUSIVE`. Prove US/KR datasets/results cannot share a seal, synthetic/replay is labeled wiring-only, every attempted variant enters DSR/PBO/CSCV inputs, and exact holdout metrics never appear in Discovery feedback. Reject online e-value/FDR claims unless the evidence references a separately validated market-time-series e-value evaluator version.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_day_historical_evidence.py tests/test_strategy_research_science_kernel.py tests/test_intraday_overfit_diagnostics.py`

Expected: FAIL because generated capsule lineage is not bound to a market-scoped evidence seal.

- [ ] **Step 3: Implement `DayHistoricalEvidenceSeal` and evaluator orchestration**

Bind exact version/capsule, market, windows, purge/embargo, data/cost/evaluator hashes, all-attempt count, DSR and PBO/CSCV diagnostics, power/CI preregistration, reveal receipt, classification, and artifact refs. Publish it through `day_historical_evidence_store.py` using private content-addressed immutable artifacts; the experiment ledger remains the authority for preregistration, attempts, and holdout reveal. Run the existing evaluator/science kernel under the sole heavy-process lease and 10 GiB hard stop; prohibit full-universe input. Forward Probe may start before this finishes, but this artifact alone cannot grant promotion or Paper authority.

- [ ] **Step 4: Implement one-time holdout and sanitized feedback**

Reject a second reveal for the same exact lineage and reject reuse after code/parameter/data-manifest changes. Publish only classification, integrity/data/runtime reasons, bounded preregistered summary, selection diagnostics status, and next review date to the Discovery feedback view.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_day_historical_evidence.py tests/test_strategy_research_science_kernel.py tests/test_intraday_overfit_diagnostics.py tests/test_day_discovery_loop.py`

Expected: PASS.

```bash
git add trading_agent/day_historical_evidence.py trading_agent/day_historical_evidence_store.py trading_agent/generated_intraday_evaluator.py trading_agent/strategy_research_science_kernel.py trading_agent/strategy_research_policy.py trading_agent/intraday_overfit_diagnostics.py tests/test_day_historical_evidence.py tests/test_strategy_research_science_kernel.py tests/test_intraday_overfit_diagnostics.py
git commit -m "feat(day-research): seal historical and holdout evidence"
```

## Task 9: Separate promotion review from session eligibility

**Files:**
- Create: `trading_agent/day_research_review_models.py`
- Create: `trading_agent/day_research_review.py`
- Modify: `trading_agent/day_research_ledger.py`
- Modify: `trading_agent/day_research_ledger_reader.py`
- Modify: `trading_agent/intraday_promotion_models.py`
- Create: `tests/test_day_research_review.py`
- Modify: `tests/test_intraday_promotion_models.py`

- [ ] **Step 1: Write failing promotion/eligibility separation tests**

Prove fixed review windows cannot close early, every attempted variant is counted, sealed holdout results are not generator feedback, mixed-market evidence fails, KR cannot exceed `SHADOW_CANDIDATE`, and US Paper candidate states still lack order authority.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_day_research_review.py tests/test_intraday_promotion_models.py`

Expected: FAIL because market-generic review models are absent.

- [ ] **Step 3: Implement immutable review artifacts**

Add:

```python
class DayPromotionStatus(StrEnum):
    REJECTED = "rejected"
    INSUFFICIENT = "insufficient"
    SHADOW_CANDIDATE = "shadow_candidate"
    PAPER_TRIAL_CANDIDATE = "paper_trial_candidate"
    PAPER_CHAMPION_CANDIDATE = "paper_champion_candidate"


class DayExecutionAuthorityClass(StrEnum):
    PAPER_TRIAL_APPROVED = "paper_trial_approved"
    PAPER_CHAMPION = "paper_champion"
```

`PromotionDecision` contains a fixed window, evidence seal, historical/holdout/forward/cost/data-quality refs, attempted variants, selection-adjusted statistics, blockers, reviewer/policy version, power/CI sufficiency, owner-approval-required flag, and effective-after session. `ExecutionEligibility` is a separate session artifact containing exact capsule/version, clean commit, risk hash, authority event, expiry, and `ELIGIBLE|BLOCKED|SUSPENDED|EXPIRED`.

- [ ] **Step 4: Add ledger persistence and feedback redaction**

Only an owner authority event can support US eligible Paper classes. KR eligibility artifacts must explicitly be broker-blocked. Add a redacted `ReviewFeedbackSummary` that excludes holdout exact metrics, symbol contributions, account IDs, and raw provider/auth data.

- [ ] **Step 5: Pass tests and commit**

Run: `uv run pytest -q tests/test_day_research_review.py tests/test_intraday_promotion_models.py tests/test_intraday_promotion_controller.py tests/test_strategy_authority_models.py`

Expected: PASS.

```bash
git add trading_agent/day_research_review_models.py trading_agent/day_research_review.py trading_agent/day_research_ledger.py trading_agent/day_research_ledger_reader.py trading_agent/intraday_promotion_models.py tests/test_day_research_review.py tests/test_intraday_promotion_models.py
git commit -m "feat(day-research): separate promotion from execution eligibility"
```

## Task 10: Add market-close reports, revision chains, and next-session policy

**Files:**
- Create: `trading_agent/day_learning_report_models.py`
- Create: `trading_agent/day_learning_policy.py`
- Create: `trading_agent/day_learning_reports.py`
- Create: `trading_agent/day_learning_report_store.py`
- Modify: `trading_agent/day_research_ledger.py`
- Modify: `trading_agent/day_research_ledger_reader.py`
- Create: `tests/test_day_learning_report_models.py`
- Create: `tests/test_day_learning_policy.py`
- Create: `tests/test_day_learning_reports.py`

- [ ] **Step 1: Write failing report/policy tests**

Test separate execution/research/lineage/next-session sections; explicit `provider_read_only` for KR; actual versus modeled returns; unresolved/censored counts; finalization watermark; exactly one initial final report per `(market_id, session_date, watermark)`; immutable `previous_report_id` revisions; and no cross-market aggregation.

Test policy activation only on a supplied official next-session calendar snapshot, max three active slots, deterministic queue order, and inability to change risk, strategy source, promotion, or execution eligibility.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_day_learning_report_models.py tests/test_day_learning_policy.py tests/test_day_learning_reports.py`

Expected: FAIL because shared report/policy contracts are absent.

- [ ] **Step 3: Implement report contracts and projector**

Add `MarketFinalizationWatermark`, `MarketCloseReport`, `ExecutionReportSection`, `ResearchReportSection`, `CumulativeLineageSection`, `NextSessionSection`, and query-only `DailyLearningReport`. A façade links verified US/KR report IDs; it must not expose a combined-return field and cannot be accepted by review/promotion writers.

- [ ] **Step 4: Implement deterministic next-session policy**

Add `ExplorationPolicy` with `KEEP`, `ROTATE_EXPLORATION`, `SUSPEND_SHADOW`, and `NO_TRADE`. The input is the latest final report revision and redacted feedback. Require official calendar snapshot ID and `effective_session_date` later than the report session.

- [ ] **Step 5: Persist reports separately from authority facts and pass tests**

Use `day_learning_report_store.py` with existing private immutable-file helpers for content-addressed `MarketCloseReport` revisions. It is written only by a query-only projector and is never a promotion evidence writer. Persist `ExplorationPolicy` in the Day experiment ledger because it controls future research activation; the policy stores the exact final report ID but does not copy raw report metrics.

Run: `uv run pytest -q tests/test_day_learning_report_models.py tests/test_day_learning_policy.py tests/test_day_learning_reports.py tests/test_day_research_ledger.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add trading_agent/day_learning_report_models.py trading_agent/day_learning_policy.py trading_agent/day_learning_reports.py trading_agent/day_learning_report_store.py trading_agent/day_research_ledger.py trading_agent/day_research_ledger_reader.py tests/test_day_learning_report_models.py tests/test_day_learning_policy.py tests/test_day_learning_reports.py
git commit -m "feat(day-research): add close reports and exploration policy"
```

## Task 11: Add a contract-only smoke CLI and verify the foundation

**Files:**
- Create: `run_day_research_contract_smoke.py`
- Create: `tests/test_day_research_contract_smoke_cli.py`
- Create: `tests/fixtures/day-research/cross-market.json`
- Create: `tests/fixtures/day-research/valid-dual-market.json`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing CLI tests**

Cover `--help`, a malformed/cross-market fixture returning non-zero without a traceback or secret material, and a local happy path that creates a temporary v10 ledger, registers one family plus separate US/KR versions, publishes research-only capsules, and prints only IDs/statuses.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_day_research_contract_smoke_cli.py`

Expected: FAIL because the CLI is absent.

- [ ] **Step 3: Implement the CLI with no provider/network imports**

Use `argparse`, explicit local paths, canonical JSON output, and safe error codes. The happy path is contract-only and must label synthetic fixtures as such; it must not claim performance.

- [ ] **Step 4: Run focused verification**

```bash
uv run pytest -q \
  tests/test_day_hypothesis_models.py \
  tests/test_day_research_ledger.py \
  tests/test_day_research_attempt_binding.py \
  tests/test_day_strategy_capsule.py \
  tests/test_day_strategy_capsule_store.py \
  tests/test_day_discovery_loop.py \
  tests/test_day_discovery_cycle_cli.py \
  tests/test_day_forward_probe_bridge.py \
  tests/test_day_forward_trial.py \
  tests/test_day_forward_probe_admission.py \
  tests/test_day_historical_evidence.py \
  tests/test_day_research_review.py \
  tests/test_day_learning_report_models.py \
  tests/test_day_learning_policy.py \
  tests/test_day_learning_reports.py \
  tests/test_day_research_contract_smoke_cli.py \
  tests/test_experiment_ledger_store.py \
  tests/test_generated_strategy_protocol.py \
  tests/test_generated_strategy_runtime.py \
  tests/test_trade_signal_publication.py
uv run ruff check trading_agent/day_*.py trading_agent/experiment_ledger_*.py trading_agent/generated_strategy_protocol.py trading_agent/generated_strategy_runner.py run_day_discovery_cycle.py run_day_research_contract_smoke.py tests/test_day_*.py
uv run basedpyright trading_agent/day_hypothesis_models.py trading_agent/day_research_attempt_binding.py trading_agent/day_strategy_capsule_models.py trading_agent/day_strategy_capsule.py trading_agent/day_discovery_loop.py trading_agent/day_forward_trial_models.py trading_agent/day_historical_evidence.py trading_agent/day_historical_evidence_store.py trading_agent/day_research_review_models.py trading_agent/day_research_review.py trading_agent/day_learning_report_models.py trading_agent/day_learning_report_store.py trading_agent/day_research_ledger.py trading_agent/day_research_ledger_reader.py trading_agent/day_forward_probe_bridge.py trading_agent/day_forward_probe_admission.py trading_agent/day_learning_policy.py trading_agent/day_learning_reports.py run_day_discovery_cycle.py run_day_research_contract_smoke.py
```

Expected: all exit 0.

- [ ] **Step 5: Manually exercise the user surface**

```bash
day_foundation_tmp=$(mktemp -d)
uv run python run_day_research_contract_smoke.py --help
uv run python run_day_research_contract_smoke.py --fixture tests/fixtures/day-research/cross-market.json
uv run python run_day_research_contract_smoke.py --fixture tests/fixtures/day-research/valid-dual-market.json --database "$day_foundation_tmp/contract.sqlite3"
uv run python run_day_discovery_cycle.py --help
uv run python run_day_discovery_cycle.py --market us_equities --evidence-view tests/fixtures/day-research/discovery-evidence.json --experiment-ledger "$day_foundation_tmp/discovery.sqlite3" --generated-artifact-root "$day_foundation_tmp/artifacts" --receipt-root "$day_foundation_tmp/receipts" --calendar-snapshot tests/fixtures/day-research/calendar-snapshot.json
```

Observe: help is readable; bad input is blocked before publication; the contract smoke shows two independent market versions/capsules; the Discovery cycle emits a sanitized attempt/family/version/capsule/probe result and no authority or return claim.

- [ ] **Step 6: Commit the smoke surface**

```bash
git add run_day_research_contract_smoke.py tests/test_day_research_contract_smoke_cli.py tests/fixtures/day-research/cross-market.json tests/fixtures/day-research/valid-dual-market.json pyproject.toml
git commit -m "test(day-research): add contract smoke surface"
```

## Foundation completion gate

Do not start a market vertical until all of these are observed:

- v1-v9 ledgers migrate to v10 without mutating historical payloads.
- A same-family US/KR pair produces independent versions, capsules, trials, reviews, reports, and policies.
- The Day AI can register a safe, constructible hypothesis/code artifact outside existing strategy enums, while rejected/failed drafts remain counted.
- A cross-market child reference fails before publication.
- A generated candidate cannot provide provider, size, risk, target, or authority fields.
- A registration bar cannot produce a same-bar or backdated trial.
- Every terminal attempt status appears in review accounting.
- Historical/holdout and future evidence remain separate, one-time holdout/selection diagnostics are enforced, and neither plane alone grants promotion.
- No shared module imports Alpaca Paper mutation, KIS order/account/balance/position, or LS trading mutation code.
- Focused tests, Ruff, basedpyright, CLI help/bad/happy paths all pass.
