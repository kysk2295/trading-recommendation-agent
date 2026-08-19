# KR Day Learning Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run generated Strategy Capsules on future completed XKRX/KIS bars under Korean session, VI, auction, halt, designation, price-limit, quote, cost, and data-quality rules; preserve all Shadow outcomes; publish Korean daily/cumulative learning and next-session policy; and prove that no Korean broker mutation can be constructed in this repository.

**Architecture:** Consume the Shared Day Research/Capsule Foundation and generalize the existing `kr_theme_day_*` Shadow vertical through a capsule adapter rather than replacing its proven market gates and stores. An official KIS calendar receipt becomes a content-addressed XKRX session snapshot; up to three market-local capsules are evaluated sequentially only on eligible completed bars. Host code projects candidates into Shadow entries/exits, resolves same-bar collisions to stop, censors non-contiguous evidence, and records market-scoped review/report/policy artifacts. KR promotion stops at `SHADOW_CANDIDATE`; its execution eligibility is always broker-blocked and no Paper/KIS/LS order adapter exists.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite Day experiment ledger and existing KR Shadow stores, KIS/LS read-only clients, pytest, Ruff, basedpyright, React/TypeScript query-only dashboard

---

## Preconditions and safety boundary

Do not begin until `2026-08-20-shared-day-research-capsule-foundation.md` passes its completion gate. The US plan is not a dependency; KR and US may proceed independently after the shared contracts land.

KIS, LS, OpenDART, and every non-Alpaca provider remain read-only. This plan must not add or call order, balance, account, position-changing, or account WebSocket registration APIs. Specifically, LS `/stock/accno`, `/stock/order`, and WebSocket registration types `1`/`2` are forbidden. KR modules must not import or construct `PaperOrderAdmissionRequest`, any Alpaca mutation client, `PaperMutationArm`, or a generic broker interface. Credentials continue to be loaded only by existing provider-specific config code from mode-600 files and are never printed or copied into artifacts.

## Existing surfaces to preserve and generalize

- `trading_agent/kis_kr_session_calendar_client.py`, `kis_kr_session_calendar.py`, `kis_kr_session_calendar_store.py`: official read-only holiday/session evidence.
- `trading_agent/kr_session_runtime_gate.py` and `kr_theme_day_session_manifest.py`/`verifier.py`/`supervisor.py`: KST/XKRX session orchestration and evidence receipts.
- `trading_agent/kr_intraday_market_gate.py`: session, VI, auction, halt, designation, price-limit, and quote gate.
- `trading_agent/kr_theme_day_shadow_entry.py`/store and `kr_theme_day_shadow_exit.py`/cycle/store: deterministic Shadow fills and outcomes.
- `trading_agent/kr_theme_day_trial_terminal.py`/store: terminal, failed, and censored evidence.
- `trading_agent/kr_theme_day_reviewer.py`, review store, lifecycle controller/projection/store: fixed-window review and next-session behavior.
- `trading_agent/kr_theme_day_terminal_delivery.py`: immutable delivery pattern.

Keep theme-day names readable for historical audit. New generic capsule adapters write new versioned records or fields; they do not rewrite legacy rows.

## Task 1: Seal official XKRX/KST session snapshots

**Files:**
- Create: `trading_agent/kr_day_calendar_snapshot.py`
- Modify: `trading_agent/kis_kr_session_calendar.py`
- Modify: `trading_agent/kr_session_runtime_gate.py`
- Modify: `trading_agent/kr_theme_day_session_manifest.py`
- Create: `tests/test_kr_day_calendar_snapshot.py`
- Modify: `tests/test_kis_kr_session_calendar.py`
- Modify: `tests/test_kr_theme_day_session_manifest.py`

- [ ] **Step 1: Write failing calendar and phase tests**

Cover an ordinary session, official holiday, temporary closure payload, receipt freshness, malformed/unsigned payload, auction phases, regular close, timezone date rollover, and next official session. Prove the next session cannot be inferred by weekday when an official snapshot is missing.

