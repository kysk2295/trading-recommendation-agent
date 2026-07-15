# Lane Control-Plane Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable append-only lane registry and wire immutable lane/experiment/risk contracts into the existing intraday research and Paper smoke surfaces without changing broker authority.

**Architecture:** Keep every execution lane's existing SQLite ledger and Writer isolated. Add a separate query-only-readable registry for manifests, account bindings, experiment scopes, and finalized daily snapshots; then project current daily research and smoke risk configuration through the registered `intraday_momentum` contracts.

**Tech Stack:** Python 3.12, frozen Pydantic v2 models, SQLite append-only triggers, `fcntl` Writer leases, argparse CLI, pytest, Ruff, basedpyright.

---

### Task 1: Define Lane Policies And Conservative Risk Contracts

**Files:**
- Create: `trading_agent/lane_policy_models.py`
- Create: `trading_agent/lane_defaults.py`
- Create: `tests/test_lane_policy_models.py`

- [x] **Step 1: Write failing tests for the closed lane set and distinct state machines**

```python
def test_default_lanes_use_distinct_execution_state_machines() -> None:
    policies = (INTRADAY_EXECUTION_POLICY, SWING_EXECUTION_POLICY, MARKET_REGIME_EXECUTION_POLICY)
    assert tuple(policy.state_machine for policy in policies) == (
        "intraday_flat_by_close_v1",
        "swing_shadow_multisession_v1",
        "regime_signal_publish_v1",
    )
    assert len({policy.state_machine for policy in policies}) == 3
```

- [x] **Step 2: Write failing tests that pin the current intraday smoke limits**

```python
def test_intraday_pilot_risk_contract_does_not_expand_smoke_limits() -> None:
    risk = INTRADAY_PILOT_RISK_CONTRACT
    assert risk.max_notional_dollars == Decimal("100")
    assert risk.max_planned_risk_dollars == Decimal("10")
    assert risk.max_open_positions == 1
    assert risk.daily_loss_limit_dollars == Decimal("30")
    assert risk.per_side_cost_bps == Decimal("20")
```

- [x] **Step 3: Run the tests and verify RED**

Run: `uv run pytest -q tests/test_lane_policy_models.py`

Expected: import failure because the lane modules do not exist.

- [x] **Step 4: Implement the typed policy union and risk validator**

Define `LaneId` as a `StrEnum`, `LaneOrderAuthority` as `alpaca_paper | shadow_only | none`, three frozen policy models with literal state-machine IDs, and a frozen `LaneRiskContract`. Validate finite non-negative Decimal limits; require all exposure fields to be zero for `none`; require positive bounded limits for `alpaca_paper` and `shadow_only`.

The intraday policy must encode entry cutoff 30 and flatten 5 minutes before close. Swing must carry explicit multi-session states. Regime signal-only must expose no order states.

- [x] **Step 5: Add immutable defaults and the PaperRiskConfig adapter**

```python
def intraday_pilot_paper_risk_config() -> PaperRiskConfig:
    config = PaperRiskConfig(
        max_risk_dollars=float(INTRADAY_PILOT_RISK_CONTRACT.max_planned_risk_dollars),
        max_notional_dollars=float(INTRADAY_PILOT_RISK_CONTRACT.max_notional_dollars),
        max_open_positions=INTRADAY_PILOT_RISK_CONTRACT.max_open_positions,
        daily_loss_limit_dollars=float(INTRADAY_PILOT_RISK_CONTRACT.daily_loss_limit_dollars),
        per_side_cost_bps=float(INTRADAY_PILOT_RISK_CONTRACT.per_side_cost_bps),
    )
    config.assert_within_hard_limits()
    return config
```

- [x] **Step 6: Run focused tests**

Run: `uv run pytest -q tests/test_lane_policy_models.py`

Expected: all tests pass.

### Task 2: Define Immutable Manifest, Binding, Scope, And Snapshot Contracts

**Files:**
- Create: `trading_agent/lane_contract_models.py`
- Create: `trading_agent/lane_contract_keys.py`
- Modify: `trading_agent/lane_defaults.py`
- Create: `tests/test_lane_contract_models.py`

- [x] **Step 1: Write failing tests for manifest and account-binding invariants**

Cover deterministic manifest keys, exact Paper base URL, aware timestamps, 64-character lowercase hex fingerprints, and rejection of account bindings for shadow/signal manifests.

```python
with pytest.raises(InvalidLaneContractError):
    lane_account_binding(MARKET_REGIME_MANIFEST, account_fingerprint, ledger_fingerprint, NOW)
```

- [x] **Step 2: Write failing tests for cross-lane anti-mixing rules**

```python
def test_cross_lane_scope_requires_a_new_preregistered_hypothesis() -> None:
    with pytest.raises(ValidationError):
        ExperimentScope(
            scope_kind="cross_lane_hypothesis",
            hypothesis_id="H-MOM-ORB-001",
            primary_lane=LaneId.INTRADAY_MOMENTUM,
            lanes=(LaneId.INTRADAY_MOMENTUM, LaneId.MARKET_REGIME),
            source_hypothesis_ids=("H-MOM-ORB-001", "H-REGIME-VIX-001"),
            combination_rule="Apply the pre-open VIX state to every ORB candidate.",
            registered_at=NOW,
        )
```

