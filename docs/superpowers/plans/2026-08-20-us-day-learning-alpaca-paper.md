# US Day Learning + Alpaca Paper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run market-scoped generated Strategy Capsules against each eligible completed XNYS bar, attribute modeled Shadow and reconciled Alpaca Paper results separately, publish daily/cumulative learning plus the next-session policy, and permit at most one new Paper entry per session only for an exact owner-approved capsule.

**Architecture:** Consume the completed Shared Day Research/Capsule Foundation. A US Shadow controller reads current-session completed bars and fresh quotes, evaluates up to three active capsules sequentially, and records every outcome in the Day experiment ledger. A separate owner-approved authority projector creates session-specific execution eligibility. Signal, intent, eligibility, risk contract, and session are sealed into a content-addressed admission proof in the existing Paper execution ledger; the proof is revalidated immediately before opening the exact Alpaca Paper broker client. Existing risk, one-use arm, OCO, cancellation, flatten, account activity, and reconciliation remain authoritative.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite Day experiment ledger v10, Paper execution ledger, existing Alpaca Paper clients and US operating coordinator, pytest, Ruff, basedpyright, React/TypeScript dashboard projection

---

## Preconditions and non-negotiable boundary

Do not begin this plan until `2026-08-20-shared-day-research-capsule-foundation.md` passes its completion gate. The following imports/contracts must exist: `StrategyCapsule`, `DayForwardTrial`, `TradeSignalEnvelope` host bridge, `PromotionDecision`, `ExecutionEligibility`, `MarketCloseReport`, `ExplorationPolicy`, and the v10 Day ledger APIs.

This plan authorizes Alpaca **Paper** operations only. Every trading call must use exactly `https://paper-api.alpaca.markets`; any other base URL must fail before an HTTP client/request is opened. Generated code, the Shadow controller, reports, and the AI selection layer never import or call a broker. The first release preserves the existing one-use Paper arm, so there is at most one new Paper entry per XNYS session. Suspension blocks new entries but must not disable cancel, protective OCO, reconciliation, or same-day flatten for existing Paper positions.

## Existing surfaces to preserve

- `trading_agent/paper_order_gate.py` and `paper_operating_session.py`: current-bar, market clock, quote/spread, liquidity, and portfolio risk checks.
- `trading_agent/paper_operating_mutation_execution.py`: final recovery barrier and broker opener.
- `trading_agent/alpaca_paper_mutation_runtime.py`, `alpaca_paper_mutation_client.py`, and `alpaca_paper_config.py`: exact Paper URL guard.
- `trading_agent/paper_auto_arm_authority.py`, `paper_mutation_arm.py`, `hermes_arm_authority.py`: owner arm authority.
- `trading_agent/us_day_operating_coordinator.py`, `us_day_operating_driver.py`, `us_day_operating_models.py`: single operating state machine.
- `trading_agent/paper_account_activity_store.py`, `paper_trade_update_runtime.py`, `execution_writer.py`, `paper_reconciliation.py`: raw actual-fill evidence and single writer.
- `trading_agent/paper_entry_source.py`, `orb_artifact_gate.py`, `orb_forward_trial.py`: legacy ORB paths to re-seal or block, not delete.

## Task 1: Add content-addressed XNYS session snapshots

**Files:**
- Create: `trading_agent/us_day_calendar_snapshot.py`
- Modify: `trading_agent/us_equity_calendar.py`
- Create: `tests/test_us_day_calendar_snapshot.py`
- Modify: `tests/test_future_session_plan_compiler.py`

- [ ] **Step 1: Write failing calendar-snapshot tests**

Test regular days, holidays, early close, timezone/DST boundaries, unsupported dates, and next official session. A snapshot identity must hash exact session open/close and the published-calendar source version. No generic weekday fallback is allowed.