```python
def test_missing_official_calendar_receipt_does_not_use_weekday_fallback() -> None:
    with pytest.raises(InvalidKrDayCalendarSnapshotError, match="official_calendar_required"):
        build_xkrx_session_snapshot(calendar_receipt=None, session_date=MONDAY)
```

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_kr_day_calendar_snapshot.py tests/test_kis_kr_session_calendar.py tests/test_kr_theme_day_session_manifest.py`

Expected: FAIL because the capsule-era XKRX snapshot does not exist.

- [ ] **Step 3: Implement `XkrxSessionSnapshot`**

Bind snapshot ID, KST session date, exact regular/auction phase bounds, holiday/open status, next official session, source receipt hash, source endpoint contract, received-at/published-at, and expiry. Build only from a verified `KisKrSessionCalendarReceipt` plus the existing session-phase contract. Unknown or stale evidence blocks the snapshot.

- [ ] **Step 4: Require the snapshot in runtime gates/manifests**

Replace fixed `09:00`/weekday assumptions in the capsule path with exact snapshot phase checks. Historical theme-day manifests remain readable; new capsule manifests require `calendar_snapshot_id`.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_kr_day_calendar_snapshot.py tests/test_kis_kr_session_calendar.py tests/test_kr_theme_day_session_manifest.py tests/test_kr_theme_day_session_verify_cli.py`

Expected: PASS.

```bash
git add trading_agent/kr_day_calendar_snapshot.py trading_agent/kis_kr_session_calendar.py trading_agent/kr_session_runtime_gate.py trading_agent/kr_theme_day_session_manifest.py tests/test_kr_day_calendar_snapshot.py tests/test_kis_kr_session_calendar.py tests/test_kr_theme_day_session_manifest.py
git commit -m "feat(kr-day): seal official XKRX session snapshots"
```

## Task 2: Bind Strategy Capsules to Korean read-only evidence

**Files:**
- Create: `trading_agent/kr_day_capsule_adapter.py`
- Create: `trading_agent/kr_day_capsule_models.py`
- Modify: `trading_agent/kr_same_cycle_opportunity_bundle.py`
- Modify: `trading_agent/kr_source_cycle_orchestrator.py`
- Modify: `trading_agent/kr_theme_day_signal.py`
- Create: `tests/test_kr_day_capsule_adapter.py`
- Modify: `tests/test_kr_same_cycle_opportunity_run.py`
- Modify: `tests/test_kr_theme_day_signal.py`

- [ ] **Step 1: Write failing market/evidence boundary tests**

Prove the adapter accepts only `KR_EQUITIES`, capsule cadence/evidence schema matches available KIS/LS/OpenDART receipts, completed-bar identity is current XKRX session, and generated output is converted by the shared host bridge. Reject US capsule/evidence, future/incomplete bar, stale/missing quote/spread, missing source lineage, non-KR symbol, and any generated provider/size/risk field.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_kr_day_capsule_adapter.py tests/test_kr_same_cycle_opportunity_run.py tests/test_kr_theme_day_signal.py`

Expected: FAIL because the existing signal path is theme-day strategy-specific.

- [ ] **Step 3: Implement a thin capsule adapter**

Create `KrDayCapsuleEvaluationInput` from the exact calendar snapshot, current market-constraint snapshot, completed KIS bars, point-in-time opportunity/evidence refs, cost model, and verified capsule. Call the shared generated/builtin evaluator and host `TradeSignalEnvelope` projection; never import execution or provider mutation modules.

- [ ] **Step 4: Preserve existing opportunity evidence**

Generalize `kr_same_cycle_opportunity_bundle.py` and `kr_theme_day_signal.py` only at the input/output seam. Existing theme-day strategy IDs still replay; new capsule IDs/version/trial IDs remain market-local and append-only.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_kr_day_capsule_adapter.py tests/test_kr_same_cycle_opportunity_run.py tests/test_kr_theme_day_signal.py tests/test_day_forward_probe_bridge.py`

Expected: PASS.

```bash
git add trading_agent/kr_day_capsule_adapter.py trading_agent/kr_day_capsule_models.py trading_agent/kr_same_cycle_opportunity_bundle.py trading_agent/kr_source_cycle_orchestrator.py trading_agent/kr_theme_day_signal.py tests/test_kr_day_capsule_adapter.py tests/test_kr_same_cycle_opportunity_run.py tests/test_kr_theme_day_signal.py
git commit -m "feat(kr-day): bind capsules to read-only market evidence"
```

