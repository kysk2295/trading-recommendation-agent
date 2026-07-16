# Intraday Code-Version Rollover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a clean checkout with new intraday research code to register a new append-only strategy version and complete an ORB shadow trial without mixing evidence with an older commit.

**Architecture:** Keep `StrategyResearchContract.strategy_version` as the human-readable parameter-set base. Derive the ledger identity deterministically from that base and the exact code version, then use that identity consistently in bootstrap, daily records, Reviewer, ORB trial registration, and terminal verification. Existing static legacy rows remain readable and are never rewritten.

**Tech Stack:** Python 3.12, Pydantic, SQLite append-only stores, pytest, Ruff, basedpyright.

---

### Task 1: Add a deterministic code-coupled strategy-version identity

**Files:**
- Modify: `trading_agent/daily_research_contract.py`
- Test: `tests/test_daily_research_contract.py`

- [x] **Step 1: Write the failing identity tests**

```python
def test_strategy_version_identity_changes_only_when_code_version_changes() -> None:
    assert strategy_version_identity(StrategyMode.ORB, "a" * 40) == strategy_version_identity(
        StrategyMode.ORB, "a" * 40
    )
    assert strategy_version_identity(StrategyMode.ORB, "a" * 40) != strategy_version_identity(
        StrategyMode.ORB, "b" * 40
    )
```

- [x] **Step 2: Run the focused test and observe the missing helper failure**

Run: `uv run pytest -q tests/test_daily_research_contract.py -k strategy_version_identity`

- [x] **Step 3: Add `strategy_version_identity(strategy, code_version)`**

```python
def strategy_version_identity(strategy: StrategyMode, code_version: str) -> str:
    digest = hashlib.sha256(code_version.encode("utf-8")).hexdigest()[:12]
    return f"{strategy_contract(strategy).strategy_version}-code-{digest}"
```

Reject an empty or non-canonical code version before hashing. The returned string must satisfy the existing strategy-version identifier validation.

- [x] **Step 4: Re-run the focused test**

Run: `uv run pytest -q tests/test_daily_research_contract.py -k strategy_version_identity`

Expected: PASS.

### Task 2: Separate daily research and adaptive evidence by code identity

**Files:**
- Modify: `trading_agent/daily_research_ledger.py`
- Modify: `trading_agent/lane_reviewer.py`
- Modify: `trading_agent/intraday_lane_daily_snapshot.py`
- Test: `tests/test_daily_research_ledger.py`
- Test: `tests/test_lane_reviewer.py`
- Test: `tests/test_intraday_lane_daily_snapshot.py`

- [x] **Step 1: Write failing tests for a record built at a new code version**

```python
assert record.strategy_version == strategy_version_identity(StrategyMode.ORB, CODE_VERSION)
assert record.strategy_version != strategy_contract(StrategyMode.ORB).strategy_version
```

Add a Reviewer and snapshot case where the dynamic record version and its `code_version` agree, and keep the existing tampered-version case blocked.

- [x] **Step 2: Run the focused tests and observe the static-version assertion failures**

Run: `uv run pytest -q tests/test_daily_research_ledger.py tests/test_lane_reviewer.py tests/test_intraday_lane_daily_snapshot.py`

- [x] **Step 3: Use the identity in every current-record verifier**

`build_daily_record` must store and aggregate by `strategy_version_identity(strategy, code_version)`. The Reviewer and daily snapshot must recompute the expected identity from the record's code version and reject mismatches; adaptive evaluation continues to group only equal record identities.

- [x] **Step 4: Re-run the focused tests**

Run: `uv run pytest -q tests/test_daily_research_ledger.py tests/test_lane_reviewer.py tests/test_intraday_lane_daily_snapshot.py`

Expected: PASS.

### Task 3: Register append-only code rollovers in the global experiment ledger

**Files:**
- Modify: `trading_agent/experiment_ledger_bootstrap.py`
- Test: `tests/test_experiment_ledger_bootstrap.py`
- Test: `tests/test_experiment_ledger_bootstrap_cli.py`

- [x] **Step 1: Write failing rollover/replay tests**