```python
def test_next_session_does_not_fall_back_to_weekday_when_calendar_is_unsupported() -> None:
    with pytest.raises(UnsupportedUsEquityCalendarDateError):
        build_xnys_calendar_snapshot(after_date=dt.date(2028, 12, 31))
```

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_us_day_calendar_snapshot.py tests/test_future_session_plan_compiler.py`

Expected: FAIL because snapshot contracts do not exist.

- [ ] **Step 3: Implement immutable snapshots**

Add `XnysSessionSnapshot` with `snapshot_id`, official session date, aware open/close, early-close flag, source version, published-at timestamp, and previous/next session dates. Construct it only from `regular_session_bounds()`/`next_regular_session()` for a published date. Do not infer a session from `weekday()` outside the tracked calendar.

- [ ] **Step 4: Pass and commit**

Run: `uv run pytest -q tests/test_us_day_calendar_snapshot.py tests/test_future_session_plan_compiler.py`

Expected: PASS.

```bash
git add trading_agent/us_day_calendar_snapshot.py trading_agent/us_equity_calendar.py tests/test_us_day_calendar_snapshot.py tests/test_future_session_plan_compiler.py
git commit -m "feat(us-day): seal XNYS session snapshots"
```

## Task 2: Run bounded generated-capsule Shadow on completed bars

**Files:**
- Create: `trading_agent/us_day_capsule_shadow.py`
- Create: `trading_agent/us_day_capsule_shadow_models.py`
- Create: `trading_agent/us_day_capsule_shadow_service.py`
- Modify: `trading_agent/generated_intraday_trial.py`
- Modify: `trading_agent/generated_intraday_registration.py`
- Modify: `trading_agent/research_agent_service_runtime.py`
- Modify: `trading_agent/research_os_runtime.py`
- Create: `tests/test_us_day_capsule_shadow.py`
- Create: `tests/test_us_day_capsule_shadow_service.py`
- Modify: `tests/test_generated_intraday_registration.py`
- Modify: `tests/test_generated_intraday_loop.py`

- [ ] **Step 1: Write failing current-session and sequencing tests**

Cover closed session, non-current data date, stale feed, missing spread, incomplete/future bar, first-eligible boundary, duplicate bar replay, generated runtime failure, and independent capsule failure. Prove at most three active capsules are evaluated in deterministic policy order and one failure does not skip later capsules.

```python
def test_registration_bar_cannot_create_a_shadow_observation() -> None:
    result = controller_fixture(first_eligible_at=BAR_END).evaluate(bar_ending_at=BAR_END)
    assert result.blockers == ("forward_trial_not_future",)
```

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_us_day_capsule_shadow.py tests/test_us_day_capsule_shadow_service.py tests/test_generated_intraday_registration.py tests/test_generated_intraday_loop.py`

Expected: FAIL because the controller is absent and generated registration is US-history oriented.

- [ ] **Step 3: Implement `UsDayCapsuleShadowController`**

Inputs are a verified `XnysSessionSnapshot`, latest completed current-session bar, fresh quote/spread receipt, active `ExplorationPolicy`, and capsule/ledger readers. For each active capsule:

1. verify US market, session, policy, first eligible bar, and evidence freshness;
2. run generated/builtin evaluation through the shared host bridge;
3. append `NO_SIGNAL`, `BLOCKED`, `FAILED`, or signal/entry events;
4. never construct `PaperOrderAdmissionRequest` or import a broker client.

Generalize only the reusable future-bar runner seams in `generated_intraday_*`; leave historical warm-up non-actionable.

- [ ] **Step 4: Add restart/replay behavior**

A restarted controller reads existing event chains, returns canonical existing results for a repeated bar, and never emits a second signal/trial event. Changed content under the same event identity conflicts and stops only that capsule.

- [ ] **Step 5: Register the controller with the intraday service**

`UsDayCapsuleShadowService` subscribes to each newly completed current XNYS bar/evidence wake, advances a durable market/bar cursor exactly once, invokes Discovery when a generation trigger is present, then evaluates active Shadow capsules. It sleeps outside the official session, never busy-polls, and a US failure does not advance or block the KR cursor. No broker import is permitted in this service.

- [ ] **Step 6: Pass and commit**

Run: `uv run pytest -q tests/test_us_day_capsule_shadow.py tests/test_us_day_capsule_shadow_service.py tests/test_generated_intraday_registration.py tests/test_generated_intraday_loop.py tests/test_day_forward_trial.py tests/test_research_agent_service_runtime.py tests/test_research_os_runtime.py`

Expected: PASS.

```bash
git add trading_agent/us_day_capsule_shadow.py trading_agent/us_day_capsule_shadow_models.py trading_agent/us_day_capsule_shadow_service.py trading_agent/generated_intraday_trial.py trading_agent/generated_intraday_registration.py trading_agent/research_agent_service_runtime.py trading_agent/research_os_runtime.py tests/test_us_day_capsule_shadow.py tests/test_us_day_capsule_shadow_service.py tests/test_generated_intraday_registration.py tests/test_generated_intraday_loop.py
git commit -m "feat(us-day): run capsule shadow on completed XNYS bars"
```

## Task 3: Project owner-approved trial and champion eligibility

**Files:**
- Create: `trading_agent/us_day_execution_eligibility.py`
- Modify: `trading_agent/intraday_promotion_models.py`
- Modify: `trading_agent/intraday_promotion_control.py`
- Modify: `trading_agent/lifecycle_authority_policy.py`
- Modify: `trading_agent/paper_auto_arm_authority.py`
- Create: `tests/test_us_day_execution_eligibility.py`
- Modify: `tests/test_intraday_promotion_controller.py`
- Modify: `tests/test_paper_auto_arm_authority.py`
- Create: `tests/test_lifecycle_authority_policy.py`

