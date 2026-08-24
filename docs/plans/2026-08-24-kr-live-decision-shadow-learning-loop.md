# KR Live Decision, Shadow Trading, and Learning Loop Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 한국시장 장중 포착 종목을 2분 이내에 조건부 진입 계획 또는 명시적 기각으로 판정하고, 최대 3개 전략 캡슐의 가상매매·실시간 알림·대시보드·장 마감 학습까지 하나의 감사 가능한 루프로 연결한다.

**Architecture:** 이미 운영 중인 `run_kr_strategy_research_service.py`와 `run_day_session_service.py`를 유일한 장중 오케스트레이션 경로로 유지한다. 기존 `KrDayCapsuleShadowEvent`는 실제 가상 포지션의 원장으로 보존하고, 진입 전 판단만 별도의 append-only 결정 원장에 기록한다. 빠른 루프는 결정론적 데이터·리스크 규칙만 사용하고, 느린 장 마감 루프에서 실패 단계를 진단해 한 번에 하나의 challenger만 다음 세션 shadow 평가에 등록한다.

**Tech Stack:** Python, dataclasses/enums, SQLite append-only stores, launchd, pytest, Ruff, basedpyright, KIS read-only market data, Hermes delivery, existing dashboard projection.

---

## 1. Product Contract

사용자에게 보이는 핵심 약속은 다음과 같다.

1. 장중 포착된 종목은 다음 서비스 주기 안에 반드시 이유가 포함된 결정 상태가 된다. 최초 `INVESTIGATING`은 허용하지만 같은 근거와 같은 완료 봉에서 반복할 수 없고, 다음 완료 봉까지 아래 둘 중 하나로 확정돼야 한다.
   - `ARMED` 또는 `ACTIVE`: 진입 조건/가격, 손절, 목표가, 무효화 조건, 유효 시각, 근거가 포함된 계획
   - `REJECTED`, `BLOCKED`, 또는 `EXPIRED`: 매매하지 않는 구체적 이유가 포함된 판정
2. 단순히 거래대금 순위에 들어왔다는 이유만으로 매매 추천을 만들지 않는다.
3. 체결이 일어나지 않은 `ARMED` 계획은 실제 진입처럼 표시하지 않는다.
4. `ACTIVE` 이후에는 같은 완료 봉 기준으로 손절을 먼저 판정하고, 목표·만료·장 마감 상태를 불변 이력으로 남긴다.
5. Hermes는 상태 변화가 있을 때만 알리고, 2분마다 동일한 관찰 알림을 반복하지 않는다.
6. 대시보드는 현재 상태뿐 아니라 왜 진입했거나 기각했는지와 이후 결과를 보여준다.
7. 학습 루프는 장중 전략을 직접 바꾸지 않는다. 장 마감 후 실패 단계를 하나 선택하고, 한 가지 변경만 포함한 challenger를 다음 세션 shadow로 등록한다.

### Canonical lifecycle

```text
DISCOVERED
  -> INVESTIGATING
       -> ARMED
            -> ACTIVE
                 -> STOPPED | TARGETED | CENSORED
       -> REJECTED | BLOCKED | EXPIRED
  -> REVIEWED (after close)
```

`INVESTIGATING`과 `ARMED`는 진입 전 결정 원장, `ACTIVE`와 종료 상태는 기존 shadow 원장이 소유한다. 과거 행은 갱신하지 않고 새 이벤트를 추가한다.

## 2. Safety and Non-goals

- KIS와 LS는 시세·뉴스 등 승인된 읽기 전용 계약만 사용한다. 주문·계좌·잔고·포지션 변경 API는 호출하지 않는다.
- 한국시장 주문은 전부 shadow 체결이다.
- 미국시장 실행은 Alpaca Paper 전용이며 정확히 `https://paper-api.alpaca.markets`만 허용한다.
- LLM은 장중 주문 권한, 리스크 한도, 체결 판정, 손절 판정을 갖지 않는다.
- 장중 프롬프트나 전략 코드를 자동 수정하지 않는다.
- 백테스트·replay·shadow 성과를 실제 수익성으로 표현하지 않는다.
- `data/regend_us_stocks` 및 전체 유니버스 백테스트를 사용하지 않는다.

## 3. Current Capability and Gap Map