## Task 3: Run bounded Forward Probe/Shadow under the KR market gate

**Files:**
- Create: `trading_agent/kr_day_capsule_shadow.py`
- Create: `trading_agent/kr_day_capsule_shadow_service.py`
- Modify: `trading_agent/kr_intraday_market_gate.py`
- Modify: `trading_agent/kr_theme_day_intraday.py`
- Modify: `trading_agent/kr_theme_day_shadow_entry_models.py`
- Modify: `trading_agent/kr_theme_day_shadow_entry.py`
- Modify: `trading_agent/kr_theme_day_shadow_entry_store.py`
- Modify: `trading_agent/kr_theme_day_session_supervisor.py`
- Create: `tests/test_kr_day_capsule_shadow.py`
- Create: `tests/test_kr_day_capsule_shadow_service.py`
- Modify: `tests/test_kr_intraday_market_gate.py`
- Modify: `tests/test_kr_theme_day_shadow_entry.py`

- [ ] **Step 1: Write failing gate, future-bar, and slot tests**

Cover closed/unknown session, stale/future evidence, static/dynamic VI, call auction, halt, designation, upper/lower/near-upper limit, missing/crossed quote, registration/same bar, and duplicate restart. Test at most three active capsules per KR policy and deterministic queue order; one capsule failure cannot block others.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_kr_day_capsule_shadow.py tests/test_kr_day_capsule_shadow_service.py tests/test_kr_intraday_market_gate.py tests/test_kr_theme_day_shadow_entry.py`

Expected: FAIL because Shadow entry is bound to theme-day trial fields and not capsule identity.

- [ ] **Step 3: Extend entry records without rewriting legacy rows**

Add a versioned capsule entry model or new table with `market_id`, capsule, hypothesis version, Day Forward Trial, signal, calendar snapshot, market-gate result, completed-bar/evidence hashes, cost/slippage declaration, entry/stop/targets, and fill timestamp. Reuse existing `project_kr_theme_day_shadow_entry()` pricing semantics through a generic helper.

- [ ] **Step 4: Implement `KrDayCapsuleShadowController`**

For each active capsule and completed bar, validate future-only eligibility and `assess_kr_shadow_entry()`, append a blocked/no-signal/failed/signal event, and publish an entry only after all gates pass. It writes local Shadow/Day ledgers only and cannot construct `PaperOrderAdmissionRequest`.

- [ ] **Step 5: Register a durable KST intraday service**

`KrDayCapsuleShadowService` is owned by the existing session supervisor. It wakes on each new verified completed KIS bar/evidence event, advances one XKRX market/bar cursor exactly once, invokes Discovery triggers, evaluates active capsules, and sleeps outside eligible continuous-trading phases. A KR provider/runtime failure records a blocked/censored event without advancing or blocking the US cursor.

- [ ] **Step 6: Pass and commit**

Run: `uv run pytest -q tests/test_kr_day_capsule_shadow.py tests/test_kr_day_capsule_shadow_service.py tests/test_kr_intraday_market_gate.py tests/test_kr_theme_day_shadow_entry.py tests/test_day_forward_trial.py tests/test_kr_theme_day_session_supervisor.py`

Expected: PASS.

```bash
git add trading_agent/kr_day_capsule_shadow.py trading_agent/kr_day_capsule_shadow_service.py trading_agent/kr_intraday_market_gate.py trading_agent/kr_theme_day_intraday.py trading_agent/kr_theme_day_shadow_entry_models.py trading_agent/kr_theme_day_shadow_entry.py trading_agent/kr_theme_day_shadow_entry_store.py trading_agent/kr_theme_day_session_supervisor.py tests/test_kr_day_capsule_shadow.py tests/test_kr_day_capsule_shadow_service.py tests/test_kr_intraday_market_gate.py tests/test_kr_theme_day_shadow_entry.py
git commit -m "feat(kr-day): run capsules through Korean shadow gates"
```

## Task 4: Project exits, terminal outcomes, and censorship

**Files:**
- Create: `trading_agent/kr_day_capsule_outcomes.py`
- Modify: `trading_agent/kr_theme_day_shadow_exit_models.py`
- Modify: `trading_agent/kr_theme_day_shadow_exit.py`
- Modify: `trading_agent/kr_theme_day_shadow_exit_cycle.py`
- Modify: `trading_agent/kr_theme_day_shadow_exit_store.py`
- Modify: `trading_agent/kr_theme_day_trial_terminal.py`
- Modify: `trading_agent/kr_theme_day_trial_terminal_store.py`
- Create: `tests/test_kr_day_capsule_outcomes.py`
- Modify: `tests/test_kr_theme_day_shadow_exit.py`
- Modify: `tests/test_kr_theme_day_trial_terminal.py`

- [ ] **Step 1: Write failing outcome tests**

Test stop, target, time exit, entry unfilled, no signal, blocked, runtime failed, incomplete/gapped bar chain, end-of-session unresolved, and exact replay. Explicitly prove a bar touching both stop and target resolves to stop and missing contiguous bars produce censored/unresolved, never a favorable inferred exit.

```python
def test_same_bar_stop_target_collision_resolves_to_stop() -> None:
    outcome = project_capsule_outcome(entry_fixture(), bars=(collision_bar(),))
    assert outcome.reason is KrThemeDayShadowExitReason.STOPPED