- [ ] **Step 1: Write failing authority tests**

Prove `PAPER_TRIAL_CANDIDATE` and `PAPER_CHAMPION_CANDIDATE` do not grant authority; an exact owner approval and append-only authority event are required for `PAPER_TRIAL_APPROVED`/`PAPER_CHAMPION`. `us_day_paper_trial_policy_v1` requires at least 20 eligible forward sessions, 30 completed Shadow trades, historical `SUPPORTED`, preregistered power/CI sufficiency, costs, DSR/PBO, data-quality and integrity pass. Champion candidacy retains the existing minimum 60 forward sessions, 100 completed trades, broker-ledger, overfit, plateau, and SIP blockers. Test next-session effective time, expiry, revocation, suspension, clean commit, risk hash, capsule/version match, and Paper-only market/lane.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_us_day_execution_eligibility.py tests/test_intraday_promotion_controller.py tests/test_paper_auto_arm_authority.py tests/test_lifecycle_authority_policy.py`

Expected: FAIL because only champion-oriented legacy approval exists.

- [ ] **Step 3: Implement eligibility projection**

Map a fixed market review and owner approval into a session-specific shared `ExecutionEligibility`. Require `US_EQUITIES`, `ALPACA_PAPER`, capsule ceiling `US_ALPACA_PAPER_CAPABLE`, exact strategy version, clean commit, risk contract, XNYS snapshot, authority event, effective session, and expiry. AI-authored records cannot satisfy the owner receipt type.

- [ ] **Step 4: Make capsule-era lifecycle fail closed**

Update `lifecycle_authority_policy.py` and arm authority so legacy `PAPER_CHAMPION` state alone cannot mint a new entry arm after capsule admission activation. Trial and champion classes both use the one-use arm; neither can alter quantity/risk or bypass current gate checks.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_us_day_execution_eligibility.py tests/test_intraday_promotion_controller.py tests/test_paper_auto_arm_authority.py tests/test_lifecycle_authority_policy.py tests/test_strategy_authority_models.py`

Expected: PASS.

```bash
git add trading_agent/us_day_execution_eligibility.py trading_agent/intraday_promotion_models.py trading_agent/intraday_promotion_control.py trading_agent/lifecycle_authority_policy.py trading_agent/paper_auto_arm_authority.py tests/test_us_day_execution_eligibility.py tests/test_intraday_promotion_controller.py tests/test_paper_auto_arm_authority.py tests/test_lifecycle_authority_policy.py
git commit -m "feat(us-day): project owner-approved paper eligibility"
```

## Task 4: Extend the Paper execution ledger with capsule proof and attribution

**Files:**
- Create: `trading_agent/paper_capsule_admission_models.py`
- Create: `trading_agent/paper_capsule_admission_schema.py`
- Create: `trading_agent/paper_capsule_admission_store.py`
- Modify: `trading_agent/execution_schema.py`
- Modify: `trading_agent/execution_database.py`
- Modify: `trading_agent/execution_writer.py`
- Modify: `trading_agent/execution_store_reader.py`
- Create: `tests/test_paper_capsule_admission_store.py`
- Modify: `tests/test_alpaca_paper_safety_cli.py`
- Modify: `tests/test_lane_control_plane_bootstrap_cli.py`
- Modify: `tests/test_paper_mutation_schema.py`
- Modify: `tests/test_paper_protective_oco_schema.py`
- Modify: `tests/test_paper_safety_schema.py`
- Modify: `tests/test_paper_smoke_eligibility_execution.py`

- [ ] **Step 1: Write failing schema and immutable-proof tests**

