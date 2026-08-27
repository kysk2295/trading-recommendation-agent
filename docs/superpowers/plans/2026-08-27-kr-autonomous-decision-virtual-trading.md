# KR Autonomous Decision and Virtual Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect durable Chrome social evidence and fresh current-session KIS observations to autonomous KR recommendations or explicit no-trade decisions, then operate restart-safe internal virtual positions through terminal outcomes without any Korean real-order path.

**Architecture:** Keep the existing open-ended `AutonomousSupervisorRuntime` as the decision loop and add typed tools rather than a fixed browser-to-trade pipeline. Social normalization, KIS collection/projection, numerical trade planning, Critic admission, and virtual execution are deterministic boundaries backed by append-only stores; role delegation and tool order remain model-selected. Reuse KIS GET-only clients, KRX session/price-grid gates, completed-minute-bar contracts, and stop-first shadow semantics while keeping KIS/LS mutation and Alpaca calls at zero.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite append-only ledgers, existing autonomous supervisor/tool runtime, KIS GET-only adapters, launchd, pytest, Ruff, basedpyright.

---

## Product and safety boundary

- This plan implements only design subproject 12.2. Hermes/Dashboard projection, outcome memory, Loop Engineer bundles, and full operating rhythm remain 12.3.
- Browser tools stay read-only. KIS uses only the reviewed market-data contracts already enforced by `KisKrMarketClient`; no order, balance, account, or position-changing endpoint is introduced.
- A new recommendation requires the current KRX session, a latest completed minute bar, current-date evidence, bid/ask, spread, timestamp, entry, stop, targets, virtual quantity, rationale, counterevidence, and immutable lineage.
- A browser page price never creates a plan or fill. Only KIS receipts can provide numerical market truth.
- Virtual execution is internal research state. It must never be described as a real fill or profitability.
- Existing schema-v2/v3 Research Agent configs remain readable. Schema v4 is opt-in and required only for this vertical.
- Every new or modified Python file must remain at or below 250 pure LOC and pass the official no-excuse checker.

## File map

- `trading_agent/kr_social_signal_models.py`: deterministic normalized social-signal contract and identity.
- `trading_agent/kr_social_signal_store.py`: append-only private SQLite social-signal ledger.
- `trading_agent/kr_autonomous_market_models.py`: bounded KIS corroboration observation.
- `trading_agent/kr_autonomous_market_service.py`: current-session GET-only KIS collection and projection.
- `trading_agent/kr_autonomous_trade_models.py`: agent thesis, deterministic plan, Critic verdict, and recommendation/no-trade artifacts.
- `trading_agent/kr_autonomous_trade_planner.py`: KRX grid, risk budget, duplicate, freshness, and evidence admission.
- `trading_agent/kr_virtual_position_models.py`: ARMED/ACTIVE/terminal virtual-position events.
- `trading_agent/kr_virtual_position_store.py`: append-only position event ledger.
- `trading_agent/kr_virtual_position_engine.py`: future-bar fill, stop-first collision, target, expiry, close, and replay.
- `trading_agent/autonomous_kr_tools.py`: role-scoped typed tool callbacks for normalize, corroborate, plan, Critic, execute, and reconcile.
- `trading_agent/autonomous_supervisor_service.py`: add the KR tool bindings without changing the autonomous loop.
- `trading_agent/research_agent_service_config.py`: opt-in schema v4 KR paths; preserve v2/v3.
- `trading_agent/research_agent_service_builder.py`: construct v4 services and stores.
- `trading_agent/browser_research_agenda.py`: migrate the KR agenda goal/version to decision and virtual-position ownership while preserving the existing v1 episode.
- `run_research_agent_runtime.py`: provision, verify, status, and replace the v4 service.
- `tests/test_kr_social_signal.py`, `tests/test_kr_social_signal_store.py`: Task 1 contracts.
- `tests/test_kr_autonomous_market_service.py`, `tests/test_autonomous_kr_market_tool.py`: KIS/read-only boundary.
- `tests/test_kr_autonomous_trade_planner.py`, `tests/test_kr_autonomous_critic.py`: recommendation and no-trade admission.
- `tests/test_kr_virtual_position_engine.py`, `tests/test_kr_virtual_position_store.py`: internal execution/restart semantics.
- `tests/test_autonomous_kr_tools.py`, `tests/test_kr_autonomous_vertical.py`: free role/tool collaboration and end-to-end flow.
- `docs/checkpoints/2026-08-27-kr-autonomous-decision-virtual-trading-ko.md`: exact-SHA deployment and QA evidence.

