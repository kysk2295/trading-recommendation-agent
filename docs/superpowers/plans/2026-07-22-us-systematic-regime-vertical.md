# US Systematic Regime Vertical Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first real `us_equities/systematic_quant` vertical: causal completed-daily replay, a next-session risk-on/risk-off recommendation or no-recommendation card, and an immutable shadow trial/lifecycle, without account or order authority.

**Architecture:** Reuse the existing Alpaca completed-daily GET/fixture contract and global multi-market experiment ledger. A fixed ETF universe produces a point-in-time market-context snapshot and deterministic regime-rotation decision; a dedicated append-only store preserves cards and terminal shadow outcomes while the existing experiment ledger preserves the strategy version, `experimental_shadow` lifecycle registration, and daily trial chain. One phase-aware CLI starts existing trials during the regular session and, after close, finalizes the current trial before creating the next-session card.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite append-only ledgers, Typer-compatible argparse CLI, pytest, Ruff, basedpyright, Alpaca read-only daily bars.

---

### Task 1: Causal market-context and replay kernel

**Files:**
- Create: `trading_agent/systematic_regime_models.py`
- Create: `trading_agent/systematic_regime_engine.py`
- Test: `tests/test_systematic_regime_engine.py`

- [ ] **Step 1: Write failing tests for risk-on, risk-off, mixed, and lookahead exclusion**

Use six fixed ETFs (`GLD`, `IEF`, `IWM`, `QQQ`, `SHY`, `SPY`) and 200 completed-session warmup bars. Assert that the engine returns `risk_on` only when SPY is above its 200-session mean, SPY 20-session momentum is positive, and at least two of SPY/QQQ/IWM are above their 50-session means; return `risk_off` only for the symmetric weak case; otherwise return `mixed` and no signals. Mutate only the next session bar and assert the prior replay observation is byte-identical.

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run pytest tests/test_systematic_regime_engine.py -q`

Expected: collection fails because `trading_agent.systematic_regime_engine` does not exist.

- [ ] **Step 3: Implement immutable models and pure calculations**

Define `RegimeLabel`, `SystematicMarketContext`, `SystematicReplayObservation`, `SystematicReplayResult`, `SystematicDecisionKind`, and `SystematicRecommendationCard` as frozen Pydantic models. Implement:

```python
def replay_systematic_regime(source: SwingDailySource) -> SystematicReplayResult: ...

def build_systematic_card(
    source: SwingDailySource,
    replay: SystematicReplayResult,
    strategy_version: str,
) -> SystematicRecommendationCard: ...
```

For each completed decision session, use only bars through that close; rank the matching three-ETF sleeve by trailing 60-session return; score the next session from open to close with 40 bp round-trip cost. The latest card is conditional for the next regular session, contains zero signals for `mixed`, and otherwise contains the two highest-ranked long ETF candidates with entry, stop, target, evidence, and explicit `order_authority=False`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_systematic_regime_engine.py -q`

Expected: all engine tests pass.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/systematic_regime_models.py trading_agent/systematic_regime_engine.py tests/test_systematic_regime_engine.py
git commit -m "feat: add causal systematic regime replay"
```

### Task 2: Long-history read-only source

**Files:**
- Create: `trading_agent/systematic_regime_source.py`
- Test: `tests/test_systematic_regime_source.py`

- [ ] **Step 1: Write failing fixture and transport tests**

Assert strict six-symbol identity, at least 201 aligned completed sessions, current NYSE post-close production gating before credentials, bounded GET-only pagination, repeated page-token rejection, and fixture manifest/source hash parity.

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run pytest tests/test_systematic_regime_source.py -q`

Expected: missing source module.

- [ ] **Step 3: Implement the source adapter**

Reuse `SwingDailyBar`, `SwingDailySource`, `AlpacaDailyPageRequest`, and `AlpacaBarsClient`. Fetch a fixed 430-calendar-day window from `data.alpaca.markets` through existing read-only clients; do not import Paper account, position, order, or mutation modules. Fixture input uses the existing strict `manifest.json` plus `bars.json` shape.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_systematic_regime_source.py -q`

Expected: all source tests pass and recorded HTTP methods are GET.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/systematic_regime_source.py tests/test_systematic_regime_source.py
git commit -m "feat: collect systematic ETF history read only"
```

### Task 3: Immutable card and shadow lifecycle