Prove execution schema v1-v9 migrate to v10, old rows remain unchanged, and v10 adds append-only `paper_capsule_admission_proofs` and `paper_capsule_fill_attributions`. Exact replay is idempotent; same proof/attribution ID with changed payload conflicts.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_paper_capsule_admission_store.py tests/test_paper_mutation_schema.py tests/test_paper_protective_oco_schema.py tests/test_paper_safety_schema.py tests/test_paper_smoke_eligibility_execution.py tests/test_alpaca_paper_safety_cli.py tests/test_lane_control_plane_bootstrap_cli.py`

Expected: FAIL because execution schema is 9 and tables/models are absent.

- [ ] **Step 3: Define the proof contract**

`PaperCapsuleAdmissionProof` must bind:

- capsule, hypothesis version, Forward Trial, signal and intent IDs;
- promotion decision, owner approval, current authority event, and execution eligibility IDs;
- XNYS session/snapshot, exact completed bar, quote/spread receipt;
- clean commit, risk-policy hash, canonical intent hash, and proof timestamp.

The content-addressed proof ID covers every field. It contains no credentials, headers, account ID/fingerprint, raw broker response, or mutable approval flag.

- [ ] **Step 4: Add single-writer/read-only persistence**

Persist proof before `order_intents`; require one exact proof per entry intent. Add `PaperCapsuleFillAttribution` keyed by reconciled broker activity/event refs, not modeled prices. Reader APIs remain query-only.

- [ ] **Step 5: Pass migration tests and commit**

Run: `uv run pytest -q tests/test_paper_capsule_admission_store.py tests/test_paper_mutation_schema.py tests/test_paper_protective_oco_schema.py tests/test_paper_safety_schema.py tests/test_paper_smoke_eligibility_execution.py tests/test_alpaca_paper_safety_cli.py tests/test_lane_control_plane_bootstrap_cli.py tests/test_execution_ledger_identity.py`

Expected: PASS and `SCHEMA_VERSION == 10`.

```bash
git add trading_agent/paper_capsule_admission_models.py trading_agent/paper_capsule_admission_schema.py trading_agent/paper_capsule_admission_store.py trading_agent/execution_schema.py trading_agent/execution_database.py trading_agent/execution_writer.py trading_agent/execution_store_reader.py tests/test_paper_capsule_admission_store.py tests/test_alpaca_paper_safety_cli.py tests/test_lane_control_plane_bootstrap_cli.py tests/test_paper_mutation_schema.py tests/test_paper_protective_oco_schema.py tests/test_paper_safety_schema.py tests/test_paper_smoke_eligibility_execution.py
git commit -m "feat(paper): persist capsule admission and fill attribution"
```

## Task 5: Require and revalidate proof at the mutation boundary

**Files:**
- Create: `trading_agent/paper_capsule_admission.py`
- Modify: `trading_agent/paper_execution_models.py`
- Modify: `trading_agent/paper_operating_session_models.py`
- Modify: `trading_agent/paper_operating_session.py`
- Modify: `trading_agent/paper_operating_mutation_execution.py`
- Modify: `trading_agent/paper_mutation_source_validation.py`
- Modify: `tests/test_paper_operating_session.py`
- Modify: `tests/test_paper_operating_mutation_execution.py`
- Modify: `tests/test_paper_operating_entry_execution.py`

- [ ] **Step 1: Write failing pre-network rejection tests**

Use a broker-opener spy. Missing, tampered, future, stale, expired, suspended, revoked, cross-session, wrong capsule/version/signal/intent/risk hash, and legacy-only inputs must return a blocked decision with `opener.calls == 0`. A valid proof continues to the existing gate and opener.

```python
def test_missing_capsule_proof_is_rejected_before_broker_open() -> None:
    opener = BrokerOpenerSpy()
    result = mutation_runtime_fixture(opener=opener).execute_entry(request_without_proof())
    assert result.state is PaperOrderGateState.BLOCKED
    assert opener.calls == 0
```

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_paper_operating_session.py tests/test_paper_operating_mutation_execution.py tests/test_paper_operating_entry_execution.py`

Expected: FAIL because `PaperOrderAdmissionRequest` has no required capsule proof.

- [ ] **Step 3: Add a required proof reference to entry admission**

Extend `PaperOrderIntent` with stable Day lineage IDs only if the execution schema migration covers them; keep quantity host-sized. Add a non-optional `capsule_admission_proof`/proof ID to `PaperOrderAdmissionRequest`. Update every trusted constructor and fixture; do not make the field optional and do not retain a legacy bypass.

- [ ] **Step 4: Verify proof twice**

Validate once when the operating session admits the request and again inside `PaperOperatingMutationExecution.execute_entry()` after recovery/current-epoch checks but before `with self._broker_opener(...)`. The second check reloads current execution eligibility and authority projection so a just-appended suspension/revocation blocks the entry. Persist the verified proof/intention atomically through the existing writer before mutation.

- [ ] **Step 5: Preserve protective/safety authority**

Do not require a new-entry eligibility proof for cancellation, protective OCO, reconciliation, or flatten of existing positions. Their existing mutation provenance and risk scope remain unchanged.

- [ ] **Step 6: Pass and commit**

Run: `uv run pytest -q tests/test_paper_operating_session.py tests/test_paper_operating_mutation_execution.py tests/test_paper_operating_entry_execution.py tests/test_paper_protective_mutation_gate.py tests/test_paper_safety_planner.py`

Expected: PASS.

```bash
git add trading_agent/paper_capsule_admission.py trading_agent/paper_execution_models.py trading_agent/paper_operating_session_models.py trading_agent/paper_operating_session.py trading_agent/paper_operating_mutation_execution.py trading_agent/paper_mutation_source_validation.py tests/test_paper_operating_session.py tests/test_paper_operating_mutation_execution.py tests/test_paper_operating_entry_execution.py
git commit -m "feat(paper): require capsule proof before entry mutation"
```