| Capability | Current state | Planned change |
|---|---|---|
| 2-minute KR source cycle | Implemented | Reuse unchanged |
| KRX session/freshness checks | Implemented | Reuse and propagate reason codes |
| Up to three active capsules | Implemented | Reuse existing authority |
| Theme VWAP impulse/pullback/reclaim | Implemented at final setup only | Expose setup progression before reclaim |
| Paper/shadow entry, stop, target | Implemented | Consume an admitted `ARMED` decision |
| Same-bar stop-first | Implemented | Preserve and regression-test |
| Explicit non-entry reasons | Partial/coarse | Add granular decision reasons |
| State-change-only Hermes delivery | Missing on modern path | Add projection and deduplication |
| Dashboard entry/stop/targets | Implemented for shadow events | Add investigating/armed/rejected views |
| Close report and learning policy | Implemented as callable logic | Add scheduled idempotent finalizer |
| One-patch challenger loop | Implemented generically | Feed KR close diagnosis into it |

## 4. Fast and Slow Loops

### Fast loop: every 120 seconds during the KRX session

```text
completed market bar
  -> existing opportunity discovery
  -> capsule-specific admission and setup assessment
  -> append decision event
  -> optional ARMED -> ACTIVE shadow transition
  -> dashboard projection
  -> Hermes delivery only on state change
```

### Slow loop: after the KRX close

```text
decision + shadow event ledgers
  -> final metrics and failure-stage attribution
  -> daily report
  -> choose one weakest stage
  -> generate one challenger patch
  -> register future-session shadow trial
  -> promotion gate after predeclared evidence threshold
```

## 5. Implementation Tasks

### Task 1: Lock the observable decision contract

**Files:**

- Create: `tests/test_kr_live_decision_contract.py`
- Reference: `trading_agent/day_session_service.py`
- Reference: `trading_agent/kr_day_capsule_shadow_service.py`

**Step 1: Write the failing end-to-end contract test**

Build a minimal current-session opportunity and assert that one service tick produces exactly one user-interpretable disposition per active capsule:

```python
assert decision.status in {
    "INVESTIGATING",
    "ARMED",
    "REJECTED",
    "BLOCKED",
    "EXPIRED",
}
assert decision.reason_codes
assert decision.observed_at is not None
```

Also assert that no opportunity is silently represented only by `None` and that a no-op tick does not fabricate a recommendation.

Add a second completed-bar cycle assertion: an unchanged candidate may not remain indefinitely `INVESTIGATING`; by that cycle it must become `ARMED`, `REJECTED`, `BLOCKED`, or `EXPIRED`.

**Step 2: Run the new test and prove the missing boundary**

Run: `pytest -q tests/test_kr_live_decision_contract.py`

Expected: FAIL because the pre-entry decision model/service does not exist yet.

**Step 3: Commit the red contract test**

```bash
git add tests/test_kr_live_decision_contract.py
git commit -m "test: lock KR live decision contract"
```

### Task 2: Add immutable pre-entry decision models and storage

**Files:**

- Create: `trading_agent/kr_day_decision_models.py`
- Create: `trading_agent/kr_day_decision_store.py`
- Create: `tests/test_kr_day_decision_store.py`

**Step 1: Define the smallest model surface**

Add enums for:

```python
class KrDayDecisionStatus(StrEnum):
    INVESTIGATING = "INVESTIGATING"
    ARMED = "ARMED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    EXPIRED = "EXPIRED"
```

Add structured reason codes including:

- `THEME_BREADTH_MISSING`
- `CATALYST_SOURCE_MISSING`
- `VOLUME_CONFIRMATION_MISSING`
- `FLOW_CONFIRMATION_MISSING`
- `PRICE_SETUP_INCOMPLETE`
- `SPREAD_TOO_WIDE`
- `MARKET_GATE_BLOCKED`
- `STALE_EVIDENCE`
- `DUPLICATE_THESIS`
- `OPPORTUNITY_EXPIRED`

An `ARMED` record must include a `conditional_plan` with trigger rule, indicative entry or trigger price, stop, targets, invalidation, `valid_until`, rationale, capsule/version identifiers, evidence references, and `paper_only=True`. An unavailable exact entry must remain explicitly conditional; it must not be copied from a stale quote.

**Step 2: Write store tests**

Test append-only behavior, deterministic serialization, `0600` database permissions, restart persistence, and idempotency on `(session_date, capsule_id, opportunity_id, completed_bar_at, status)`.

**Step 3: Implement the store**