```

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_kr_day_capsule_outcomes.py tests/test_kr_theme_day_shadow_exit.py tests/test_kr_theme_day_trial_terminal.py`

Expected: FAIL because capsule lineage/terminal classifications are absent.

- [ ] **Step 3: Generalize deterministic exit projection**

Retain the existing fixed slippage/cost calculation and stop-first collision order. Add capsule/version/trial/calendar/evidence hashes to versioned outcomes. Require ordered, contiguous completed KIS bars and current XKRX session; no raw provider response is stored.

- [ ] **Step 4: Append terminal Day Forward events**

Map local terminal artifacts to shared `EXIT`, `NO_SIGNAL`, `BLOCKED`, `FAILED`, or `CENSORED` events and immutable outcome refs. Restart reads both stores and never duplicates an exit/terminal event.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_kr_day_capsule_outcomes.py tests/test_kr_theme_day_shadow_exit.py tests/test_kr_theme_day_trial_terminal.py tests/test_forward_outcomes.py`

Expected: PASS.

```bash
git add trading_agent/kr_day_capsule_outcomes.py trading_agent/kr_theme_day_shadow_exit_models.py trading_agent/kr_theme_day_shadow_exit.py trading_agent/kr_theme_day_shadow_exit_cycle.py trading_agent/kr_theme_day_shadow_exit_store.py trading_agent/kr_theme_day_trial_terminal.py trading_agent/kr_theme_day_trial_terminal_store.py tests/test_kr_day_capsule_outcomes.py tests/test_kr_theme_day_shadow_exit.py tests/test_kr_theme_day_trial_terminal.py
git commit -m "feat(kr-day): derive capsule shadow outcomes safely"
```

## Task 5: Generalize fixed-window KR review with a Shadow-only ceiling

**Files:**
- Create: `trading_agent/kr_day_capsule_reviewer.py`
- Modify: `trading_agent/kr_theme_day_reviewer.py`
- Modify: `trading_agent/kr_theme_day_review_models.py`
- Modify: `trading_agent/kr_theme_day_review_store.py`
- Modify: `trading_agent/kr_theme_day_lifecycle_controller.py`
- Modify: `trading_agent/kr_theme_day_lifecycle_projection.py`
- Create: `tests/test_kr_day_capsule_reviewer.py`
- Modify: `tests/test_kr_theme_day_reviewer.py`
- Modify: `tests/test_kr_theme_day_lifecycle.py`

- [ ] **Step 1: Write failing review-integrity tests**

Prove fixed review date/window cannot be shortened by good interim results; failed/censored/no-signal/blocked attempts are counted; selection-adjusted metrics use every attempted version; evidence seal is KR-only; US evidence/returns reject the dossier; and no action can exceed `SHADOW_CANDIDATE`. The existing 20 completed sessions and 30 completed trades are only the `COMPARISON_READY` operating minimum and never order eligibility.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_kr_day_capsule_reviewer.py tests/test_kr_theme_day_reviewer.py tests/test_kr_theme_day_lifecycle.py`

