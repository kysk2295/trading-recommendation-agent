# KR Same-Cycle Source Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the four existing KR source stages with one date and cycle ID in deterministic order, then append or replay the existing complete/incomplete source cycle without opening providers for terminal replay.

**Architecture:** `trading_agent.kr_source_cycle_orchestrator` owns pure stage sequencing and exact terminal-contract checks.  `run_kr_same_cycle_collect.py` adapts that service to the existing OpenDART, LS NWS, KIS ranking, volume-surge, and coordinator CLIs.  OpenDART gains a date-bound v2 DB-only resume path so the command can safely replay a terminal source before any provider setup.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, Typer, pytest, Ruff, basedpyright.

---

### Task 1: OpenDART Date-Bound Replay Preflight

**Files:**
- Modify: `trading_agent/opendart_collection.py`
- Modify: `run_opendart_collect.py`
- Modify: `tests/test_opendart_collection.py`
- Modify: `tests/test_opendart_collect_cli.py`

- [ ] **Step 1: Write failing replay tests**

Add tests which collect a v2 fixture source run and then assert a second CLI call with `fixture_manifest=None` does not call `load_opendart_credentials`, `create_opendart_http_client`, or `load_opendart_fixture`.  Assert a replay with a different collection date fails before its fetcher runs.  Assert the stored v2 run has `collection_date=COLLECTION_DATE`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest -q tests/test_opendart_collection.py tests/test_opendart_collect_cli.py`

Expected: the new replay test fails because OpenDART has no resume preflight and no date-bound v2 run.

- [ ] **Step 3: Add the minimum date-bound resume contract**

Set `OPENDART_ADAPTER_VERSION = "opendart-list-v2"`.  Add:

```python
def resume_opendart_collection(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    adapter_version: str = OPENDART_ADAPTER_VERSION,
) -> OpenDartCollectionResult | None:
    ...
```

It returns `None` only when no OpenDART run or orphan receipts exist.  It validates exactly one run, the exact `"<cycle>:dart"` ID, v2 adapter version, and exact collection date; otherwise it raises `ValueError`.  `collect_opendart_disclosures()` calls it before `fetch_page()`, and its terminal run stores `collection_date`.  `run_opendart_collect.main()` calls it before selecting fixture or live dependencies.

- [ ] **Step 4: Verify focused gates**

Run:

```bash
uv run pytest -q tests/test_opendart_collection.py tests/test_opendart_collect_cli.py
uv run ruff check trading_agent/opendart_collection.py run_opendart_collect.py tests/test_opendart_collection.py tests/test_opendart_collect_cli.py
uv run basedpyright trading_agent/opendart_collection.py run_opendart_collect.py tests/test_opendart_collection.py tests/test_opendart_collect_cli.py
```

- [ ] **Step 5: Commit the preflight**

```bash
git add trading_agent/opendart_collection.py run_opendart_collect.py tests/test_opendart_collection.py tests/test_opendart_collect_cli.py
git commit -m "fix: preflight date-bound OpenDART replay"
```

### Task 2: Serial Orchestration Service

**Files:**
- Create: `trading_agent/kr_source_cycle_orchestrator.py`
- Create: `tests/test_kr_source_cycle_orchestrator.py`

- [ ] **Step 1: Write failing sequencing tests**

Use injected callbacks which append exact source runs and append their source name to a list.  Assert success runs `[DART, NEWS, KIS_RANKING, VOLUME_SURGE]` then appends one complete coordinator cycle.  Assert a terminal failed DART callback still runs NEWS, KIS, and VOLUME and appends an incomplete cycle.  Assert a callback which leaves no terminal run aborts before the next callback.  Seed four terminal runs plus a cycle and assert historical replay calls no callback.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest -q tests/test_kr_source_cycle_orchestrator.py`

Expected: import failure because the orchestration module does not exist.

- [ ] **Step 3: Implement exact sequential finalization**

Expose frozen stage/outcome result types and:

```python
def orchestrate_kr_source_cycle(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    stage_runners: Mapping[KrCatalystSource, Callable[[], None]],
) -> KrSourceCycleOrchestration:
    ...
```

Require exactly the four source keys.  For each ordered source, verify an existing source run is exact or invoke one callback.  Re-read the ledger after every callback; continue only when it left one exact terminal run.  Call `finalize_kr_source_cycle()` only after all four stages.  Reuse an existing exact cycle as a no-op; let store conflicts raise.  Never use threads, tasks, subprocesses, provider clients, credentials, or broker imports.

- [ ] **Step 4: Verify focused gates**

Run:

```bash
uv run pytest -q tests/test_kr_source_cycle_orchestrator.py tests/test_kr_source_cycle.py
uv run ruff check trading_agent/kr_source_cycle_orchestrator.py tests/test_kr_source_cycle_orchestrator.py
uv run basedpyright trading_agent/kr_source_cycle_orchestrator.py tests/test_kr_source_cycle_orchestrator.py
```

- [ ] **Step 5: Commit the service**

```bash
git add trading_agent/kr_source_cycle_orchestrator.py tests/test_kr_source_cycle_orchestrator.py
git commit -m "feat: orchestrate serial KR source cycles"
```

### Task 3: Fixture and Production CLI Adapter

**Files:**
- Create: `run_kr_same_cycle_collect.py`
- Create: `tests/test_kr_same_cycle_collect_cli.py`
- Create: `tests/fixtures/opendart/fixture-manifest.json`
- Create: `tests/fixtures/opendart/page-1.json`

- [ ] **Step 1: Write failing CLI tests**

Create an isolated fixture root containing OpenDART, LS NWS, and KIS manifests.  Assert the command creates four source runs and one complete cycle, writes mode-600 aggregate reports, and contains no fixture payload/hash/path/cycle ID.  On a second call patch all provider entrypoints to raise and assert replay still succeeds.  Seed a failed source and assert nonzero with an immutable incomplete cycle.  Assert malformed input creates no DB and production historical dates are rejected before every provider entrypoint.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest -q tests/test_kr_same_cycle_collect_cli.py`

Expected: import failure because the orchestration CLI does not exist.

- [ ] **Step 3: Implement the bounded CLI**

Expose only `--collection-cycle-id`, `--collection-date`, `--database`, `--output-dir`, `--fixture-root`, and `--help`.  Resolve the three exact fixture manifest paths only in fixture mode.  In production, reject a non-current KST date only after an all-terminal replay check and before any source callback.  Run existing source CLIs in the specified order with the same database/date/cycle; catch their safe `BadParameter` only to re-read the exact terminal run.  Write aggregate `kr_same_cycle_coverage.csv` and `kr_same_cycle_summary_ko.md` through `write_private_report()`.  Return success only for a complete final cycle.

- [ ] **Step 4: Verify CLI and static gates**

Run:

```bash
uv run pytest -q tests/test_kr_same_cycle_collect_cli.py tests/test_opendart_collect_cli.py tests/test_ls_nws_collect_cli.py tests/test_kis_kr_ranking_collect_cli.py tests/test_kr_volume_surge_cli.py tests/test_kr_source_cycle_cli.py
uv run ruff check run_kr_same_cycle_collect.py tests/test_kr_same_cycle_collect_cli.py
uv run basedpyright run_kr_same_cycle_collect.py tests/test_kr_same_cycle_collect_cli.py
```

- [ ] **Step 5: Commit the CLI**

```bash
git add run_kr_same_cycle_collect.py tests/test_kr_same_cycle_collect_cli.py tests/fixtures/opendart/fixture-manifest.json tests/fixtures/opendart/page-1.json
git commit -m "feat: add KR same-cycle collection CLI"
```

### Task 4: Documentation and Complete Verification

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/superpowers/plans/2026-07-16-kr-same-cycle-orchestrator.md`
- Create: `docs/checkpoints/2026-07-16-kr-same-cycle-orchestrator-ko.md`

- [ ] **Step 1: Document usage and boundary**

Document the fixture-root layout, serial order, terminal replay behavior, current-KST production gate, incomplete-cycle semantics, and the fact that the command is not a KR Opportunity, trade signal, shadow fill, or order system.

- [ ] **Step 2: Run full automated gates**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
```

- [ ] **Step 3: Run manual CLI QA**

Run `./run_kr_same_cycle_collect.py --help`, a malformed ID, a fixture happy path, the identical fixture replay with provider entrypoints unavailable through its regression test, and a fixture failed-source path.  Confirm reports are mode 600 and no provider/credential/broker is used during replay.

- [ ] **Step 4: Record the checkpoint and mark plan items**

Record exact test, lint, type, help, invalid-input, fixture, replay, and incomplete-cycle outcomes.  State that production provider calls, KIS/LS account endpoints, domestic order paths, Alpaca, LLM, and external messages are zero.  Mark every completed checkbox in this plan.

- [ ] **Step 5: Commit documentation and verification**

```bash
git add README.md CODEX_START_HERE.md docs/superpowers/plans/2026-07-16-kr-same-cycle-orchestrator.md docs/checkpoints/2026-07-16-kr-same-cycle-orchestrator-ko.md
git commit -m "docs: record KR same-cycle orchestrator"
```