Follow the established patterns in `trading_agent/kr_day_capsule_shadow_store.py`. Never update or delete an older decision event.

**Step 4: Verify**

Run:

```bash
pytest -q tests/test_kr_day_decision_store.py
ruff check trading_agent/kr_day_decision_models.py trading_agent/kr_day_decision_store.py tests/test_kr_day_decision_store.py
basedpyright trading_agent/kr_day_decision_models.py trading_agent/kr_day_decision_store.py
```

The Task 1 end-to-end contract remains intentionally red until Task 5 connects the decision service to the session service.

**Step 5: Commit**

```bash
git add trading_agent/kr_day_decision_models.py trading_agent/kr_day_decision_store.py tests/test_kr_day_decision_store.py
git commit -m "feat: add immutable KR pre-entry decisions"
```

### Task 3: Expose price-setup progression and conditional plans

**Files:**

- Modify: `trading_agent/kr_theme_day_setup.py`
- Create: `trading_agent/kr_price_grid.py`
- Create: `trading_agent/kr_theme_day_setup_progress.py`
- Create: `tests/test_kr_theme_day_setup_progress.py`
- Create: `tests/test_kr_price_grid.py`

**Step 1: Verify the current official KRX price-unit rules**

Before coding, verify the current KRX specification from an official source. Encode the effective date or ruleset version. Do not infer tick sizes from historical memory.

**Step 2: Test tick-grid boundaries**

Write tests for every price-unit bracket boundary and for upward/downward normalization. The entry, stop, and targets must always land on valid price units.

**Step 3: Add setup assessment without breaking the final setup API**

Introduce `assess_kr_theme_day_setup(...)` that returns a phase and evidence even when reclaim is incomplete. Keep the reusable scan state in `kr_theme_day_setup_progress.py` so the existing setup module remains below the 250-pure-LOC limit:

```text
NO_IMPULSE -> INVESTIGATING
IMPULSE_ONLY -> INVESTIGATING
PULLBACK_FOUND -> ARMED with conditional reclaim trigger
RECLAIM_CONFIRMED -> ready for ACTIVE evaluation
SETUP_EXPIRED -> EXPIRED
```

Keep `derive_kr_theme_day_setup(...)` as the final reclaim-compatible API used by existing callers until all call sites migrate.

**Step 4: Make the conditional plan truthful**

- Entry: normalized reclaim trigger or current ask only after trigger confirmation
- Stop: normalized pullback invalidation level
- Targets: capsule policy R multiples, normalized to the valid grid
- Validity: current session and explicit expiration bar/time
- Rationale: impulse, pullback, reclaim, volume, and current market-gate evidence

**Step 5: Verify**

Run:

```bash
pytest -q tests/test_kr_theme_day_setup.py tests/test_kr_theme_day_setup_progress.py tests/test_kr_price_grid.py
ruff check trading_agent/kr_theme_day_setup.py trading_agent/kr_theme_day_setup_progress.py trading_agent/kr_price_grid.py tests/test_kr_theme_day_setup_progress.py tests/test_kr_price_grid.py
basedpyright trading_agent/kr_theme_day_setup.py trading_agent/kr_theme_day_setup_progress.py trading_agent/kr_price_grid.py tests/test_kr_theme_day_setup_progress.py tests/test_kr_price_grid.py
```

**Step 6: Commit**

```bash
git add trading_agent/kr_theme_day_setup.py trading_agent/kr_theme_day_setup_progress.py trading_agent/kr_price_grid.py tests/test_kr_theme_day_setup_progress.py tests/test_kr_price_grid.py
git commit -m "feat: expose KR setup progression"
```

### Task 4: Add a theme, catalyst, volume, and flow admission gate

**Files:**

- Create: `trading_agent/kr_day_candidate_admission.py`
- Create: `tests/test_kr_day_candidate_admission.py`
- Modify: `trading_agent/kr_day_decision_models.py`

**Step 1: Write policy-driven admission tests**

Cover:

- theme breadth and related-symbol confirmation
- sourced catalyst presence/absence
- completed-bar volume confirmation
- executed trading/price-response confirmation
- spread and current market-gate constraints
- duplicate thesis suppression

Use a fixture shaped like the observed `005930` watch: high trading value but `publisher_count=0`, `related_symbols=1`, and neutral volume ratio. Assert it cannot become `ARMED` or `ACTIVE` and records the exact missing-evidence reason codes.

**Step 2: Implement a versioned capsule admission policy**