Expected: FAIL because existing review is one theme strategy and does not model capsule/all-attempt lineage.

- [ ] **Step 3: Implement capsule review over proven stores**

Reuse `review_kr_theme_day_strategy()` aggregation primitives, but source trials/attempts by exact KR capsule/version and a preregistered review seal. Preserve existing minimum evidence rules unless the shared promotion policy is stricter. Produce `REJECTED`, `INSUFFICIENT`, or `SHADOW_CANDIDATE` only.

- [ ] **Step 4: Append lifecycle/eligibility projection**

Append the shared `PromotionDecision`; derive a KR `ExecutionEligibility` with status `BLOCKED`, reason `provider_read_only`, and no owner/broker authority class. A lifecycle controller must reject Paper trial/champion targets for `KR_EQUITIES`.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_kr_day_capsule_reviewer.py tests/test_kr_theme_day_reviewer.py tests/test_kr_theme_day_lifecycle.py tests/test_day_research_review.py`

Expected: PASS.

```bash
git add trading_agent/kr_day_capsule_reviewer.py trading_agent/kr_theme_day_reviewer.py trading_agent/kr_theme_day_review_models.py trading_agent/kr_theme_day_review_store.py trading_agent/kr_theme_day_lifecycle_controller.py trading_agent/kr_theme_day_lifecycle_projection.py tests/test_kr_day_capsule_reviewer.py tests/test_kr_theme_day_reviewer.py tests/test_kr_theme_day_lifecycle.py
git commit -m "feat(kr-day): review capsules with shadow-only authority"
```

## Task 6: Publish KR close reports and next-XKRX-session policy

**Files:**
- Create: `trading_agent/kr_day_market_close_report.py`
- Create: `trading_agent/kr_day_learning_policy.py`
- Modify: `trading_agent/kr_day_capsule_shadow_service.py`
- Modify: `trading_agent/kr_theme_day_terminal_delivery.py`
- Modify: `trading_agent/kr_theme_day_review_store.py`
- Create: `tests/test_kr_day_market_close_report.py`
- Create: `tests/test_kr_day_learning_policy.py`
- Modify: `tests/test_kr_theme_day_terminal_delivery.py`

- [ ] **Step 1: Write failing finalization/report/policy tests**

Require all due trial events to be terminal or explicitly censored, all local entries/exits to be linked, review/lifecycle projection complete, and an official next-session calendar snapshot. Test daily and cumulative cost-adjusted Shadow return, win rate, mean R, PF, MDD, failed/censored counts, selection diagnostics, risk/data incidents, lineage, review date, and `execution.provider_read_only`.

Late source evidence must create a new immutable report with `previous_report_id`; the latest final revision alone may drive policy.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_kr_day_market_close_report.py tests/test_kr_day_learning_policy.py tests/test_kr_theme_day_terminal_delivery.py`

Expected: FAIL because no capsule-era market report exists.

- [ ] **Step 3: Implement KR-only report projection**

Read the Day experiment ledger plus existing KR Shadow stores query-only. Partition by `(KR_EQUITIES, official_session_date)` and exact capsule. Do not include US/Paper values and do not label Shadow P&L as actual execution or profitability.

- [ ] **Step 4: Implement official next-session policy**

The policy may keep/rotate/suspend/no-trade, activate up to three KR Shadow capsules, and reorder the queue. It cannot change capsule source/parameters/costs, risk, promotion, or execution authority. Activation requires the exact next `XkrxSessionSnapshot`, not a weekday.