### Task 1: Normalize social evidence into immutable KR signals

**Files:**
- Create: `trading_agent/kr_social_signal_models.py`
- Create: `trading_agent/kr_social_signal_store.py`
- Create: `tests/test_kr_social_signal.py`
- Create: `tests/test_kr_social_signal_store.py`

- [ ] **Step 1: Write failing model tests**

Create evidence fixtures with two copied posts sharing `content_sha256` and one independent source. Assert normalization deduplicates repost clusters, counts independent-source clusters, derives the earliest publication and observation times, and binds the exact source payload hashes. Add failures for an invalid KR symbol, unknown evidence ID, duplicate or unsorted IDs, empty claim/theme, future publication, and a forged `signal_id`.

```python
def test_normalizer_counts_clusters_not_posts() -> None:
    signal = normalize_kr_social_signal(
        KrSocialSignalRequest(
            task_id="a" * 64,
            symbol="005930",
            theme="온디바이스 AI",
            claim_summary="독립 출처들이 현재 세션 전에 같은 촉매를 언급했다.",
            evidence_ids=tuple(item.evidence_id for item in EVIDENCE),
            normalized_at=NOW,
        ),
        EVIDENCE,
    )
    assert signal.post_count == 3
    assert signal.repost_cluster_count == 2
    assert signal.independent_source_count == 2
    assert signal.verification_state is KrSocialVerificationState.MULTI_SOURCE_CORROBORATED
```

- [ ] **Step 2: Run Task 1 model tests and confirm RED**

Run: `uv run pytest -q tests/test_kr_social_signal.py`

Expected: collection fails because `trading_agent.kr_social_signal_models` does not exist.

- [ ] **Step 3: Implement the normalized signal contract**

Define these exact public types. `normalize_kr_social_signal` must validate every selected `BrowserSocialEvidence`, sort by `(first_observed_at, evidence_id)`, and compute all derived fields and the content-addressed identity itself.

```python
class KrSocialVerificationState(StrEnum):
    UNVERIFIED_SOCIAL = "unverified_social"
    MULTI_SOURCE_CORROBORATED = "multi_source_corroborated"


class KrSocialSignalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    task_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    symbol: str
    theme: str = Field(min_length=1, max_length=160)
    claim_summary: str = Field(min_length=8, max_length=1_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    normalized_at: AwareDatetime


class KrSocialSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    signal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    symbol: str
    theme: str
    claim_summary: str
    evidence_ids: tuple[str, ...]
    source_payload_sha256s: tuple[str, ...]
    repost_cluster_ids: tuple[str, ...]
    independent_source_cluster_ids: tuple[str, ...]
    post_count: int = Field(ge=1, le=64)
    repost_cluster_count: int = Field(ge=1, le=64)
    independent_source_count: int = Field(ge=1, le=64)
    verification_state: KrSocialVerificationState
    earliest_published_at: AwareDatetime | None
    first_observed_at: AwareDatetime
    normalized_at: AwareDatetime
```

Identity material is compact canonical JSON of every field except `signal_id`; validate `is_kr_instrument_symbol_v2(symbol)`, sorted unique tuple fields, `published_at <= first_observed_at <= normalized_at`, exact counts, and `MULTI_SOURCE_CORROBORATED` only when `independent_source_count >= 2`.

- [ ] **Step 4: Run model tests and confirm GREEN**