Thresholds belong to the registered capsule/policy version, not mutable global state. Order-book imbalance alone must never satisfy flow confirmation because it can disappear without execution.

**Step 3: Preserve evidence, including failures**

Persist every failed gate and the feature values that produced it. Do not discard a candidate merely because it was rejected.

**Step 4: Verify and commit**

Run:

```bash
pytest -q tests/test_kr_day_candidate_admission.py tests/test_kr_live_decision_contract.py
ruff check trading_agent/kr_day_candidate_admission.py tests/test_kr_day_candidate_admission.py
basedpyright trading_agent/kr_day_candidate_admission.py
```

Commit: `feat: gate KR candidates with explicit evidence`

### Task 5: Integrate decisions into the existing session service

**Files:**

- Create: `trading_agent/kr_day_decision_service.py`
- Create: `trading_agent/kr_day_decision_projection.py`
- Create: `trading_agent/kr_day_session_materializer.py`
- Create: `trading_agent/kr_day_capsule_shadow_projection.py`
- Modify: `trading_agent/day_session_service.py`
- Modify: `trading_agent/kr_day_capsule_adapter.py`
- Modify: `trading_agent/kr_day_capsule_shadow_service.py`
- Modify: `run_kr_day_capsule_shadow.py`
- Modify: `tests/test_day_session_service.py`
- Modify: `tests/test_kr_live_decision_contract.py`
- Modify: `tests/test_kr_day_capsule_shadow_cli.py`

**Step 1: Write integration tests**

For each active capsule, one completed bar must produce an idempotent decision. Test zero, one, and three active capsules. Test that an existing `ACTIVE` position continues to be managed when no new opportunity is discovered.

**Step 2: Implement the decision service**

The service must:

1. validate the session, completed-bar timestamp, quote freshness, spread, and market gate;
2. evaluate candidate admission;
3. assess setup progression;
4. append exactly one decision transition when state changes;
5. return a projection for shadow evaluation, dashboard, and delivery.

Keep policy/setup-to-decision projection in `kr_day_decision_projection.py` so request
orchestration, replay binding, and append-only storage remain below the 250-pure-LOC
module limit in `kr_day_decision_service.py`.

Keep per-capsule opportunity/receipt materialization in
`kr_day_session_materializer.py`. Management requests must bind exactly to the prior
`ACTIVE` capsule, session, symbol, collection cycle, and calendar lineage; mixed batches
must not let a non-active sibling block an active sibling's management.

Keep shadow event payload/identity projection in `kr_day_capsule_shadow_projection.py`
so the state machine and its exact-lineage defense remain below the 250-pure-LOC limit.

**Step 3: Wire it into `_run_kr`**

Use the existing active-capsule authority and state root. Do not add another daemon or another market scanner. Do not let a missing source opportunity stop management of an already-active shadow position.

An expired opportunity may be reused only as immutable lineage for a management-only
evaluation when the shadow store already has the same capsule/session in `ACTIVE`.
It must never admit a new decision or open a new shadow position. Current completed bars,
market constraints, and the normal risk/stop-first lifecycle remain mandatory.

**Step 4: Verify and commit**

Run:

```bash
pytest -q tests/test_day_session_service.py tests/test_kr_live_decision_contract.py tests/test_kr_same_cycle_day_session_e2e.py
ruff check trading_agent/day_session_service.py trading_agent/kr_day_decision_service.py trading_agent/kr_day_decision_projection.py tests/test_day_session_service.py
basedpyright trading_agent/day_session_service.py trading_agent/kr_day_decision_service.py trading_agent/kr_day_decision_projection.py
```

Commit: `feat: integrate KR decisions into session service`

### Task 6: Connect ARMED decisions to shadow execution

**Files:**

- Modify: `run_kr_day_capsule_shadow.py`
- Modify: `trading_agent/day_session_service.py`
- Modify: `trading_agent/kr_day_capsule_adapter.py`
- Modify: `trading_agent/kr_day_capsule_models.py`
- Modify: `trading_agent/kr_day_capsule_outcomes.py`
- Modify: `trading_agent/kr_day_market_close_report.py`
- Modify: `trading_agent/kr_day_capsule_shadow_service.py`
- Create: `trading_agent/kr_day_shadow_decision_bridge.py`
- Modify: `trading_agent/kr_theme_day_signal.py`
- Modify: `tests/test_kr_day_capsule_shadow.py`
- Modify: `tests/test_kr_day_capsule_shadow_safety.py`
- Modify: `tests/test_kr_day_capsule_shadow_cli.py`
- Modify: `tests/kr_day_shadow_support.py`
- Modify: `tests/test_day_session_service.py`