```python
first = bootstrap_current_intraday_experiments(..., code_version="a" * 40, recorded_at=FIRST)
rollover = bootstrap_current_intraday_experiments(..., code_version="b" * 40, recorded_at=SECOND)
replay = bootstrap_current_intraday_experiments(..., code_version="b" * 40, recorded_at=SECOND + dt.timedelta(minutes=1))

assert (first.hypotheses_created, first.versions_created) == (4, 4)
assert (rollover.hypotheses_created, rollover.versions_created) == (0, 4)
assert (replay.hypotheses_created, replay.versions_created) == (0, 0)
```

Assert the old version and lifecycle row remain unchanged, the new lifecycle becomes effective only on the next NYSE session, and a partial pre-existing rollover batch blocks without writes.

- [x] **Step 2: Run the focused bootstrap tests and observe the existing conflict**

Run: `uv run pytest -q tests/test_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap_cli.py`

- [x] **Step 3: Build registrations with the code-coupled identity**

Reuse the original exact hypothesis registration timestamp. For a new four-strategy code batch, append four code-coupled strategy versions and their sequence-one lifecycle registrations at the new bootstrap timestamp. For an exact replay, reuse the existing version timestamp and lifecycle effective date. Do not update, delete, or reinterpret legacy static version rows.

- [x] **Step 4: Re-run the bootstrap tests**

Run: `uv run pytest -q tests/test_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap_cli.py`

Expected: PASS.

### Task 4: Bind ORB preregistration and terminal evidence to the code-coupled version

**Files:**
- Modify: `trading_agent/orb_forward_trial.py`
- Test: `tests/test_orb_forward_trial.py`
- Test: `tests/test_orb_forward_trial_cli.py`

- [x] **Step 1: Write failing registration and terminal tests**

```python
version = strategy_version_identity(StrategyMode.ORB, CODE_VERSION)
assert registration.strategy_version == version
assert registration.trial_id == orb_shadow_trial_id(SESSION_DATE, version)
```

Add a test where a prior static legacy version remains present but a current-code preregistration selects only the current code-coupled version. Keep post-open registration blocked and reject a record whose `code_version` does not match the registered version.

- [x] **Step 2: Run the focused trial tests and observe the static-version failures**

Run: `uv run pytest -q tests/test_orb_forward_trial.py tests/test_orb_forward_trial_cli.py`

- [x] **Step 3: Verify the exact current code identity at registration**

Registration selects the unique ORB ledger version matching both the static strategy contract and `strategy_version_identity(ORB, runtime_code_version)`. `start`, `finalize`, and `fail` resolve the existing session trial first, then verify its registered version and its daily-record code identity. Multiple ORB versions for one session remain fail-closed.

- [x] **Step 4: Re-run the focused trial tests**

Run: `uv run pytest -q tests/test_orb_forward_trial.py tests/test_orb_forward_trial_cli.py`

Expected: PASS.

### Task 5: Update operations documentation and verify the real local rollover path

**Files:**
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/runbooks/alpaca-paper-first-regular-session-smoke-ko.md`
- Create: `docs/checkpoints/2026-07-17-intraday-code-version-rollover-ko.md`

- [x] **Step 1: Document the pre-open sequence**

State that a clean checkout must run the local-only experiment-ledger bootstrap after a code change and before the next NYSE pre-open trial registration. State that a post-open missing preregistration is never backfilled and only read-only observation is allowed.

- [x] **Step 2: Run complete quality and CLI QA**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
uv run python run_experiment_ledger_bootstrap.py --help
uv run python run_orb_forward_trial.py --help
```

Run one malformed local-only bootstrap command and a fixture-backed rollover/replay happy path. Do not run Paper mutation commands and do not print credentials.

- [ ] **Step 3: Commit and push the completed milestone**

```bash
git add trading_agent/daily_research_contract.py trading_agent/daily_research_ledger.py \
  trading_agent/experiment_ledger_bootstrap.py trading_agent/intraday_lane_daily_snapshot.py \
  trading_agent/lane_reviewer.py trading_agent/orb_forward_trial.py tests \
  CODEX_START_HERE.md docs/runbooks/alpaca-paper-first-regular-session-smoke-ko.md \
  docs/checkpoints/2026-07-17-intraday-code-version-rollover-ko.md \
  docs/superpowers/plans/2026-07-17-intraday-code-version-rollover.md
git commit -m "feat: roll intraday strategy versions by code"
git push origin main
```