Run: `uv run pytest -q tests/test_kr_social_signal.py`

Expected: all Task 1 model tests pass.

- [ ] **Step 5: Write failing append-only store tests**

Cover first append, exact replay returning `False`, divergent identity conflict, deterministic ordering, mode `600`, parent mode `700`, symlink/hardlink/wrong-mode rejection, query-only reads, schema tamper, update/delete triggers, and concurrent same-signal append.

```python
def test_store_is_append_only_and_replay_safe(tmp_path: Path) -> None:
    store = KrSocialSignalStore(tmp_path / "signals.sqlite3")
    assert store.append(SIGNAL)
    assert not store.append(SIGNAL)
    assert store.get(SIGNAL.signal_id) == SIGNAL
    assert store.signals_for_task(SIGNAL.task_id) == (SIGNAL,)
```

- [ ] **Step 6: Implement the private SQLite store**

Create schema version 1 with `signal_id` primary key, indexed `task_id/symbol/normalized_at`, `payload_sha256`, canonical `payload_json`, and update/delete rejection triggers. Use `absolute_private_path`, `open_private_parent`, `require_private_directory_query_only`, `sqlite_read_only_uri`, and the same transaction/idempotency pattern as `BrowserSocialEvidenceStore`.

```sql
CREATE TABLE kr_social_signals (
  signal_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  normalized_at TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX kr_social_signals_by_task
ON kr_social_signals(task_id, normalized_at, signal_id);
```

- [ ] **Step 7: Verify and commit Task 1**

Run:

```bash
uv run pytest -q tests/test_kr_social_signal.py tests/test_kr_social_signal_store.py
uv run ruff format --check trading_agent/kr_social_signal_models.py trading_agent/kr_social_signal_store.py tests/test_kr_social_signal.py tests/test_kr_social_signal_store.py
uv run ruff check trading_agent/kr_social_signal_models.py trading_agent/kr_social_signal_store.py tests/test_kr_social_signal.py tests/test_kr_social_signal_store.py
uv run basedpyright trading_agent/kr_social_signal_models.py trading_agent/kr_social_signal_store.py tests/test_kr_social_signal.py tests/test_kr_social_signal_store.py
uv run python /Users/goyunseo/.codex/plugins/cache/sisyphuslabs/omo/4.19.4/skills/programming/scripts/python/check-no-excuse-rules.py trading_agent/kr_social_signal_models.py trading_agent/kr_social_signal_store.py tests/test_kr_social_signal.py tests/test_kr_social_signal_store.py
```

Expected: tests and all static gates pass; every file is at most 250 pure LOC.

Commit: `feat: normalize KR browser social signals`

### Task 2: Add fresh current-session KIS corroboration

**Files:**
- Create: `trading_agent/kr_autonomous_market_models.py`
- Create: `trading_agent/kr_autonomous_market_service.py`
- Create: `tests/test_kr_autonomous_market_service.py`
- Create: `tests/test_autonomous_kr_market_tool.py`
- Modify: `trading_agent/research_agent_service_config.py`
- Modify: `tests/test_research_agent_service_config.py`

- [ ] **Step 1: Write RED tests for schema v4 and bounded market truth**

Add schema-v4 config tests requiring absolute `kr_market_receipt_root` and `kr_social_signal_database`; v2/v3 payloads must remain unchanged. With existing KIS fixtures, assert one corroboration includes the signal ID, symbol, KRX session date, latest completed bar, projected bid/ask/spread, trading value, receipt evidence IDs, observed time, and validity deadline. Reject closed session, stale/future/date-mismatched receipts, missing quote/spread, symbol mismatch, and a market response that predates the social signal.

```python
assert result.latest_completed_bar.end_at <= result.observed_at
assert result.market_snapshot.bid_price is not None
assert result.market_snapshot.ask_price is not None
assert result.spread_bps <= Decimal("20")
assert result.social_first_observed_at <= result.market_response_at
```

- [ ] **Step 2: Implement schema v4 without changing v2/v3**