**Step 1: Add transition tests**

Test:

- `ARMED` remains unfilled before the trigger;
- confirmed trigger plus current quote and passing risk gate creates `ACTIVE`;
- stale, missing, crossed, halted, VI, call-auction, or price-limit conditions block entry before any fill;
- same-bar stop/target collision resolves to stop;
- reasons from the market gate and admission gate survive projection instead of collapsing into `SIGNAL_BLOCKED`.

**Step 2: Implement the narrow bridge**

The day-session subprocess receives the private decision-store path and resolves the exact latest immutable
decision for each request. A focused bridge binds capsule, hypothesis, opportunity, symbol, completed bar,
request-input SHA, plan validity, trigger readiness, and granular decision/market-gate reasons. The shadow
service remains the sole owner of actual shadow fill, slippage, stop, target, censor, and position lifecycle
events. Existing ACTIVE management uses only its exact stored symbol/cycle/calendar lineage and does not
require a new decision. Preserve shadow event schema v1 identities; expose the immutable decision+shadow
join through the service/CLI result instead of mutating historical event payloads.

**Step 3: Verify and commit**

Run:

```bash
pytest -q tests/test_kr_day_capsule_shadow.py tests/test_kr_day_capsule_shadow_safety.py tests/test_kr_day_capsule_shadow_cli.py
ruff check trading_agent/kr_day_capsule_shadow_service.py trading_agent/kr_theme_day_signal.py
basedpyright trading_agent/kr_day_capsule_shadow_service.py trading_agent/kr_theme_day_signal.py
```

Commit: `feat: execute admitted KR plans in shadow`

### Task 7: Deliver only meaningful state changes through Hermes

**Files:**

- Create: `trading_agent/kr_day_decision_delivery.py`
- Create: `trading_agent/kr_day_decision_delivery_identity.py`
- Create: `trading_agent/kr_day_decision_delivery_records.py`
- Create: `trading_agent/kr_day_decision_delivery_rendering.py`
- Create: `trading_agent/kr_day_delivery_supplements.py`
- Modify: `trading_agent/hermes_delivery_projection.py`
- Modify: `tests/test_hermes_delivery_e2e.py`
- Modify: `tests/test_hermes_plugin_delivery.py`

**Step 1: Define the delivery mapping**

| Internal transition | Hermes kind | User message |
|---|---|---|
| first `INVESTIGATING` | no push by default | retained in DB/dashboard |
| `ARMED` | `ACTIONABLE` | clearly labeled conditional plan |
| `ACTIVE` | `ACTIONABLE` reply | shadow entry, stop, targets, timestamp |
| `REJECTED`/`BLOCKED` after a prior user-visible state | `INVALIDATION` | explicit reason |
| `STOPPED`/`TARGETED`/`CENSORED` | `EXIT` | outcome and immutable history |
| service/data failure | `INCIDENT` | failure and affected scope |
| close report | `DAILY_SUMMARY` | metrics, failures, challenger decision |

**Step 2: Test delivery deduplication**

Ten repeated no-signal ticks must produce zero duplicate pushes. One thesis must form one Hermes thread: `ARMED` once, `ACTIVE` once, terminal `EXIT` once. Deduplicate by stable thesis/opportunity/capsule/state identifiers, not message text.

**Step 3: Implement and verify**

Run:

```bash
pytest -q tests/test_hermes_delivery_e2e.py tests/test_hermes_plugin_delivery.py
ruff check trading_agent/kr_day_decision_delivery.py trading_agent/hermes_delivery_projection.py
basedpyright trading_agent/kr_day_decision_delivery.py trading_agent/hermes_delivery_projection.py
```

Commit: `feat: notify KR state changes through Hermes`

### Task 8: Project the full lifecycle on the dashboard

**Files:**

- Modify: `trading_agent/dashboard_projection_day_agent.py`
- Modify: `tests/test_dashboard_projection_day_agent.py`

**Step 1: Write projection tests**

Assert the dashboard displays:

- `INVESTIGATING`: current evidence and missing confirmations
- `ARMED`: conditional entry, stop, targets, invalidation, validity
- `ACTIVE`: fill price/time, stop, targets, unrealized shadow state
- `REJECTED`/`BLOCKED`: exact reason codes and evidence
- terminal state: outcome and links to the immutable timeline