- [x] **Step 3: Write failing tests for finalized snapshots**

Require intraday zero orders/positions/open risk, signal-only zero broker fields, and allocation eligibility only with complete data, no incidents, and a champion.

- [x] **Step 4: Run tests and verify RED**

Run: `uv run pytest -q tests/test_lane_contract_models.py`

Expected: import failure.

- [x] **Step 5: Implement frozen models and canonical SHA-256 keys**

Use `ConfigDict(frozen=True, extra="forbid")`, canonical `model_dump(mode="json")`, sorted-key compact JSON, and lowercase SHA-256. Define `manifest_key`, `binding_key`, `experiment_scope_key`, and `lane_daily_snapshot_key` without accepting caller-provided keys.

- [x] **Step 6: Implement registration-time checks**

Add `require_scope_registered_before_session(scope, session_date)` using the local NYSE regular-session open. Reject unsupported calendar dates and registration at or after the open.

- [x] **Step 7: Run focused tests**

Run: `uv run pytest -q tests/test_lane_contract_models.py tests/test_lane_policy_models.py`

Expected: all tests pass.

### Task 3: Add The Append-Only Lane Registry

**Files:**
- Create: `trading_agent/lane_registry_schema.py`
- Create: `trading_agent/lane_registry_store.py`
- Create: `tests/test_lane_registry_store.py`

- [x] **Step 1: Write failing schema and lease tests**

Verify schema version 1, all four tables, update/delete rejection triggers, file mode `0600`, and a second non-blocking Writer lease failure before mutation.

- [x] **Step 2: Write failing idempotency and conflict tests**

Exact replay returns `False`. A lane/version, hypothesis ID, lane binding, or lane/date snapshot identity with different canonical JSON raises `LaneRegistryConflictError`.

- [x] **Step 3: Write failing isolation tests**

Registering the same account fingerprint or execution-ledger fingerprint for two lanes must fail. Binding `swing_momentum` or signal-only `market_regime` under their default manifests must fail before insert.

- [x] **Step 4: Run tests and verify RED**

Run: `uv run pytest -q tests/test_lane_registry_store.py`

Expected: import failure.

- [x] **Step 5: Implement the schema and read-only reader**

Create `lane_manifests`, `lane_account_bindings`, `experiment_scopes`, and `lane_daily_snapshots` with canonical JSON payloads, identity columns, foreign keys, uniqueness constraints, and append-only triggers. Reader connections must use SQLite URI `mode=ro`, `PRAGMA query_only = ON`, and exact schema-version validation.

- [x] **Step 6: Implement the Writer and source validation**

The Writer must check that bindings reference a currently registered broker-authorized manifest for that lane, and snapshots reference registered manifest/scope keys whose lane sets contain the snapshot lane. Persist canonical content before returning.

- [x] **Step 7: Run focused registry tests**

Run: `uv run pytest -q tests/test_lane_registry_store.py tests/test_lane_contract_models.py`

Expected: all tests pass.

- [x] **Step 8: Commit and push the durable registry checkpoint**

```bash
git add trading_agent/lane_* tests/test_lane_*
git commit -m "feat: add append-only lane registry"
git push origin feature/paper-account-activities
```

### Task 4: Add A Redacted Local Bootstrap CLI

**Files:**
- Create: `trading_agent/lane_bootstrap.py`
- Create: `run_lane_control_plane_bootstrap.py`
- Create: `tests/test_lane_control_plane_bootstrap_cli.py`
- Modify: `pyproject.toml`

- [x] **Step 1: Write failing CLI tests**