Extend `ResearchAgentServiceConfig.schema_version` to `Literal[2, 3, 4]`. Require browser binding for v3/v4 and require both KR paths only for v4. Reject KR paths in v2/v3 so an old deployment cannot silently acquire new authority.

```python
kr_market_receipt_root: Path | None = None
kr_social_signal_database: Path | None = None
```

- [ ] **Step 3: Implement GET-only collection and projection**

`collect_and_project_kr_corroboration(signal, config, now)` must select the exact current KRX calendar snapshot, load KIS credentials only through existing mode-600 loaders, use `KisKrMarketClient` and `collect_kis_kr_market_receipts`, append receipts under `<root>/<symbol>.sqlite3`, then project with existing parsing, `project_kis_kr_market_snapshot`, `assess_kr_shadow_entry`, and `KrCompletedMinuteBar`. Return only bounded typed fields and digests; never return raw provider payload or authentication data.

- [ ] **Step 4: Prove provider mutation is impossible**

Use a recording HTTP transport. Assert only the reviewed price/status, order-book, and minute-bar GET contracts are requested. Feed account/order/balance paths and redirects to the client and prove rejection before the transport records a request. Assert exception strings, tool observations, and receipts contain no headers, keys, tokens, account IDs, or raw auth response.

- [ ] **Step 5: Verify and commit Task 2**

Run the new tests plus `tests/test_kis_kr_market_client.py`, `tests/test_kis_kr_market_collection.py`, `tests/test_kr_intraday_market_gate.py`, and config tests; run Ruff, basedpyright, no-excuse, and `git diff --check`.

Commit: `feat: add current-session KR market corroboration`

### Task 3: Produce deterministic recommendation or no-trade artifacts

**Files:**
- Create: `trading_agent/kr_autonomous_trade_models.py`
- Create: `trading_agent/kr_autonomous_trade_planner.py`
- Create: `trading_agent/kr_autonomous_trade_store.py`
- Create: `tests/test_kr_autonomous_trade_planner.py`
- Create: `tests/test_kr_autonomous_critic.py`
- Create: `tests/test_kr_autonomous_trade_store.py`

- [ ] **Step 1: Write RED thesis, planner, and Critic tests**

Define `RECOMMEND`, `NO_TRADE`, and `REJECTED` outcomes. A thesis supplies symbol, theme, hypothesis, counterevidence, setup kind, social signal ID, market corroboration ID, and exact evidence refs; it never supplies entry, stop, targets, or quantity. Test KRX grid rounding, current ask entry, completed-bar-derived stop, ordered 1R/2R targets, verified and unverified risk budgets, max virtual notional, duplicate symbol/theme rejection, stale market, missing spread, post-reaction social discovery, contradictory evidence, and explicit no-trade wake.

```python
class KrAutonomousSetupKind(StrEnum):
    MOMENTUM_RECLAIM = "momentum_reclaim"
    BREAKOUT_CONTINUATION = "breakout_continuation"


class KrAutonomousTradeThesis(BaseModel):
    thesis_id: str
    task_id: str
    symbol: str
    theme: str
    hypothesis: str
    counterevidence: tuple[str, ...]
    setup_kind: KrAutonomousSetupKind
    social_signal_id: str
    market_corroboration_id: str
    evidence_refs: tuple[str, ...]
    submitted_at: AwareDatetime
```

- [ ] **Step 2: Implement deterministic levels and virtual sizing**

Use `round_kr_equity_price_up` for entry/targets and `round_kr_equity_price_down` for stop. Entry is current validated ask. Stop is the lower setup boundary from completed bars, never model prose. Targets are one and two risk units above entry. Quantity is the smaller integer of risk-budget/risk-per-share and max-notional/entry; reject zero quantity.

```python
VERIFIED_RISK_KRW = Decimal("25000")
UNVERIFIED_RISK_KRW = Decimal("5000")
VERIFIED_MAX_NOTIONAL_KRW = Decimal("1000000")
UNVERIFIED_MAX_NOTIONAL_KRW = Decimal("300000")
```