Every price card must display `SHADOW/PAPER ONLY`, market/session timestamp, capsule/version, and evidence freshness.

**Step 2: Implement without a second read model**

Join the decision and shadow event ledgers in the existing dashboard projection. Do not create a separate mutable dashboard database.

**Step 3: Verify and commit**

Run:

```bash
pytest -q tests/test_dashboard_projection_day_agent.py
ruff check trading_agent/dashboard_projection_day_agent.py tests/test_dashboard_projection_day_agent.py
basedpyright trading_agent/dashboard_projection_day_agent.py
```

Commit: `feat: show KR decision lifecycle on dashboard`

### Task 9: Automate idempotent KRX close finalization

**Files:**

- Create: `trading_agent/kr_day_close_service.py`
- Create: `run_kr_day_close_service.py`
- Create: `tests/test_kr_day_close_service.py`
- Modify or create: the launchd service definition following the repository's existing service pattern
- Reference: `run_kr_day_close_report.py`
- Reference: `trading_agent/kr_day_market_close_report.py`

**Step 1: Write close-service tests**

Cover pre-close no-op, post-close execution, restart idempotency, missing/corrupt ledger failure, holiday behavior, and one daily summary delivery. A failed finalization must remain visible in health state and must not mark the day complete.

**Step 2: Implement the finalizer**

Build the canonical finalization request from the current calendar plus decision/shadow stores, invoke existing close-report logic, write health state, and exit safely. Schedule it after the official close with a recovery run window; derive session dates from the exchange calendar rather than weekday assumptions.

**Step 3: Manual CLI verification**

Run:

```bash
python run_kr_day_close_service.py --help
python run_kr_day_close_service.py --config /definitely/missing.json
```

Then run a fixture-backed happy path against a temporary state root.

**Step 4: Automated verification and commit**

Run:

```bash
pytest -q tests/test_kr_day_close_service.py tests/test_kr_day_market_close_report.py tests/test_kr_day_close_report_cli.py
ruff check trading_agent/kr_day_close_service.py run_kr_day_close_service.py tests/test_kr_day_close_service.py
basedpyright trading_agent/kr_day_close_service.py run_kr_day_close_service.py
```

Commit: `feat: automate KR day close finalization`

### Task 10: Feed close evidence into the one-patch learning loop

**Files:**

- Create: `trading_agent/kr_day_loop_engineer.py`
- Modify: `trading_agent/day_agent_loop_engineer.py`
- Modify: `trading_agent/kr_day_learning_policy.py`
- Create: `tests/test_kr_day_loop_engineer.py`
- Modify: `tests/test_day_agent_loop_engineer.py`

**Step 1: Map observable failures to research stages**

Map counts and outcome evidence to the existing stage taxonomy: market, theme, catalyst, leader, flow, entry, exit, execution. Select only the lowest-confidence failing stage for the next challenger.

**Step 2: Test research discipline**

Assert:

- one challenger changes one policy field or one hypothesis only;
- champion code/config is never changed in place;
- failed hypotheses and rejected promotions remain in the audit history;
- challenger is eligible only for a future session;
- promotion requires the capsule's predeclared observation threshold, risk limits, no safety incidents, and multiple-testing-aware evidence;
- LLM output cannot grant order authority or bypass deterministic validation.

**Step 3: Implement the KR adapter to the existing loop engineer**

Prefer adapting close-report evidence to `day_agent_loop_engineer.py` instead of building a second generic research engine. The optional LLM role is limited to hypothesis wording and post-hoc critique; deterministic code owns feature calculation, risk, validation, and registration.

**Step 4: Verify and commit**

Run:

```bash
pytest -q tests/test_kr_day_loop_engineer.py tests/test_day_agent_loop_engineer.py tests/test_kr_day_learning_policy.py
ruff check trading_agent/kr_day_loop_engineer.py trading_agent/day_agent_loop_engineer.py trading_agent/kr_day_learning_policy.py
basedpyright trading_agent/kr_day_loop_engineer.py trading_agent/day_agent_loop_engineer.py trading_agent/kr_day_learning_policy.py
```

Commit: `feat: learn KR challenger from close evidence`

### Task 11: Add US semantic parity only after the KR gate passes

**Files:**