## Task 6: Replace legacy ORB entry fallback with grandfathered capsules

**Files:**
- Create: `trading_agent/orb_capsule_migration.py`
- Modify: `trading_agent/paper_entry_source.py`
- Modify: `trading_agent/us_day_armed_entry.py`
- Modify: `trading_agent/us_day_operating_cli.py`
- Modify: `trading_agent/us_day_operating_cli_contract.py`
- Modify: `run_alpaca_paper_entry_smoke.py`
- Create: `tests/test_orb_capsule_migration.py`
- Modify: `tests/test_paper_entry_source.py`
- Modify: `tests/test_us_day_operating_failures.py`

- [ ] **Step 1: Write failing migration/fallback tests**

Test append-only `grandfathered_capsule` mapping from exact ORB strategy version, source/parameter hash, risk policy, clean commit, current authority, and evidence. Missing any field blocks migration. After capsule admission activation, ORB-only loader, missing `--day-loop-root`/ledger input, and a legacy champion row cannot construct a new entry.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_orb_capsule_migration.py tests/test_paper_entry_source.py tests/test_us_day_operating_failures.py`

Expected: FAIL because current defaults call `load_current_orb_paper_entry`.

- [ ] **Step 3: Implement append-only migration**

Create a new capsule and mapping event; never update/delete the ORB row. The resulting capsule enters the same review/owner approval/session eligibility path as any other capsule. It receives no implicit champion authority.

- [ ] **Step 4: Remove runtime fallback**

Make the capsule source/ledger arguments required for new entries in `us_day_armed_entry.py`, `us_day_operating_cli.py`, and the smoke path. Keep legacy readers available only for audit/migration commands. A disabled capsule-admission feature state blocks new entries while existing-position safety operations continue.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_orb_capsule_migration.py tests/test_paper_entry_source.py tests/test_us_day_operating_failures.py tests/test_orb_forward_trial.py`

Expected: PASS; old ORB rows remain readable but cannot bypass proof.

```bash
git add trading_agent/orb_capsule_migration.py trading_agent/paper_entry_source.py trading_agent/us_day_armed_entry.py trading_agent/us_day_operating_cli.py trading_agent/us_day_operating_cli_contract.py run_alpaca_paper_entry_smoke.py tests/test_orb_capsule_migration.py tests/test_paper_entry_source.py tests/test_us_day_operating_failures.py
git commit -m "feat(us-day): migrate ORB entry authority to capsules"
```

## Task 7: Wire eligible capsules through the existing one-use operating path

**Files:**
- Create: `trading_agent/us_day_capsule_entry_source.py`
- Modify: `trading_agent/us_day_operating_models.py`
- Modify: `trading_agent/us_day_operating_coordinator.py`
- Modify: `trading_agent/us_day_operating_driver.py`
- Modify: `trading_agent/paper_auto_arm_policy.py`
- Modify: `trading_agent/research_agent_day_actions.py`
- Create: `tests/test_us_day_capsule_entry_source.py`
- Modify: `tests/test_us_day_operating_vertical_e2e.py`
- Modify: `tests/test_paper_auto_arm_policy.py`
- Modify: `tests/test_research_agent_day_actions.py`

- [ ] **Step 1: Write failing end-to-end admission tests**

Test AI selection among already eligible capsules or `no_trade`; deterministic selection validation; host-built prices/targets; risk sizing; one-use arm; second entry blocked; exact session matching; and no eligible capsule/failure producing no fallback order.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_us_day_capsule_entry_source.py tests/test_us_day_operating_vertical_e2e.py tests/test_paper_auto_arm_policy.py tests/test_research_agent_day_actions.py`

Expected: FAIL because operating requests come from the legacy ORB source.

- [ ] **Step 3: Implement the capsule source adapter**

Read a host-validated `TradeSignalEnvelope`, exact current `ExecutionEligibility`, owner authority event, risk hash, and XNYS snapshot; build the content-addressed proof and project to `PaperOrderIntent`/`PaperOrderAdmissionRequest`. AI output is an eligible capsule ID or `no_trade` only. Reject any AI-provided numeric price, quantity, leverage, risk, broker URL, or request body.

- [ ] **Step 4: Preserve coordinator ownership**

Route the admission request through the unchanged current risk gate, arm consumption, OCO, safety flatten, reconciliation, and result delivery state machine. Keep at most one new entry because the same session arm cannot be consumed twice.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_us_day_capsule_entry_source.py tests/test_us_day_operating_vertical_e2e.py tests/test_paper_auto_arm_policy.py tests/test_paper_order_gate.py tests/test_research_agent_day_actions.py`

Expected: PASS.