- [ ] **Step 3: Implement deterministic Critic admission**

Critic returns a content-addressed `KrAutonomousCriticVerdict` with `APPROVED`, `MORE_RESEARCH`, or `REJECTED`. Approval requires exact social/market/task lineage, causal publication before market response, independent cluster counts matching the signal, current completed bar and spread, valid price grid and quantity, no open duplicate symbol/theme, and rationale/counterevidence consistency. Critic never edits levels.

- [ ] **Step 4: Persist recommendation and no-trade history**

Create one append-only event store keyed by event ID with `previous_event_id`. Exact replay returns `False`; divergent replay and broken chains fail closed. A recommendation event contains timestamp, entry, stop, targets, quantity, rationale, counterevidence, verification state, Critic verdict ID, validity, `virtual_only=True`, and `trading_authority=False`. A no-trade event contains reason codes and next wake but no price or quantity.

- [ ] **Step 5: Verify and commit Task 3**

Run all new tests plus price-grid, candidate-admission, completed-bar, and market-gate tests; run static gates and diff check.

Commit: `feat: plan evidence-bound KR virtual recommendations`

### Task 4: Expose role-scoped autonomous KR tools

**Files:**
- Create: `trading_agent/autonomous_kr_tools.py`
- Create: `tests/test_autonomous_kr_tools.py`
- Modify: `trading_agent/autonomous_supervisor_service.py`
- Modify: `tests/test_autonomous_supervisor_service.py`

- [ ] **Step 1: Write RED authority and arbitrary-order tests**

Assert exact signatures and roles:

```text
social.signal.normalize(claim_summary,evidence_ids_json,symbol,theme)
kr.market.corroborate(signal_id,symbol)
kr.trade.plan(thesis_json)
critic.request(plan_id)
```

Market Observer/Research can normalize; Opportunity/Research can corroborate; Trading can plan; Critic alone can approve; no role can call KIS order/account paths or arbitrary files. Demonstrate normalize can occur before or after extra browser reads, corroboration can repeat after a wake, and the Supervisor does not require all tools or a fixed number of pages.

- [ ] **Step 2: Implement typed bindings**

Use existing `AutonomousToolBinding`, `AutonomousToolArguments`, trusted task context, bounded canonical JSON, and partial-bound private paths. Every callback validates `context.task_id`, task market `kr_equities`, root lineage, allowed role, and exact argument names before storage or network access. Return only artifact IDs, status, bounded reason codes, next wake, and numerical market/plan fields.

- [ ] **Step 3: Add the bindings without changing foundation/browser behavior**

Extend `build_foundation_tool_runtime` with an optional `KrAutonomousToolServices`. Preserve existing foundation-only and browser-only tuples byte-for-byte when the service is absent. Add only the exact new worker module to `worker_modules`.

- [ ] **Step 4: Verify and commit Task 4**

Run autonomous tool/wire/process/recovery/security tests, new arbitrary-order tests, Ruff, basedpyright, no-excuse, and diff check.

Commit: `feat: expose autonomous KR decision tools`

### Task 5: Add restart-safe internal virtual positions

**Files:**
- Create: `trading_agent/kr_virtual_position_models.py`
- Create: `trading_agent/kr_virtual_position_store.py`
- Create: `trading_agent/kr_virtual_position_engine.py`
- Create: `tests/test_kr_virtual_position_engine.py`
- Create: `tests/test_kr_virtual_position_store.py`
- Modify: `trading_agent/autonomous_kr_tools.py`
- Modify: `tests/test_autonomous_kr_tools.py`

- [ ] **Step 1: Write RED state-machine and replay tests**

Cover `ARMED`, `ACTIVE`, `STOPPED`, `TARGETED`, `EXPIRED`, and `CENSORED`. A fill may use only a completed bar strictly after recommendation creation. If entry, stop, and target are reachable in the same completed bar, resolve to stop. Cover pending entry, expiry, one-target terminal policy, 15:30 time exit, bar gap censored, process reconstruction, exact replay, divergent replay, and multiple tasks attempting the same symbol/theme.