- Modify only the relevant existing US day-agent projection/delivery files after discovery
- Modify: US day-session tests identified during implementation
- Reference: `trading_agent/day_session_service.py`

**Step 1: Reuse lifecycle semantics**

Adopt the same investigating/armed/active/rejected/terminal user semantics and state-change delivery. Do not force KR-specific theme or price-grid logic into the US strategy.

**Step 2: Preserve execution boundary**

All US orders remain Alpaca Paper only. Add a regression test proving any non-paper URL is rejected before HTTP, then reconcile paper open orders and positions after an explicitly authorized real paper smoke test.

**Step 3: Verify**

Run the discovered targeted US tests, Ruff and basedpyright for changed files, then manual CLI help/bad-input/fixture happy-path checks.

**Step 4: Commit**

Commit: `feat: align US day-agent decision lifecycle`

### Task 12: Run operational acceptance and staged rollout

**Files:**

- Modify: relevant operator/runbook documentation discovered during implementation
- Create or update: fixture/replay test for the observed `005930` evidence, without storing credentials or raw auth data

**Step 1: Full targeted automated gate**

Run the union of all changed test modules, followed by:

```bash
ruff check <all changed Python files>
basedpyright <all changed Python files>
git diff --check
```

Do not substitute a repository-wide heavy empirical run.

**Step 2: Replay the observed watch**

Using sanitized evidence with high trading value, no publisher confirmation, one related symbol, and neutral volume confirmation, verify that:

- the item is visible as investigated;
- it is not presented as actionable;
- the missing evidence reasons appear in Hermes only if a prior user-visible state is invalidated;
- the dashboard retains the decision and timestamp.

**Step 3: Open-session read-only smoke**

On the next KRX session, verify one completed-bar service cycle end to end:

1. source health is current;
2. every discovered item obtains a disposition within the next 120-second cycle;
3. no duplicate Hermes message is emitted without a state change;
4. `ARMED` shows a conditional plan, not a fill;
5. an actual trigger, if naturally observed, transitions to shadow `ACTIVE` and then remains managed;
6. provider mutation count remains zero;
7. close finalization produces one daily summary and one or zero future challenger registrations.

Do not manufacture a signal or relax gates merely to obtain an `ACTIVE` event.

**Step 4: Promotion gate**

Keep the champion unchanged until the registered capsule's predeclared minimum observations and safety/risk evidence are satisfied. A good single day is insufficient for promotion.

**Step 5: Final commit**

```bash
git add <runbook and acceptance artifacts>
git commit -m "docs: record KR day-agent operating acceptance"
```

## 6. Acceptance Criteria

The implementation is complete only when all of the following are observed:

- Every current-session discovery becomes a reason-bearing recorded disposition within one subsequent 120-second service cycle, and an unchanged `INVESTIGATING` state resolves by the next completed bar.
- Every `ARMED`/`ACTIVE` user-facing item contains timestamp, entry or trigger, stop, targets, rationale, invalidation, validity, and paper/shadow labeling.
- Rejected candidates retain exact reason codes and feature evidence.
- A current active shadow position is managed even when no new opportunity is discovered.
- Same-bar stop and target collision resolves to stop.
- Ten unchanged cycles produce no duplicate Hermes push.
- Dashboard and Hermes describe the same canonical lifecycle.
- Close finalization survives restart and runs once per valid KRX session.
- The learning loop selects one failing stage, creates at most one future-session challenger patch, and never changes the current champion directly.
- KIS/LS mutation count is zero, no credential is printed or stored, and no non-paper Alpaca URL reaches HTTP.
- Targeted tests, Ruff, basedpyright, CLI help, bad-input, fixture happy path, and open-session read-only QA all pass.

## 7. Recommended Delivery Order

1. Tasks 1-4: make every watch explainable before touching notifications.
2. Tasks 5-6: connect explainable decisions to the existing shadow engine.
3. Tasks 7-8: make state changes visible without alert noise.
4. Tasks 9-10: close the daily research and challenger loop.
5. Task 11: port the proven lifecycle semantics to the US paper path.
6. Task 12: run operational acceptance and retain evidence.

The first releasable milestone is Tasks 1-8. It delivers the user's immediate experience: a detected stock no longer disappears after a watch alert; it becomes either a truthful conditional/active shadow plan or a visible, evidence-backed rejection. Tasks 9-10 complete the self-improving research loop, and Task 11 adds market parity without coupling the two markets' strategy logic.