```bash
git add trading_agent/us_day_capsule_entry_source.py trading_agent/us_day_operating_models.py trading_agent/us_day_operating_coordinator.py trading_agent/us_day_operating_driver.py trading_agent/paper_auto_arm_policy.py trading_agent/research_agent_day_actions.py tests/test_us_day_capsule_entry_source.py tests/test_us_day_operating_vertical_e2e.py tests/test_paper_auto_arm_policy.py tests/test_research_agent_day_actions.py
git commit -m "feat(us-day): route eligible capsules through paper operating"
```

## Task 8: Attribute actual results only from reconciled activity

**Files:**
- Create: `trading_agent/us_day_paper_attribution.py`
- Modify: `trading_agent/paper_account_activity_store.py`
- Modify: `trading_agent/paper_trade_update_runtime.py`
- Modify: `trading_agent/paper_reconciliation.py`
- Modify: `trading_agent/execution_writer.py`
- Create: `tests/test_us_day_paper_attribution.py`
- Modify: `tests/test_paper_reconciliation.py`
- Modify: `tests/test_paper_trade_update_ingestion.py`

- [ ] **Step 1: Write failing actual-fill attribution tests**

Cover partial fills, multiple activity events, cancel/replace, reconnect replay, protective exits, forced flatten, open quantity, unmatched/ambiguous broker activity, fees/slippage, and exact capsule/trial lineage. A modeled signal or submitted order without reconciled fill must not create actual return.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_us_day_paper_attribution.py tests/test_paper_reconciliation.py tests/test_paper_trade_update_ingestion.py`

Expected: FAIL because fill events lack capsule/trial attribution.

- [ ] **Step 3: Implement reconciled attribution**

Join account activity → broker order → client intent → admission proof. Write append-only `PaperCapsuleFillAttribution` only after reconciliation identifies quantity and price. Preserve unmatched records as anomalies and exclude their quantity/P&L until resolved; a later match appends a new resolution artifact rather than rewriting history.

- [ ] **Step 4: Pass and commit**

Run: `uv run pytest -q tests/test_us_day_paper_attribution.py tests/test_paper_reconciliation.py tests/test_paper_trade_update_ingestion.py tests/test_paper_capsule_admission_store.py`

Expected: PASS.

```bash
git add trading_agent/us_day_paper_attribution.py trading_agent/paper_account_activity_store.py trading_agent/paper_trade_update_runtime.py trading_agent/paper_reconciliation.py trading_agent/execution_writer.py tests/test_us_day_paper_attribution.py tests/test_paper_reconciliation.py tests/test_paper_trade_update_ingestion.py
git commit -m "feat(us-day): attribute reconciled paper fills to capsules"
```

## Task 9: Publish the US close report and next-session policy

**Files:**
- Create: `trading_agent/us_day_market_close_report.py`
- Create: `trading_agent/us_day_learning_policy.py`
- Modify: `trading_agent/us_day_capsule_shadow_service.py`
- Modify: `trading_agent/strategy_research_close_report.py`
- Modify: `trading_agent/intraday_lane_daily_snapshot.py`
- Create: `tests/test_us_day_market_close_report.py`
- Create: `tests/test_us_day_learning_policy.py`

- [ ] **Step 1: Write failing watermark/report/policy tests**

Require terminal or explicitly censored due Shadow trials, Paper reconciliation, lifecycle projection, and safety events before final report. Test daily account return, cumulative account return, capsule-attributed actual return, modeled Shadow return, fees/slippage, MDD, blockers, all attempts, and next review date as separate fields. Late activity creates a `previous_report_id` revision.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_us_day_market_close_report.py tests/test_us_day_learning_policy.py`

Expected: FAIL because no US market-scoped finalization projector exists.

- [ ] **Step 3: Implement US-only projection**

Read the v10 Day ledger and query-only Paper execution/reconciliation ledgers. Do not reuse a code path that can mix KR observations. Actual figures come only from reconciled attribution; Shadow figures are labeled modeled/research. Redact account identifiers and raw broker/provider material.

- [ ] **Step 4: Implement next-XNYS-session policy**

Use the latest final report revision and a verified `XnysSessionSnapshot`. The policy can keep/rotate/suspend/no-trade, select up to three Shadow capsules, and order the queue. It cannot change current-session source/risk/authority or automatically promote the session winner.

