# US Opportunity And Signal Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Follow test-driven development and preserve the existing KIS scan, v1 alert outbox, SQLite store, and Paper execution behavior.

**Goal:** Publish the existing KIS US ranking and risk-screen result as an immutable `OpportunitySnapshot`, then publish only fresh matching SETUP recommendations as conditional `TradeSignalEnvelope` records and Korean operator cards.

**Architecture:** This is an additive projection layer around the current intraday scanner. The current ranking discovery, halt feed, market-risk gate, scanner, SQLite recommendation store, report, and v1 alert outbox remain authoritative and unchanged. New pure projectors validate complete causal input, while append-only JSONL writers provide idempotent local delivery. Because this milestone does not fetch a fresh quote at publication time, every new trade signal remains `conditional` and cannot trigger an order.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, Ruff, basedpyright, uv

---

## Safety And Compatibility Invariants

- Do not add or call a broker mutation API.
- Do not change the existing Paper execution arm, account binding, risk caps, or order lifecycle.
- Do not remove or reinterpret the existing `alerts.jsonl` and Korean Markdown v1 outbox.
- Publish no v2 opportunity when any of the six KIS ranking requests is missing, duplicated, or failed.
- Publish no v2 signal unless its SETUP recommendation is fresh and its symbol belongs to the exact published opportunity.
- Treat all v2 signals as `SignalActionability.CONDITIONAL`; `quote_validation` remains `None`.
- Parse existing JSONL records structurally. Duplicate ID plus identical payload is a no-op; duplicate ID plus different payload fails closed.
- Never log credentials, account identifiers, or raw authorization headers.

## File Map

- Create `trading_agent/kis_opportunity_projection.py`: complete KIS discovery and market-risk screen to `OpportunitySnapshot` projection.
- Create `trading_agent/contract_outbox.py`: append-only opportunity/signal JSONL writers and Korean signal cards.
- Create `trading_agent/trade_signal_publication.py`: fresh SETUP selection and conditional signal publication contracts.
- Create `tests/test_kis_opportunity_projection.py`: complete, incomplete, causal, and empty projection tests.
- Create `tests/test_contract_outbox.py`: append, idempotence, conflict, malformed JSONL, and card tests.
- Create `tests/test_trade_signal_publication.py`: freshness, exact-opportunity, all strategy mappings, and conditional-only tests.
- Create `tests/test_run_kis_paper_scan_contracts.py`: network-free integration helper tests.
- Modify `run_kis_paper_scan.py`: invoke the additive v2 publication helpers beside the existing report/outbox.
- Modify `README.md`: document the local artifacts and conditional semantics.
- Create `docs/checkpoints/2026-07-15-us-opportunity-signal-publication-ko.md`: record verification evidence and remaining live quote work.

### Task 1: Project Complete KIS Discovery To An Opportunity

**Files:**
- Create: `tests/test_kis_opportunity_projection.py`
- Create: `trading_agent/kis_opportunity_projection.py`

- [ ] **Step 1: Write failing projection tests**

Cover these public behaviors:

- the exact two sources (`updown`, `volume`) across `NAS`, `NYS`, and `AMS` are required;
- any `RankingFailure`, missing source/exchange pair, or duplicate group raises a domain projection error;
- an empty selected screen returns `None` rather than an invalid empty snapshot;
- selected symbols must exist in the supplied ranking groups;
- evidence timestamps and source coverage cannot be newer than `observed_at`;
- selected order becomes contiguous candidate ranks;
- candidate feature names are sorted and include price, change, volume, dollar volume, volume-to-ADV, and spread;
- the lane is exactly `us_equities/opportunity_manager/ranking_momentum`;
- repeated projection of identical input creates the same deterministic opportunity ID.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_kis_opportunity_projection.py -q`

Expected: collection fails because `trading_agent.kis_opportunity_projection` does not exist.

- [ ] **Step 3: Implement the minimal pure projector**

Add:

```python
class InvalidKisOpportunityProjectionError(ValueError): ...