Wire the finalization watermark, immutable report revision, and next-session policy into `KrDayCapsuleShadowService`/session supervisor after official close. The service must not activate that policy in the closed/current session.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_kr_day_market_close_report.py tests/test_kr_day_learning_policy.py tests/test_kr_theme_day_terminal_delivery.py tests/test_day_learning_reports.py`

Expected: PASS.

```bash
git add trading_agent/kr_day_market_close_report.py trading_agent/kr_day_learning_policy.py trading_agent/kr_day_capsule_shadow_service.py trading_agent/kr_theme_day_terminal_delivery.py trading_agent/kr_theme_day_review_store.py tests/test_kr_day_market_close_report.py tests/test_kr_day_learning_policy.py tests/test_kr_theme_day_terminal_delivery.py
git commit -m "feat(kr-day): report shadow learning and next policy"
```

## Task 7: Prove Korean provider mutation is structurally impossible

**Files:**
- Create: `trading_agent/kr_day_read_only_boundary.py`
- Create: `tests/test_kr_day_read_only_boundary.py`
- Modify: `tests/test_kis_kr_market_client.py`
- Modify: `tests/test_kis_intraday_http.py`
- Modify: `tests/test_ls_nws_collect_cli.py`
- Modify: `tests/test_kr_theme_day_session_e2e.py`

- [ ] **Step 1: Write failing import-graph and endpoint tests**

Parse the runtime import graph rooted at all new `kr_day_*` modules and the generalized `kr_theme_day_*` orchestration modules. Reject imports of:

- `paper_*`, `alpaca_paper_mutation_*`, `PaperOrderAdmissionRequest`, `PaperMutationArm`;
- any KIS/LS order/account/balance/position mutation module;
- generic broker/execution adapters.

Parse string literals/capability declarations and reject `/stock/accno`, `/stock/order`, order/balance/account/position-changing endpoints, and LS WebSocket registration types `1`/`2`. Allow reviewed read-only quotation/news contracts, including LS NWS registration type `3`.

- [ ] **Step 2: Prove the structural tests are red**

Run: `uv run pytest -q tests/test_kr_day_read_only_boundary.py`

Expected: FAIL because no explicit KR Day capability boundary/manifest exists.

- [ ] **Step 3: Add a closed read-only capability manifest**

`KrDayReadOnlyCapability` lists exact provider/module/endpoint/TR/WebSocket contracts consumed by the vertical. `require_kr_day_read_only_boundary()` verifies the manifest at startup and returns only evidence-reader protocols; it exposes no submit/cancel/account/balance/position methods. Do not add stubs for future Korean orders.

- [ ] **Step 4: Add runtime fake-transport proofs**

Execute a full fixture session with fake KIS/LS transports recording method/path/message calls. Assert all calls match the reviewed read-only allowlist and no mutation-shaped request is attempted. Prove a KR capsule object cannot type-check or runtime-construct `PaperOrderAdmissionRequest` through the KR controller API.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_kr_day_read_only_boundary.py tests/test_kis_kr_market_client.py tests/test_kis_intraday_http.py tests/test_ls_nws_collect_cli.py tests/test_kr_theme_day_session_e2e.py`

Expected: PASS.

```bash
git add trading_agent/kr_day_read_only_boundary.py tests/test_kr_day_read_only_boundary.py tests/test_kis_kr_market_client.py tests/test_kis_intraday_http.py tests/test_ls_nws_collect_cli.py tests/test_kr_theme_day_session_e2e.py
git commit -m "test(kr-day): prove provider mutation is unreachable"
```

## Task 8: Add the combined query-only Day Agent façade

Run this integration task after US plan Task 10 has created `dashboard_projection_day_learning.py`; KR Shadow Tasks 1-7 and 9 do not depend on the US vertical.

**Files:**
- Create: `trading_agent/dashboard_projection_day_agent.py`
- Modify: `trading_agent/dashboard_projection_day_learning.py`
- Modify: `trading_agent/dashboard_agent_runtime_projection.py`
- Modify: `dashboard/src/workspaces/markets.ts`
- Modify: `dashboard/src/workspaces/research.ts`
- Modify: `dashboard/package.json`
- Create: `dashboard/scripts/run-day-agent-qa.ts`
- Create: `tests/test_dashboard_projection_day_agent.py`

- [ ] **Step 1: Write failing façade tests**

Test side-by-side latest verified US/KR report IDs, independent session dates/cursors, separate Paper/US Shadow/KR Shadow metrics, active/queued/suspended capsules, and separate next-session policies. A missing or failed market shows unavailable/blocked without hiding the other. No combined P&L, return, confidence interval, promotion input, or mutation action may exist.

