# US Swing Shadow Vertical Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first executable `us_equities/swing_trading/new_high_momentum` lane: a bounded, read-only day-close OHLCV source that creates deterministic next-session conditional signals and maintains a multi-session shadow outcome ledger without any broker authority.

**Architecture:** A new `swing_shadow` module family owns its immutable day-close source, deterministic 20-day-new-high/RVOL projection, and separate SQLite event ledger. Production calls only the existing read-only Alpaca SIP bars client for at most 50 caller-supplied symbols after the current NYSE close; fixture replay does not load credentials or HTTP. Signals use the existing `TradeSignalEnvelope`, stay `CONDITIONAL`, and are projected to a private JSONL/report. The shadow ledger derives `entry_pending`, `open_multisession`, and terminal outcomes from completed daily OHLCV bars; it imports no Alpaca Paper/account/order code.

**Tech Stack:** Python 3.12, Pydantic, SQLite, existing Alpaca read-only data client, Typer, pytest, Ruff, basedpyright.

---

### Task 1: Canonical Day-Close Input Contract

**Files:**
- Create: `trading_agent/swing_shadow_models.py`
- Create: `trading_agent/swing_shadow_source.py`
- Create: `tests/test_swing_shadow_source.py`

- [ ] **Step 1: Write failing source-contract tests**

Add 21-session two-symbol fixture data. Assert exact symbol/date uniqueness, valid OHLC geometry, SHA-256 source identity, and fail-closed rejection of duplicate dates, missing requested symbols, future bars, naive observations, and production historical dates before credential/client construction.

```python
source = load_swing_daily_source(fixture_root / "manifest.json", session_date=SESSION)
assert source.source_key == swing_daily_source_key(source)
assert source.bars_for("ACME")[-1].session_date == SESSION
```

- [ ] **Step 2: Verify the tests fail**

Run `uv run pytest -q tests/test_swing_shadow_source.py`.

Expected: import failure because no swing daily source exists.

- [ ] **Step 3: Implement models and source adapters**

Define frozen models:

```python
class SwingDailyBar(BaseModel):
    symbol: str
    session_date: dt.date
    observed_at: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

class SwingDailySource(BaseModel):
    session_date: dt.date
    observed_at: dt.datetime
    universe_id: str
    bars: tuple[SwingDailyBar, ...]
```

Require `low <= min(open, close) <= max(open, close) <= high`, finite positive prices, non-negative volume, one bar per symbol/date, and sorted symbols. Add a fixture loader and `swing_daily_source_key()` over canonical JSON.

- [ ] **Step 4: Add bounded production collection**

Implement `collect_current_swing_daily_source()` with the existing `AlpacaBarsClient`. Require a sorted unique universe of 1..50 symbols, `session_date == now(New_York).date()`, and `observed_at >= regular_session_bounds(session_date)[1]`. Fetch no more than 45 calendar days through the current session with the existing SIP request; require a current-session bar for every symbol. Do not import a Paper execution module or submit any mutation.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest -q tests/test_swing_shadow_source.py tests/test_alpaca_daily_cache.py
uv run ruff check trading_agent/swing_shadow_models.py trading_agent/swing_shadow_source.py tests/test_swing_shadow_source.py
uv run basedpyright trading_agent/swing_shadow_models.py trading_agent/swing_shadow_source.py tests/test_swing_shadow_source.py
```

Commit with `git commit -m "feat: add bounded US swing daily source"`.

### Task 2: New-High RVOL Signal Projection

**Files:**
- Create: `trading_agent/swing_new_high_rvol.py`
- Create: `tests/test_swing_new_high_rvol.py`

- [ ] **Step 1: Write failing projection tests**

From a complete source, assert one `TradeSignalEnvelope` for `ACME` when its final close exceeds every prior 20 close and final volume is at least 1.5 times the 20-session average. Assert deterministic ID, `us_equities/swing_trading/new_high_momentum`, conditional next-session trigger, stop, target, and source-only evidence. Add insufficient-history, no-breakout, low-RVOL, early-observation, and invalid-next-session rejections.

```python
signals = project_new_high_rvol_signals(source, config=NewHighRvolConfig())
assert signals[0].entry_type is SignalEntryType.STOP_TRIGGER
assert signals[0].actionability is SignalActionability.CONDITIONAL
```

- [ ] **Step 2: Verify the tests fail**

Run `uv run pytest -q tests/test_swing_new_high_rvol.py`.

Expected: import failure because the swing signal engine does not exist.

- [ ] **Step 3: Implement deterministic projection**

Implement the fixed v1 contract:

```python
lookback_sessions = 20
minimum_rvol = Decimal("1.5")
entry_buffer_bps = Decimal("50")
stop_loss_bps = Decimal("800")
target_r_multiple = Decimal("2")
max_holding_sessions = 10
strategy_version = "new_high_rvol_20d_1p5_v1"
```

For each valid symbol: set trigger to `close * 1.005`, stop to `entry * 0.92`, target to `entry + 2 * (entry - stop)`, and validity through the next regular close. Build a stable ID from strategy version, source key, symbol, and session date. The result is a recommendation, never a current quote or an order.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest -q tests/test_swing_new_high_rvol.py tests/test_signal_contract_models.py
uv run ruff check trading_agent/swing_new_high_rvol.py tests/test_swing_new_high_rvol.py
uv run basedpyright trading_agent/swing_new_high_rvol.py tests/test_swing_new_high_rvol.py
```