Cover `--help`, registry-only bootstrap, idempotent replay, invalid execution database, and optional intraday binding from an initialized existing execution ledger. Assert reports contain lane names and counts but not the account fingerprint, execution path, raw binding/manifest keys, account IDs, or credentials.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_lane_control_plane_bootstrap_cli.py`

Expected: script missing.

- [x] **Step 3: Implement bootstrap service and CLI**

The service registers the three default manifests and four current intraday single-lane experiment scopes. When `--intraday-execution-database` is supplied, require current schema and an existing local account fingerprint, hash the resolved path locally as the ledger fingerprint, and bind it without any network or credential loading.

- [x] **Step 4: Add the CLI to basedpyright include and run QA**

Run:

```bash
./run_lane_control_plane_bootstrap.py --help
uv run pytest -q tests/test_lane_control_plane_bootstrap_cli.py
```

Expected: help exits 0; all tests pass; external Alpaca calls remain zero.

### Task 5: Scope The Daily Research Ledger By Lane

**Files:**
- Modify: `trading_agent/daily_research_models.py`
- Modify: `trading_agent/daily_research_contract.py`
- Modify: `trading_agent/daily_research_ledger.py`
- Modify: `tests/test_daily_research_record_cli.py`
- Modify: `tests/test_daily_research_evaluator_version.py`
- Create: `tests/test_daily_research_lane_scope.py`

- [x] **Step 1: Write failing schema-v2 and scope-isolation tests**

Assert new rows contain `schema_version=2`, the intraday single-lane scope, and a deterministic scope key. Seed a prior row with the same strategy/evaluator/data fields but a different scope and prove it does not increase cumulative days/trades.

- [x] **Step 2: Write a failing schema-v1 projection test**

Read an existing v1 JSONL row with no scope and assert the in-memory record projects to the historical intraday scope without rewriting the file.

- [x] **Step 3: Run tests and verify RED**

Run: `uv run pytest -q tests/test_daily_research_lane_scope.py tests/test_daily_research_record_cli.py`

Expected: missing scope assertions fail.

- [x] **Step 4: Implement v2 records and backward projection**

Add `experiment_scope` and `experiment_scope_key`, include the key in `record_id`, filter prior rows by exact scope key, and include lane/scope in the Korean summary. Parse v1 dictionaries by injecting the historical intraday scope in memory before Pydantic validation.

- [x] **Step 5: Enforce pre-registration before the market session**

Call `require_scope_registered_before_session` during record construction. A cross-lane scope registered after session open must fail closed before writing JSON or summary files.

- [x] **Step 6: Run daily-ledger regression tests**

Run: `uv run pytest -q tests/test_daily_research_lane_scope.py tests/test_daily_research_record_cli.py tests/test_daily_research_evaluator_version.py tests/test_candidate_input_daily_gate.py`

Expected: all tests pass.

### Task 6: Wire Smoke Risk To The Intraday Lane Contract

**Files:**
- Modify: `run_alpaca_paper_entry_smoke.py`
- Modify: `run_alpaca_paper_safety_mutation_smoke.py`
- Modify: `tests/test_alpaca_paper_entry_smoke.py`
- Modify: `tests/test_alpaca_paper_safety_mutation_smoke.py`

- [x] **Step 1: Add failing identity tests**

Assert both CLIs use `intraday_pilot_paper_risk_config()` and still pass exact values 100/10/1/30/20 to the operating session.

- [x] **Step 2: Remove duplicate local risk constants**

Import one immutable config from `lane_defaults`; do not change public CLI arguments or mutation behavior.

- [x] **Step 3: Run Paper smoke regressions**

Run: `uv run pytest -q tests/test_alpaca_paper_entry_smoke.py tests/test_alpaca_paper_safety_mutation_smoke.py tests/test_paper_safety_mutation_scope.py`

Expected: all tests pass with no broker adapter opened in blocked/fake cases.

### Task 7: Verify, Document, And Checkpoint

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Create: `docs/checkpoints/2026-07-15-lane-control-plane-contracts-ko.md`
- Modify: `docs/superpowers/plans/2026-07-15-lane-control-plane-contracts.md`

- [x] **Step 1: Document exact implemented boundaries**

State that intraday is the only broker-authorized manifest, swing is shadow-only, regime is signal-only, Portfolio Manager is absent, existing execution schema remains v9, account data is fingerprint-only, and external Alpaca POST/DELETE count is zero.

- [x] **Step 2: Run focused and full verification**

```bash
uv run pytest -q tests/test_lane_policy_models.py tests/test_lane_contract_models.py tests/test_lane_registry_store.py tests/test_lane_control_plane_bootstrap_cli.py tests/test_daily_research_lane_scope.py
uv run pytest -q
uv run ruff check .
uv run basedpyright
uv run ruff format --check $(git diff --name-only -- '*.py') $(git ls-files --others --exclude-standard -- '*.py')
git diff --check
```

Expected: zero failures, zero Ruff findings, and zero basedpyright errors/warnings.

- [x] **Step 3: Run manual CLI QA**

Run help, malformed path, registry-only bootstrap, and a temporary initialized execution-ledger binding. Inspect the report for redaction. Do not load Alpaca credentials and do not call external APIs.

- [x] **Step 4: Commit, push, and verify origin alignment**

```bash
git add README.md CODEX_START_HERE.md docs/checkpoints/2026-07-15-lane-control-plane-contracts-ko.md docs/superpowers/plans/2026-07-15-lane-control-plane-contracts.md pyproject.toml run_alpaca_paper_entry_smoke.py run_alpaca_paper_safety_mutation_smoke.py run_lane_control_plane_bootstrap.py tests/test_alpaca_paper_entry_smoke.py tests/test_alpaca_paper_safety_mutation_smoke.py tests/test_candidate_input_daily_gate.py tests/test_daily_research_evaluator_version.py tests/test_daily_research_lane_scope.py tests/test_daily_research_record_cli.py tests/test_lane_control_plane_bootstrap_cli.py trading_agent/daily_research_contract.py trading_agent/daily_research_ledger.py trading_agent/daily_research_models.py trading_agent/lane_bootstrap.py
git commit -m "feat: scope research by lane contracts"
git push origin feature/paper-account-activities
git rev-list --left-right --count HEAD...origin/feature/paper-account-activities
```

Expected: `0 0` and a clean worktree.