- [ ] **Step 2: Implement event models and append-only store**

Each event contains `position_id`, `recommendation_id`, `task_id`, symbol/theme, sequence, previous event ID, state/reason, attempted and accepted completed-bar cursors, entry/stop/targets/quantity, fill or exit price/time when applicable, evidence refs, `virtual_only=True`, and `trading_authority=False`. Identity is canonical SHA-256. The store enforces one exact chain per position and a query for open positions.

- [ ] **Step 3: Implement conservative virtual execution**

`advance_kr_virtual_position(recommendation, previous, bars, now)` first validates a continuous current-session completed-bar chain. For ARMED, a future bar that reaches entry activates; if that same bar also reaches stop, emit STOPPED. For ACTIVE, check stop before every target. At the session close use the completed 15:30 bar close; missing bars produce CENSORED, never a fabricated price.

- [ ] **Step 4: Add execute and reconcile tools**

Add exact signatures:

```text
kr.virtual.execute(recommendation_id)
kr.position.reconcile(position_id)
```

Trading may execute only an approved, current, virtual-only recommendation. Position may reconcile only an existing position in the same task. Startup reconciliation runs open positions before new recommendation work.

- [ ] **Step 5: Verify and commit Task 5**

Run new tests plus existing KR shadow entry/exit/capsule-shadow tests, static gates, and diff check.

Commit: `feat: operate restart-safe KR virtual positions`

### Task 6: Wire schema v4 into the persistent research service

**Files:**
- Modify: `trading_agent/research_agent_service_builder.py`
- Modify: `trading_agent/research_agent_service_config.py`
- Modify: `trading_agent/autonomous_supervisor_status.py`
- Modify: `trading_agent/browser_research_agenda.py`
- Modify: `run_research_agent_runtime.py`
- Create: `tests/test_research_agent_service_kr_autonomous_runtime.py`
- Modify: `tests/test_browser_research_agenda.py`
- Modify: `tests/test_research_agent_service_config.py`

- [ ] **Step 1: Write RED migration, restart, and status tests**

Provision v4 beside the active v13 deployment. Verify old v1 browser agenda evidence still loads, one lineage-linked v2 KR decision episode is created without duplicating the v1 task, open virtual positions reconcile before fresh browsing, exact restart creates no duplicate tool call/event, and status reports task, social-signal, recommendation/no-trade, and open/terminal virtual counts with `broker_mutation=0` and `trading_mutation=0`.

- [ ] **Step 2: Build v4 services and agenda migration**

Add private stores beneath `output_root/autonomous-supervisor/kr-v1`. Update the agenda goal/version to include social normalization, KIS corroboration, recommendation/no-trade, and virtual position ownership. Preserve the old goal digest parser; create a successor only once and include the predecessor task ID in root evidence. Do not replace `AutonomousSupervisorRuntime` or encode a fixed tool sequence in `current_plan`.

- [ ] **Step 3: Provision and verify a candidate LaunchAgent**

`provision` writes mode-600 canonical v4 config and a versioned plist. `verify` must validate every path and the exact browser/KIS binding before launchctl. `replace` keeps candidate health isolated, preserves the v13 rollback artifacts, and accepts only a fresh matching v4 health report.

- [ ] **Step 4: Verify and commit Task 6**

Run service config/builder/health/replace/runtime/recovery tests, the autonomous KR suites, static gates, CLI help, missing config, fixture tick, and diff check.

Commit: `feat: run KR autonomous virtual trading service`

### Task 7: Prove the complete 12.2 vertical and deploy

**Files:**
- Create: `tests/test_kr_autonomous_vertical.py`
- Create: `docs/checkpoints/2026-08-27-kr-autonomous-decision-virtual-trading-ko.md`

- [ ] **Step 1: Add fixture end-to-end scenarios**