Commit with `git commit -m "feat: add US swing new-high RVOL signals"`.

### Task 3: Multi-Session Shadow Event Ledger

**Files:**
- Create: `trading_agent/swing_shadow_store.py`
- Create: `trading_agent/swing_shadow_engine.py`
- Create: `tests/test_swing_shadow_engine.py`

- [ ] **Step 1: Write failing state-machine tests**

Use subsequent completed daily bars for one signal. Assert append-only `signal_created`, `entry_filled`, then `stopped`, `targeted`, `time_exit`, or `expired` events. Assert same-day stop/target collision resolves to stop, unfilled signals expire after next-session validity, replay adds no events, and a changed payload under the same identity conflicts.

```python
events = store.events(signal.signal_id)
assert tuple(event.kind for event in events) == ("signal_created", "entry_filled", "stopped")
```

- [ ] **Step 2: Verify the tests fail**

Run `uv run pytest -q tests/test_swing_shadow_engine.py`.

Expected: import failure because the swing shadow ledger and engine do not exist.

- [ ] **Step 3: Implement isolated SQLite state**

Create a mode-`600` SQLite ledger with one Writer lease and query-only Reader. Store immutable signals and ordered event payloads. Process complete next-session OHLCV as:

```text
entry_pending: high >= trigger -> fill at max(open, trigger)
same bar or open: low <= stop -> stopped before target
otherwise: high >= target -> targeted
after 10 completed holding sessions -> time_exit at close
no fill by valid-until close -> expired
```

Do not store broker/account/order identifiers and do not import Paper execution modules.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest -q tests/test_swing_shadow_engine.py
uv run ruff check trading_agent/swing_shadow_store.py trading_agent/swing_shadow_engine.py tests/test_swing_shadow_engine.py
uv run basedpyright trading_agent/swing_shadow_store.py trading_agent/swing_shadow_engine.py tests/test_swing_shadow_engine.py
```

Commit with `git commit -m "feat: add multi-session swing shadow ledger"`.

### Task 4: Safe CLI and Operation Evidence

**Files:**
- Create: `run_us_swing_shadow.py`
- Create: `examples/us_swing_shadow/manifest.json`
- Create: `examples/us_swing_shadow/daily-bars.json`
- Create: `tests/test_us_swing_shadow_cli.py`
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Create: `docs/checkpoints/2026-07-16-us-swing-shadow-vertical-ko.md`

- [ ] **Step 1: Write failing CLI E2E tests**

Cover fixture first run, replay, missing manifest, and production historical-date refusal before credential/client construction. Assert JSONL/report mode `600`, redacted source output, idempotence, and zero Alpaca Paper mutation.

- [ ] **Step 2: Verify the tests fail**

Run `uv run pytest -q tests/test_us_swing_shadow_cli.py`.

Expected: executable CLI does not exist.

- [ ] **Step 3: Implement the CLI**

Expose exactly:

```text
--session-date YYYY-MM-DD
--universe-file PATH
--fixture-root PATH
--database PATH
--output-dir PATH
--secret-path PATH
```

Fixture mode loads committed daily source. Production first applies current-date/post-close guards, then reads mode-`600` `alpaca.env` and calls only `https://data.alpaca.markets`. Append signals to the existing contract outbox, use `write_private_report()` for an aggregate Korean report, and never invoke Alpaca Paper modules.

- [ ] **Step 4: Verify the full vertical**

Run `--help`, missing-fixture input, fixture happy path, exact replay, and permission checks. Then run:

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
git diff --check
```

Do not run production HTTP, Paper, broker, Telegram, or any order route in this checkpoint.

- [ ] **Step 5: Document and commit**

Record exact verification outcomes, shadow-only boundaries, and the next experiment-ledger integration. Commit with `git commit -m "feat: add US swing shadow vertical"`.

