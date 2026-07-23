# Futures Positioning Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Join an immutable CFTC TFF positioning artifact to the exact as-of active contract in an immutable futures roll security master through an explicit reviewed binding.

**Architecture:** Three private query-only loaders verify canonical bytes, content-addressed filenames and SHA-256 before a pure builder validates binding effectiveness, causality, report freshness and the active roll window. A separate publisher writes one content-addressed shadow context, while a Typer CLI emits only aggregate operational evidence.

**Tech Stack:** Python 3.12, Pydantic v2, Typer, pytest, Ruff, basedpyright, existing private immutable file primitives.

---

### Task 1: Typed binding and output contract

**Files:**
- Create: `trading_agent/futures_positioning_context_models.py`
- Create: `tests/test_futures_positioning_context.py`

- [ ] **Step 1: Write the failing happy-join test**

Create a private CFTC artifact from `tests/fixtures/cftc_tff/es_latest_two.json`,
load the existing two-contract ES fixture manifest, and define:

```python
binding = FuturesPositioningBinding(
    cftc_contract_market_code="13874A",
    root_symbol="ES",
    venue="XCME",
    observed_at=at("2026-06-01T17:00:00Z"),
    effective_from=at("2026-06-01T17:00:00Z"),
    effective_to=None,
    source_reference="https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
)
```

The first test must call `build_futures_positioning_context(request)` and assert
the active instrument is `cme:es-202609`, the active window contains `as_of`,
all five CFTC categories are preserved and all three input hashes are present.

- [ ] **Step 2: Run the test and verify RED**

Run:
`uv run pytest -q tests/test_futures_positioning_context.py::test_join_binds_positioning_to_exact_active_contract`

Expected: import failure because `futures_positioning_context_models` and the
builder do not exist.

- [ ] **Step 3: Implement the minimum frozen models**

Define:

```python
class FuturesPositioningBinding(BaseModel): ...

@dataclass(frozen=True, slots=True)
class LoadedCftcTffContext:
    value: CftcTffPositioningContext
    artifact_sha256: str

@dataclass(frozen=True, slots=True)
class LoadedFuturesRollMaster:
    value: FuturesRollSecurityMaster
    artifact_sha256: str

@dataclass(frozen=True, slots=True)
class LoadedFuturesPositioningBinding:
    value: FuturesPositioningBinding
    artifact_sha256: str

class FuturesPositioningJoinRequest(BaseModel): ...
class FuturesPositioningContext(BaseModel): ...
class FuturesPositioningContextError(ValueError): ...
```

Use `extra="forbid"`, `frozen=True`, exact SHA/root/code regexes, aware
datetime checks and a `context_id` property derived from canonical JSON.

- [ ] **Step 4: Add one failing test per semantic boundary**

Add independent tests for:

- CFTC code mismatch
- root or venue mismatch
- binding observed/effective time after `as_of`
- CFTC observation after `as_of`
- latest report older than the configured maximum
- report date after the as-of UTC date
- as-of exactly at roll boundary selecting the next contract

Run the focused file after each test is introduced. Each new test must fail
because the corresponding check is absent, then pass after the minimum check.

### Task 2: Private artifact loaders, join and publisher

**Files:**
- Create: `trading_agent/futures_positioning_context.py`
- Modify: `tests/test_futures_positioning_context.py`

- [ ] **Step 1: Write failing loader identity tests**

Publish valid CFTC and futures master artifacts, then rename one file without
changing its bytes. Assert these functions fail before the builder:

```python
load_cftc_tff_context_artifact(path)
load_futures_roll_master_artifact(path)
load_futures_positioning_binding(path)
```

Also make the binding public with mode `0644` and assert it is rejected.

- [ ] **Step 2: Verify RED**

Run:
`uv run pytest -q tests/test_futures_positioning_context.py -k 'artifact or binding'`

Expected: import or attribute failure for the missing loaders.

- [ ] **Step 3: Implement strict query-only loading**

Use `read_private_bytes_query_only` with a 4 MiB bound. Require each published
artifact to equal:

```python
(canonical_experiment_ledger_json(model) + "\n").encode()
```

Require exact filenames:

```text
cftc_tff_context_<context-id>.json
futures_roll_security_master_<master-id>.json
```

Parse the binding from canonical newline-terminated JSON and return SHA-bound
loaded dataclasses. Convert only named Pydantic/private-reader failures to
`FuturesPositioningContextError`.

- [ ] **Step 4: Implement the pure builder and publisher**

`build_futures_positioning_context(request)` must revalidate every nested model,
enforce binding and freshness, call `resolve_active_futures_contract`, then
construct the exact output contract. `publish_futures_positioning_context`
must use `publish_private_immutable_text` at:

`futures_positioning_context_<context-id>.json`

Run:
`uv run pytest -q tests/test_futures_positioning_context.py`

Expected: all semantic, loader and publisher tests pass.

### Task 3: CLI E2E and safe aggregate report

**Files:**
- Create: `run_futures_positioning_context.py`
- Create: `tests/test_futures_positioning_context_cli.py`

- [ ] **Step 1: Write the failing CLI E2E**

The test must create private valid inputs and run:

```text
--cftc-context <path>
--futures-master <path>
--binding <path>
--as-of 2026-07-24T18:00:00Z
--maximum-report-age-days 14
--output-dir <path>
```

Assert exit `0`, exactly one mode-600 content-addressed artifact, report mode
`600`, five categories, active contract present, network access `0`, and no
instrument ID/provider symbol/local path in the report. Run the same command
again and assert `artifact_created=no` with unchanged file SHA.

- [ ] **Step 2: Verify RED**

Run:
`uv run pytest -q tests/test_futures_positioning_context_cli.py`

Expected: failure because `run_futures_positioning_context.py` does not exist.

- [ ] **Step 3: Implement the Typer boundary**

The CLI parses `as_of`, loads three inputs, builds/publishes the context and
writes `futures_positioning_context_ko.md` with
`write_private_stable_report`. Catch only the typed context error, stable report
error, `OSError`, `TypeError` and `ValueError`, then return Typer bad-parameter
exit without input contents.

- [ ] **Step 4: Add bad-input E2E and verify GREEN**

Use a mismatched binding and assert exit `2`, output directory absent, stdout
does not contain source values, and no input is modified.

Run:
`uv run pytest -q tests/test_futures_positioning_context_cli.py`

Expected: happy/replay and bad-input tests pass.

### Task 4: Verification, fixture evidence and publication

**Files:**
- Create: `docs/checkpoints/2026-07-24-m6-futures-positioning-context-ko.md`
- Modify: `README.md`

- [ ] **Step 1: Run manual CLI QA**

Run CLI `--help`, one mismatched private binding, one private happy path and an
exact replay. Record exit codes, artifact creation `yes/no`, SHA equality and
mode `600`.

- [ ] **Step 2: Run focused and global gates**

Run:

```text
uv run pytest -q tests/test_futures_positioning_context.py tests/test_futures_positioning_context_cli.py tests/test_cftc_tff_parser.py tests/test_futures_roll_security_master.py
uv run ruff check trading_agent/futures_positioning_context_models.py trading_agent/futures_positioning_context.py run_futures_positioning_context.py tests/test_futures_positioning_context.py tests/test_futures_positioning_context_cli.py
uv run basedpyright trading_agent/futures_positioning_context_models.py trading_agent/futures_positioning_context.py run_futures_positioning_context.py tests/test_futures_positioning_context.py tests/test_futures_positioning_context_cli.py
uv run python -m compileall -q trading_agent run_futures_positioning_context.py
uv run pytest -q
uv run ruff check .
uv run basedpyright
```

Run the Python no-excuse checker and measure pure LOC for every changed Python
file. No production file may exceed 250 pure LOC.

- [ ] **Step 3: Record evidence and commit**

Document the exact input semantic IDs and file SHA, output context ID/SHA,
active-contract selection, replay counts, modes and mutation zero. State that
the current futures master input is reviewed fixture evidence, not licensed
CME/ICE coverage or a strategy result.

Commit implementation/tests as `feat: add futures positioning context`, commit
checkpoint/README separately, push `HEAD:main`, verify HEAD equals
`origin/main`, and leave the worktree clean.