def project_kis_us_opportunity(
    discovery: RankingDiscovery,
    *,
    halt_snapshot: HaltSnapshot,
    risk_screen: MarketRiskScreen,
    observed_at: dt.datetime,
) -> OpportunitySnapshot | None: ...
```

Use producer version `kis-risk-screen-v1`, a 60-second validity window, Decimal-safe feature serialization, and a deterministic ID formed from the UTC observation time plus a short SHA-256 digest of canonical selected exchange/symbol coordinates. Score is the existing `change_pct`; do not invent a confidence score.

Create sorted `EvidenceRef` records for source ranking rows, the halt snapshot, and selected market-risk rows. Create sorted `SourceCoverage` records for all six ranking requests plus the halt feed.

- [ ] **Step 4: Verify GREEN and lint the slice**

Run:

```bash
uv run pytest tests/test_kis_opportunity_projection.py -q
uv run ruff check trading_agent/kis_opportunity_projection.py tests/test_kis_opportunity_projection.py
```

- [ ] **Step 5: Commit Task 1**

```bash
git add trading_agent/kis_opportunity_projection.py tests/test_kis_opportunity_projection.py
git commit -m "feat: project KIS rankings to opportunities"
```

### Task 2: Add Immutable Local Contract Outboxes

**Files:**
- Create: `tests/test_contract_outbox.py`
- Create: `trading_agent/contract_outbox.py`

- [ ] **Step 1: Write failing outbox tests**

Test opportunity and signal JSONL independently:

- a first append writes one canonical JSON object plus newline;
- an identical repeated ID/payload returns `False` and does not add a line;
- the same ID with a different payload raises a conflict error;
- malformed existing JSON, a non-object record, or a missing identity field fails closed;
- signal publication writes a Korean Markdown card containing market, strategy, observation time, publication time, expiry, symbol, conditional entry, stop, targets, invalidation, and rationale;
- card filenames are stable and safe.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_contract_outbox.py -q`

- [ ] **Step 3: Implement structured append-only writers**

Add domain errors for malformed files and identity conflicts. Serialize Pydantic models with `model_dump(mode="json")` and deterministic key ordering. Do not use substring matching for dedupe. Create parent directories only when a write is required.

- [ ] **Step 4: Verify GREEN and lint the slice**

Run:

```bash
uv run pytest tests/test_contract_outbox.py -q
uv run ruff check trading_agent/contract_outbox.py tests/test_contract_outbox.py
```

- [ ] **Step 5: Commit Task 2**

```bash
git add trading_agent/contract_outbox.py tests/test_contract_outbox.py
git commit -m "feat: add immutable contract outboxes"
```

### Task 3: Publish Fresh Matching SETUP Signals

**Files:**
- Create: `tests/test_trade_signal_publication.py`
- Create: `trading_agent/trade_signal_publication.py`
- Modify only if a regression requires it: `trading_agent/recommendation_signal_projection.py`

- [ ] **Step 1: Write failing publication tests**

Specify a frozen `TradeSignalPublication` contract with `schema_version`, `published_at`, and `signal`. Test that:

- publication time is timezone-aware, is not before signal observation, is before signal expiry, and is at most five minutes after observation;
- only new `RecommendationState.SETUP` rows are eligible;
- recommendation symbols must occur in the exact opportunity snapshot;
- stale, unrelated, non-SETUP, and already-cut-off recommendations are skipped;
- ORB, VWAP reclaim, HOD breakout, and gap-and-go canonical strategy IDs map to their existing stored strategy names;
- evidence contains both the opportunity and recommendation records in canonical order;
- emitted envelopes are always conditional and have no quote validation.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_trade_signal_publication.py -q`

- [ ] **Step 3: Implement pure publication selection**

Add a deterministic function accepting recommendation records, a canonical US day strategy lane/version, one exact opportunity snapshot, publication time, and lower created-at bound. Reuse `project_intraday_recommendation`; do not duplicate entry/stop/target conversion.

- [ ] **Step 4: Verify GREEN and compatibility**

Run:

```bash
uv run pytest tests/test_trade_signal_publication.py tests/test_recommendation_signal_projection.py -q
uv run ruff check trading_agent/trade_signal_publication.py tests/test_trade_signal_publication.py
```

- [ ] **Step 5: Commit Task 3**

```bash
git add trading_agent/trade_signal_publication.py tests/test_trade_signal_publication.py
git commit -m "feat: publish conditional trade signals"
```

### Task 4: Integrate Beside The Existing KIS Scan Outputs

**Files:**
- Create: `tests/test_run_kis_paper_scan_contracts.py`
- Modify: `run_kis_paper_scan.py`

- [ ] **Step 1: Write failing network-free helper tests**

Test helper functions directly with fixtures. Verify:

- complete discovery writes `opportunities.v1.jsonl`;
- failed discovery writes no opportunity or signal v2 artifact;
- fresh matching recommendations write `trade-signals.v1.jsonl` and Korean cards;
- repeated helper execution is idempotent;
- existing v1 outbox paths and recommendation rows remain untouched.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_run_kis_paper_scan_contracts.py -q`

- [ ] **Step 3: Add narrow orchestration helpers and calls**

Add independently testable helpers, then call them from `main` after ranking/risk journaling and after the existing scanner/outbox respectively. Use `StrategyMode.value` for the canonical strategy ID and `<strategy-id>-v1` for the current producer version. Print only counts and artifact paths, never credentials or account data.

The existing `write_report` and `write_alert_outbox` calls remain in place and retain their current behavior.

- [ ] **Step 4: Verify GREEN and CLI behavior**

Run:

```bash
uv run pytest tests/test_run_kis_paper_scan_contracts.py tests/test_kis_paper_scan_cli.py -q
uv run ruff check run_kis_paper_scan.py tests/test_run_kis_paper_scan_contracts.py
uv run python run_kis_paper_scan.py --help
```

Also run the repository's existing invalid-input and fixture-backed happy-path commands documented in the current checkpoint. Confirm no network mutation and no broker POST.

- [ ] **Step 5: Commit Task 4**

```bash
git add run_kis_paper_scan.py tests/test_run_kis_paper_scan_contracts.py
git commit -m "feat: emit KIS opportunity and signal contracts"
```

### Task 5: Document And Verify The Milestone

**Files:**
- Modify: `README.md`
- Create: `docs/checkpoints/2026-07-15-us-opportunity-signal-publication-ko.md`

- [ ] **Step 1: Update operator-facing documentation**

Document artifact names, their additive relationship to the v1 alert outbox, the exact complete-source gate, the five-minute publication limit, and the fact that signals are conditional until a later fresh-quote milestone.

- [ ] **Step 2: Run focused and full verification**

Run:

```bash
uv run pytest tests/test_kis_opportunity_projection.py tests/test_contract_outbox.py tests/test_trade_signal_publication.py tests/test_run_kis_paper_scan_contracts.py tests/test_recommendation_signal_projection.py -q
uv run pytest -q
uv run ruff check .
uv run basedpyright
```

Run `run_kis_paper_scan.py --help`, one invalid CLI invocation, and the fixture-backed happy path. Record exact outcomes without claiming an actual current-market signal or any broker POST.

- [ ] **Step 3: Check diff and repository state**

Run:

```bash
git diff --check
git status --short
git log --oneline --decorate -8
```

- [ ] **Step 4: Commit and push the checkpoint**

```bash
git add README.md docs/checkpoints/2026-07-15-us-opportunity-signal-publication-ko.md
git commit -m "docs: record US opportunity publication milestone"
git push origin main
```

## Deferred Work

- Fresh quote revalidation and transition to `current_quote_validated`.
- External chat, push, or webhook delivery adapters.
- KR theme ingestion and KR day shadow signal generation.
- Swing and systematic-quant strategy implementations.
- Any broker order, including Alpaca Paper, from these publication artifacts.