**Files:**
- Create: `trading_agent/systematic_regime_store.py`
- Create: `trading_agent/systematic_regime_research.py`
- Create: `trading_agent/systematic_regime_trial.py`
- Test: `tests/test_systematic_regime_store.py`
- Test: `tests/test_systematic_regime_trial.py`

- [ ] **Step 1: Write failing append-only and lifecycle tests**

Assert exact replay inserts one card, conflicting payloads fail, SQLite update/delete triggers reject mutation, no-recommendation cards are preserved, strategy registration is `AgentOperatingMode.SHADOW`, lifecycle begins at `experimental_shadow`, and a daily trial follows `registered -> started -> completed` with source/card/outcome hashes. Assert no account/order authority types are imported.

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run pytest tests/test_systematic_regime_store.py tests/test_systematic_regime_trial.py -q`

Expected: missing store/research/trial modules.

- [ ] **Step 3: Implement the dedicated store and global-ledger bridge**

Store canonical card JSON and terminal outcome JSON under mode 600 with a nonblocking single-writer lock and append-only triggers. Register a fixed single-lane hypothesis for `us_equities/systematic_quant/regime_rotation`; derive strategy version from the clean Git SHA; register a multi-market `shadow_forward` trial before the target session open and one multi-market lifecycle registration effective that session. During the target session append `STARTED`; after close append one terminal outcome and `COMPLETED`. A `mixed` card completes with a no-position observation, never a synthetic profit.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_systematic_regime_store.py tests/test_systematic_regime_trial.py -q`

Expected: all store and lifecycle tests pass.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/systematic_regime_store.py trading_agent/systematic_regime_research.py trading_agent/systematic_regime_trial.py tests/test_systematic_regime_store.py tests/test_systematic_regime_trial.py
git commit -m "feat: link systematic cards to shadow trials"
```

### Task 4: Phase-aware operating CLI and card projection

**Files:**
- Create: `trading_agent/systematic_regime_operating.py`
- Create: `run_us_systematic_regime.py`
- Create: `tests/fixtures/systematic_regime/manifest.json`
- Create: `tests/fixtures/systematic_regime/bars.json`
- Test: `tests/test_run_us_systematic_regime.py`

- [ ] **Step 1: Write failing CLI E2E tests**

Drive three ticks with real local ledgers: previous post-close creates a card/trial, target-session intraday starts it without loading credentials or bars, and target post-close finalizes it then creates the next card. Also assert `--help` exits 0, malformed date exits 2, historical production date exits nonzero before credential access, reports are mode 600, and output states account/order/Paper mutation counts are zero.

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run pytest tests/test_run_us_systematic_regime.py -q`

Expected: CLI/module missing.

- [ ] **Step 3: Implement the operating tick and CLI**

Use current New York time in production and allow a clock override only through the Python test entry point. The CLI accepts `--session-date`, optional `--fixture-root`, `--database`, `--experiment-ledger`, `--output-dir`, and `--secret-path`; it exposes no endpoint, account, order, arm, POST, allocation, or position options. It writes a Korean recommendation/no-recommendation card and aggregate report through `write_private_report`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_run_us_systematic_regime.py -q`

Expected: all CLI E2E tests pass.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/systematic_regime_operating.py run_us_systematic_regime.py tests/fixtures/systematic_regime tests/test_run_us_systematic_regime.py
git commit -m "feat: operate systematic regime shadow session"
```

### Task 5: Documentation and verification

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Create: `docs/checkpoints/2026-07-22-us-systematic-regime-vertical-ko.md`

- [ ] **Step 1: Document actual contracts and evidence**

Record the fixed ETF universe, causal timing, thresholds, cost model, read-only production GET, card semantics, shadow lifecycle, fixture versus production evidence, and the permanent absence of account/order/allocation authority.

- [ ] **Step 2: Run required verification**

Run focused tests, `uv run pytest`, `uv run ruff check` on changed Python, `uv run basedpyright` on changed Python, Python no-excuse checks when available, and pure-LOC measurement. Manually run CLI `--help`, one malformed date, and fixture happy path.

- [ ] **Step 3: Commit documentation**

```bash
git add README.md CODEX_START_HERE.md docs/checkpoints/2026-07-22-us-systematic-regime-vertical-ko.md
git commit -m "docs: record systematic regime checkpoint"
```

- [ ] **Step 4: Review final branch evidence**

Inspect `git status --short`, full diff from `origin/main`, and `git log --oneline origin/main..HEAD`. Confirm no credential, launchd, live process, Paper endpoint, account/order module, or unrelated worker file changed.