Wire report finalization and policy publication into `UsDayCapsuleShadowService`: after the official close it waits for the watermark, publishes/revises the report, schedules policy for the exact next XNYS session, and does not mutate the closed/current session.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_us_day_market_close_report.py tests/test_us_day_learning_policy.py tests/test_intraday_lane_daily_snapshot.py`

Expected: PASS.

```bash
git add trading_agent/us_day_market_close_report.py trading_agent/us_day_learning_policy.py trading_agent/us_day_capsule_shadow_service.py trading_agent/strategy_research_close_report.py trading_agent/intraday_lane_daily_snapshot.py tests/test_us_day_market_close_report.py tests/test_us_day_learning_policy.py
git commit -m "feat(us-day): report paper and shadow learning"
```

## Task 10: Add query-only Day Agent dashboard projection

**Files:**
- Create: `trading_agent/dashboard_projection_day_learning.py`
- Modify: `trading_agent/dashboard_projection_paper.py`
- Modify: `trading_agent/dashboard_agent_runtime_projection.py`
- Modify: `dashboard/src/workspaces/paper.ts`
- Modify: `dashboard/src/workspaces/research.ts`
- Create: `tests/test_dashboard_projection_day_learning.py`
- Modify: `tests/test_dashboard_projection_paper.py`

- [ ] **Step 1: Write failing projection tests**

Test latest verified US report revision, actual Paper versus modeled Shadow labels, active/queued/suspended capsules, next-session eligibility and reason, blocked/risk/reconciliation incidents, cumulative lineage, and no inferred performance when trace is missing. The dashboard projector must be rejected as a promotion/policy source.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_dashboard_projection_day_learning.py tests/test_dashboard_projection_paper.py`

Expected: FAIL because the projection is absent.

- [ ] **Step 3: Implement query-only projection and UI**

Render IDs/statuses and verified metrics without account identifiers. Label every Shadow/replay value as research-only and state that neither Shadow nor Paper results prove future profitability. Do not add mutation buttons or authority-writing APIs.

- [ ] **Step 4: Pass and commit**

Run: `uv run pytest -q tests/test_dashboard_projection_day_learning.py tests/test_dashboard_projection_paper.py`

Expected: PASS.

Run: `cd dashboard && bun run check && bun run build`

Expected: typecheck, lint, unit tests, and production bundle all exit 0.

```bash
git add trading_agent/dashboard_projection_day_learning.py trading_agent/dashboard_projection_paper.py trading_agent/dashboard_agent_runtime_projection.py dashboard/src/workspaces/paper.ts dashboard/src/workspaces/research.ts tests/test_dashboard_projection_day_learning.py tests/test_dashboard_projection_paper.py
git commit -m "feat(dashboard): show verified US day learning"
```

## Task 11: Verify contract-only, observer, and Paper smoke activation

**Files:**
- Create: `run_us_day_capsule_shadow.py`
- Create: `run_us_day_close_report.py`
- Modify: `run_alpaca_paper_entry_smoke.py`
- Create: `tests/test_us_day_capsule_shadow_cli.py`
- Create: `tests/test_us_day_close_report_cli.py`
- Create: `tests/fixtures/us-day/stale-bar.json`
- Create: `tests/fixtures/us-day/valid-next-bar.json`
- Create: `tests/fixtures/us-day/finalized-session.json`
- Create: `tests/fixtures/us-day/non-paper-url.json`
- Modify: `tests/test_alpaca_paper_entry_mutation_client.py`
- Modify: `tests/test_us_day_operating_vertical_e2e.py`

- [ ] **Step 1: Add CLI help/bad/local-happy tests**

For both new CLIs test `--help`, malformed/cross-market/stale input, and a local fixture happy path. Add a mutation-client test that supplies a non-Paper/live URL and asserts rejection before the transport spy receives a request.

- [ ] **Step 2: Prove the CLI tests are red**

Run: `uv run pytest -q tests/test_us_day_capsule_shadow_cli.py tests/test_us_day_close_report_cli.py tests/test_alpaca_paper_entry_mutation_client.py`

Expected: FAIL because the new CLI modules and capsule fixture parsers are absent.

- [ ] **Step 3: Implement both CLIs and their exact boundaries**

Use `argparse`. `run_us_day_capsule_shadow.py` accepts private local fixture/evidence paths, Day experiment ledger, generated artifact root, XNYS snapshot, and Shadow root; it emits sanitized cycle/trial/capsule/event IDs and never accepts credentials, broker URL, arm, order, size, or risk overrides. `run_us_day_close_report.py` accepts query-only Day/Paper ledgers plus a finalization fixture, emits report/policy IDs and separated actual/Shadow metrics, and never mutates Paper state. Invalid input/publication exits non-zero without traceback or secret material; a blocked/no-signal research cycle is a successful terminal exit.

Keep `run_alpaca_paper_entry_smoke.py` as the separately armed Paper-only surface. Its capsule proof/eligibility inputs are required and its base URL remains fixed, not a user option.

- [ ] **Step 4: Run the full focused suite**