- [ ] **Step 2: Prove tests are red**

Run: `uv run pytest -q tests/test_dashboard_projection_day_agent.py`

Expected: FAIL because the unified query-only façade is absent.

- [ ] **Step 3: Implement report-link projection**

Build `DailyLearningReport` from the latest verified report ID for each market and a Discovery summary. Keep it query-only and explicitly reject it as a writer/reviewer/policy source. Redact raw provider/broker data, credentials, account IDs, and sealed holdout metrics.

- [ ] **Step 4: Render market/lane labels**

Show `US · Alpaca Paper`, `US · Shadow`, and `KR · Shadow · provider read-only` as distinct slices. Add the disclaimer that Paper/Shadow results do not establish future profitability. Add `qa:day-agent` to `dashboard/package.json`; `run-day-agent-qa.ts` must start/use the real dashboard surface, load verified fixture reports, assert the three labels and absence of combined-return/mutation controls, and save its QA result artifact.

- [ ] **Step 5: Pass and commit**

Run: `uv run pytest -q tests/test_dashboard_projection_day_agent.py tests/test_dashboard_projection_day_learning.py tests/test_dashboard_projection_paper.py`

Expected: PASS.

Run: `cd dashboard && bun run check && bun run build && bun run qa:day-agent`

Expected: typecheck/lint/tests/build exit 0 and the browser QA observes separate US Paper, US Shadow, and KR Shadow sections with no combined-return or mutation control.

```bash
git add trading_agent/dashboard_projection_day_agent.py trading_agent/dashboard_projection_day_learning.py trading_agent/dashboard_agent_runtime_projection.py dashboard/src/workspaces/markets.ts dashboard/src/workspaces/research.ts dashboard/package.json dashboard/scripts/run-day-agent-qa.ts tests/test_dashboard_projection_day_agent.py
git commit -m "feat(dashboard): add dual-market day agent facade"
```

## Task 9: Verify the KR vertical through its real CLI surface

**Files:**
- Create: `run_kr_day_capsule_shadow.py`
- Create: `run_kr_day_close_report.py`
- Modify: `trading_agent/kr_theme_day_onboarding.py`
- Modify: `trading_agent/kr_theme_day_session_supervisor.py`
- Create: `tests/test_kr_day_capsule_shadow_cli.py`
- Create: `tests/test_kr_day_close_report_cli.py`
- Create: `tests/fixtures/kr-day/stale-calendar.json`
- Create: `tests/fixtures/kr-day/valid-next-bar.json`
- Create: `tests/fixtures/kr-day/finalized-session.json`
- Modify: `tests/test_kr_theme_day_session_supervisor.py`

- [ ] **Step 1: Add CLI help, malformed input, and local happy-path tests**

Help must expose local/read-only evidence and ledger paths only; reject flags containing account, balance, position, order, arm, broker, mutation endpoint, or force. Bad fixtures cover stale calendar, mixed market, incomplete bar, forbidden capability, and cross-market policy. Happy fixtures run one completed-bar Shadow cycle and one finalized report without network mutation.

- [ ] **Step 2: Prove the CLI tests are red**

Run: `uv run pytest -q tests/test_kr_day_capsule_shadow_cli.py tests/test_kr_day_close_report_cli.py`

Expected: FAIL because the new CLI modules and fixture parsers are absent.

- [ ] **Step 3: Implement both read-only CLIs**

Use `argparse`. `run_kr_day_capsule_shadow.py` accepts an official calendar snapshot/verified local evidence fixture, Day experiment ledger, generated artifact root, and KR Shadow root; it emits sanitized capsule/trial/event/gate IDs only. `run_kr_day_close_report.py` accepts query-only Day and KR Shadow roots plus finalization evidence and emits report/policy IDs and modeled metrics labeled `provider_read_only`. Neither parser exposes credential, provider endpoint, account, balance, position, order, arm, broker, force, or Paper options. Invalid input/publication exits non-zero without traceback; blocked/no-signal/censored are valid terminal research exits.

- [ ] **Step 4: Run the focused verification suite**