Use browser evidence and KIS fixtures to prove:

```text
social evidence -> normalized clusters -> current KIS corroboration
-> autonomous delegation/tool choices -> approved recommendation or explicit no-trade
-> future completed-bar virtual fill -> stop-first/target/close outcome
-> restart reconciliation and exact replay
```

Add negative cases for copied-only posts, post-reaction discovery, stale KIS, missing spread, closed session, model failure, Critic rejection, duplicate theme/symbol, and bar gaps. Assert all terminal recommendations retain timestamp, entry, stop, targets, quantity, rationale, counterevidence, immutable history, and original browser/KIS refs.

- [ ] **Step 2: Run full changed-surface verification**

Run all Task 1-6 tests plus autonomous supervisor, browser, KIS KR, KR price/grid/gate, and existing KR shadow suites. Run Ruff format/check, basedpyright, official no-excuse, `git diff --check`, and the whole test suite. Baseline any unrelated whole-suite failure on the pre-change SHA in a clean temporary worktree.

- [ ] **Step 3: Perform CLI and process manual QA**

Run CLI help, one missing config, fixture happy tick, malformed social/market inputs, and candidate status. With real current-session KIS credentials loaded only from the approved mode-600 file, observe one natural public-browser signal or explicit no-trade; do not manufacture a setup. Verify KIS/LS mutation count 0, Alpaca call count 0, secrets/redacted data 0, and no Korean real-order module/path.

- [ ] **Step 4: Replace and soak the v4 LaunchAgent**

Activate v4 only after exact-SHA gates. Observe Gateway and Research PID replacement, fresh health, one durable task, no duplicate signal/recommendation/position on restart, open-position reconciliation before new research, and continued operation with the Codex session closed. Keep v13 as a recoverable rollback candidate.

- [ ] **Step 5: Write the checkpoint and commit**

Record exact full SHA, config/plist digests, tests, real Chrome/KIS observations, recommendation or no-trade, virtual outcome if naturally available, service PIDs/runs, broker/trading mutation 0, secrets 0, and all residual risks. Never claim profitability from fixtures, replay, or virtual results.

Commit: `docs: checkpoint KR autonomous virtual trading`

## Final verification gate

Before calling 12.2 complete, all of the following must be true:

1. The autonomous loop can choose tools and delegates in different valid orders; no fixed browser/KIS/Critic pipeline is encoded.
2. Real browser evidence and current-session KIS truth produce an immutable recommendation or explicit no-trade.
3. Every recommendation has timestamp, entry, stop, targets, quantity, rationale, counterevidence, verification state, and full browser/KIS/Critic lineage.
4. Only future completed bars may create virtual fills; same-bar stop/target collision resolves to stop.
5. Process restart reconstructs the same open position and does not duplicate events.
6. KIS and LS mutation calls are zero, Alpaca calls are zero, and no KR real-order path exists.
7. No secret, account identifier, raw auth response, full HTML, or unbounded social text appears in task history, logs, receipts, test artifacts, or the checkpoint.
8. The exact deployment SHA passes targeted tests, Ruff, basedpyright, no-excuse, CLI manual QA, real-surface QA, and a fresh launchd status check.

## Self-review record

- Spec coverage: Task 1 covers normalization, repost clustering, independent sources, and chronology; Task 2 covers current-session KIS; Tasks 3-4 cover Opportunity/Trading/Critic autonomy and deterministic planning; Task 5 covers virtual execution and restart reconciliation; Tasks 6-7 cover durable service integration and operational verification.
- Scope separation: Hermes, Dashboard, outcome memory, and Loop Engineer bundles are explicitly deferred to 12.3.
- Type consistency: `signal_id` flows Task 1→2, `market_corroboration_id` flows Task 2→3, `recommendation_id` flows Task 3→5, and every store remains task/root-evidence bound.
- Placeholder scan: the plan contains no deferred implementation markers; every task names exact files, public contracts, RED/GREEN commands, safety assertions, and commit boundary.