```bash
uv run pytest -q \
  tests/test_us_day_calendar_snapshot.py \
  tests/test_us_day_capsule_shadow.py \
  tests/test_us_day_capsule_shadow_service.py \
  tests/test_us_day_execution_eligibility.py \
  tests/test_paper_capsule_admission_store.py \
  tests/test_paper_operating_session.py \
  tests/test_paper_operating_mutation_execution.py \
  tests/test_orb_capsule_migration.py \
  tests/test_us_day_capsule_entry_source.py \
  tests/test_us_day_operating_vertical_e2e.py \
  tests/test_us_day_paper_attribution.py \
  tests/test_paper_reconciliation.py \
  tests/test_paper_trade_update_ingestion.py \
  tests/test_us_day_market_close_report.py \
  tests/test_us_day_learning_policy.py \
  tests/test_dashboard_projection_day_learning.py \
  tests/test_alpaca_paper_entry_mutation_client.py \
  tests/test_us_day_capsule_shadow_cli.py \
  tests/test_us_day_close_report_cli.py
uv run ruff check trading_agent/us_day_*.py trading_agent/paper_capsule_*.py trading_agent/paper_operating_*.py trading_agent/paper_entry_source.py trading_agent/orb_capsule_migration.py run_us_day_capsule_shadow.py run_us_day_close_report.py run_alpaca_paper_entry_smoke.py tests/test_us_day_*.py tests/test_paper_capsule_*.py
uv run basedpyright trading_agent/us_day_capsule_shadow.py trading_agent/us_day_capsule_shadow_service.py trading_agent/us_day_execution_eligibility.py trading_agent/paper_capsule_admission_models.py trading_agent/paper_capsule_admission_store.py trading_agent/paper_capsule_admission.py trading_agent/us_day_capsule_entry_source.py trading_agent/us_day_paper_attribution.py trading_agent/us_day_market_close_report.py trading_agent/us_day_learning_policy.py run_us_day_capsule_shadow.py run_us_day_close_report.py
```

Expected: all exit 0.

- [ ] **Step 5: Manually exercise contract-only and observer modes**

```bash
us_day_tmp=$(mktemp -d)
uv run python run_us_day_capsule_shadow.py --help
uv run python run_us_day_capsule_shadow.py --fixture tests/fixtures/us-day/stale-bar.json
uv run python run_us_day_capsule_shadow.py --fixture tests/fixtures/us-day/valid-next-bar.json --experiment-ledger "$us_day_tmp/experiments.sqlite3"
uv run python run_us_day_close_report.py --fixture tests/fixtures/us-day/finalized-session.json --experiment-ledger "$us_day_tmp/experiments.sqlite3" --execution-ledger "$us_day_tmp/execution.sqlite3"
```

Observe: stale/current-session failures are explicit; valid Shadow writes no broker fact; report separates Shadow from actual fills.

- [ ] **Step 6: Manually prove the mutation safety boundary**

Run the Paper smoke first with no arm, missing/tampered proof, and a deliberately non-Paper URL fixture. Observe zero transport requests. Only after explicit owner approval and with paper credentials loaded from `~/.config/trading-agent/alpaca-paper.env` mode `600`, run one bounded Alpaca Paper smoke if credentials are available. Never request credentials in chat and never use a pasted credential.

For an actual Paper smoke, prove in one captured run:

1. preflight and exact `https://paper-api.alpaca.markets` URL;
2. open orders/positions/activity reconciliation before entry;
3. at most one new entry arm consumption;
4. protective OCO or safe flatten as applicable;
5. post-mutation reconciliation and capsule fill attribution;
6. no secret/header/account identifier in output.

If credentials are unavailable, report the Paper network smoke as not run; fixture-based pre-network rejection is still mandatory and must pass.

- [ ] **Step 7: Commit verification surfaces**

```bash
git add run_us_day_capsule_shadow.py run_us_day_close_report.py run_alpaca_paper_entry_smoke.py tests/test_us_day_capsule_shadow_cli.py tests/test_us_day_close_report_cli.py tests/fixtures/us-day/stale-bar.json tests/fixtures/us-day/valid-next-bar.json tests/fixtures/us-day/finalized-session.json tests/fixtures/us-day/non-paper-url.json tests/test_alpaca_paper_entry_mutation_client.py tests/test_us_day_operating_vertical_e2e.py
git commit -m "test(us-day): verify capsule shadow and paper safety"
```

## Activation gate

Enable stages in order and append a feature-state event for each transition:

1. `CONTRACT_ONLY`
2. `GENERATED_FORWARD_PROBE`
3. `DAILY_LEARNING` after at least five natural XNYS sessions
4. `PAPER_OBSERVER` with intent/proof construction but no arm
5. `PAPER_TRIAL` only after owner-approved `PAPER_TRIAL_APPROVED`, at most one entry/session
6. `PAPER_CHAMPION` only after a full dossier including reconciled Paper evidence and a separate owner approval

No fixture, replay, synthetic result, or Paper result is described as guaranteed profitability. Each stage must fail closed to the previous non-mutating behavior, except existing-position cancellation/protection/reconciliation/flatten which remains available.