```bash
uv run pytest -q \
  tests/test_kr_day_calendar_snapshot.py \
  tests/test_kr_day_capsule_adapter.py \
  tests/test_kr_day_capsule_shadow.py \
  tests/test_kr_day_capsule_shadow_service.py \
  tests/test_kr_day_capsule_outcomes.py \
  tests/test_kr_day_capsule_reviewer.py \
  tests/test_kr_day_market_close_report.py \
  tests/test_kr_day_learning_policy.py \
  tests/test_kr_day_read_only_boundary.py \
  tests/test_dashboard_projection_day_agent.py \
  tests/test_kr_intraday_market_gate.py \
  tests/test_kr_theme_day_shadow_entry.py \
  tests/test_kr_theme_day_shadow_exit.py \
  tests/test_kr_theme_day_reviewer.py \
  tests/test_kr_theme_day_session_e2e.py \
  tests/test_kr_day_capsule_shadow_cli.py \
  tests/test_kr_day_close_report_cli.py
uv run ruff check trading_agent/kr_day_*.py trading_agent/kr_theme_day_*.py run_kr_day_capsule_shadow.py run_kr_day_close_report.py tests/test_kr_day_*.py
uv run basedpyright trading_agent/kr_day_calendar_snapshot.py trading_agent/kr_day_capsule_adapter.py trading_agent/kr_day_capsule_shadow.py trading_agent/kr_day_capsule_shadow_service.py trading_agent/kr_day_capsule_outcomes.py trading_agent/kr_day_capsule_reviewer.py trading_agent/kr_day_market_close_report.py trading_agent/kr_day_learning_policy.py trading_agent/kr_day_read_only_boundary.py trading_agent/dashboard_projection_day_agent.py run_kr_day_capsule_shadow.py run_kr_day_close_report.py
```

Expected: all exit 0.

- [ ] **Step 5: Manually exercise the user surface**

```bash
kr_day_tmp=$(mktemp -d)
uv run python run_kr_day_capsule_shadow.py --help
uv run python run_kr_day_capsule_shadow.py --fixture tests/fixtures/kr-day/stale-calendar.json
uv run python run_kr_day_capsule_shadow.py --fixture tests/fixtures/kr-day/valid-next-bar.json --experiment-ledger "$kr_day_tmp/experiments.sqlite3" --shadow-root "$kr_day_tmp/shadow"
uv run python run_kr_day_close_report.py --fixture tests/fixtures/kr-day/finalized-session.json --experiment-ledger "$kr_day_tmp/experiments.sqlite3" --shadow-root "$kr_day_tmp/shadow"
```

Observe: help has no mutation/account/arm options; stale calendar fails closed; valid run records Shadow only; report says `provider_read_only`, separates all terminal statuses, and makes no profitability claim.

- [ ] **Step 6: Run an optional provider read-only smoke only with valid local credentials**

If KIS/LS credentials already exist in the required mode-600 files, run the existing reviewed market/calendar/news collectors and record sanitized receipt IDs only. Do not request or use credentials from chat. Assert captured traffic remains on allowlisted read-only contracts. No Korean order, account, balance, or position call is part of this plan.

- [ ] **Step 7: Commit verification surfaces**

```bash
git add run_kr_day_capsule_shadow.py run_kr_day_close_report.py trading_agent/kr_theme_day_onboarding.py trading_agent/kr_theme_day_session_supervisor.py tests/test_kr_day_capsule_shadow_cli.py tests/test_kr_day_close_report_cli.py tests/fixtures/kr-day/stale-calendar.json tests/fixtures/kr-day/valid-next-bar.json tests/fixtures/kr-day/finalized-session.json tests/test_kr_theme_day_session_supervisor.py
git commit -m "test(kr-day): verify capsule shadow and read-only safety"
```

## Activation gate

Enable stages in order with append-only feature-state evidence:

1. `CONTRACT_ONLY`
2. `GENERATED_FORWARD_PROBE`
3. `DAILY_LEARNING` after at least five natural XKRX sessions
4. `STEADY_SHADOW` with fixed review windows and bounded active slots

There is no KR Paper or live-order stage. Any future Korean real-order objective requires a separate project, security policy, credentials boundary, broker reconciliation design, and explicit owner approval; it must not be anticipated through a generic adapter here.
