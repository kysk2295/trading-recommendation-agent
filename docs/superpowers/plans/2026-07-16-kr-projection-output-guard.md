# KR Projection Output Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local KR keyword Opportunity projection fail before any classification append when its SQLite input ledger or SQLite sidecars would collide with its JSONL outbox or Korean summary report, and make those projection artifacts owner-only.

**Architecture:** `run_kr_theme_projection.py` owns the CLI-specific path guard because `contract_outbox.py` is shared by US publication paths with different visibility requirements. The guard normalizes `--database`, its `.writer.lock`, `-journal`, `-shm`, and `-wal` companions, then compares them against exactly `opportunities.v1.jsonl` and `kr_theme_projection_summary_ko.md` below `--output-dir` before opening the ledger or parsing classification evidence. The runner uses `write_private_report()` for its summary and forces the already-appended KR outbox to mode `600`; it does not change the generic outbox contract.

**Tech Stack:** Python 3.12, pathlib, SQLite, Typer, pytest, Ruff, basedpyright.

---

### Task 1: Projection Target Regression Tests

**Files:**
- Modify: `tests/test_kr_theme_projection_cli.py`

- [x] **Step 1: Write failing collision tests**

Add a parametrized test with these database paths under one projection output directory:

```python
(
    "opportunities.v1.jsonl",
    "kr_theme_projection_summary_ko.md",
)
```

For each path, first call `run_kr_theme_ingest.main()` to create the valid synthetic SQLite ledger at that exact path. Then call `run_kr_theme_projection.main()` with the example run manifest and the shared output directory. Assert `typer.BadParameter`, `KrThemeStore(database).is_initialized() is True`, no classifications were appended, and no outbox/report artifact was created at the other target.

- [x] **Step 2: Write failing privacy test**

Extend the existing happy/replay test to assert both `opportunities.v1.jsonl` and `kr_theme_projection_summary_ko.md` have `stat.S_IMODE(path.stat().st_mode) == 0o600` after the first execution and after replay.

- [x] **Step 3: Run the focused test and verify RED**

Run:

```bash
uv run pytest -q tests/test_kr_theme_projection_cli.py
```

Expected: collision paths are accepted or corrupt the temporary ledger, and artifacts are not forced to mode `600`.

### Task 2: Fail-Closed Output Guard and Private Artifacts

**Files:**
- Modify: `run_kr_theme_projection.py`
- Modify: `tests/test_kr_theme_projection_cli.py`

- [x] **Step 1: Add explicit artifact and ledger target helpers**

Define the exact relative output artifacts:

```python
_PROJECTION_ARTIFACTS: Final = (
    Path("opportunities.v1.jsonl"),
    Path("kr_theme_projection_summary_ko.md"),
)
```

Add `_validate_projection_targets(database: Path, output_dir: Path) -> None`. It resolves the database plus `.writer.lock`, `-journal`, `-shm`, and `-wal`, resolves each artifact target, rejects any target symlink, and raises `KrThemeProjectionRunError` when a normalized artifact target overlaps a normalized ledger target.

- [x] **Step 2: Validate before opening the ledger**

At the start of `main()`, construct `database_path` and `output_path`, call `_validate_projection_targets()` before `KrThemeStore(database_path)` and before classification generation, then translate its safe domain error to `typer.BadParameter` with no exception cause. Retain the existing manifest-first validation so an invalid manifest still creates no database or output directory.

- [x] **Step 3: Make only KR projection artifacts private**

After `append_opportunity_snapshot()` finishes, set the existing outbox to mode `0o600` when it exists. Replace the direct summary `write_text()` call with `write_private_report(output / "kr_theme_projection_summary_ko.md", report)`. Do not alter `contract_outbox.py` or any US outbox/card visibility behavior.

- [x] **Step 4: Run focused gates and verify GREEN**

Run:

```bash
uv run pytest -q tests/test_kr_theme_projection_cli.py tests/test_contract_outbox.py
uv run ruff check run_kr_theme_projection.py tests/test_kr_theme_projection_cli.py
uv run basedpyright run_kr_theme_projection.py tests/test_kr_theme_projection_cli.py
```

Expected: focused tests pass, Ruff reports `All checks passed!`, and basedpyright reports zero errors and warnings.

- [x] **Step 5: Commit the guard**

```bash
git add run_kr_theme_projection.py tests/test_kr_theme_projection_cli.py
git commit -m "fix: guard KR projection output artifacts"
```

### Task 3: Documentation and Complete Verification

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/superpowers/plans/2026-07-16-kr-projection-output-guard.md`
- Create: `docs/checkpoints/2026-07-16-kr-projection-output-guard-ko.md`

- [x] **Step 1: Document the output contract**

State that the projection remains local-only, checks database/sidecar/output collisions before opening its ledger, stores the KR JSONL and Korean aggregate report with mode `600`, and still does not call provider, LLM, broker, TradeSignal, or domestic order code.

- [x] **Step 2: Run full automated gates**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
```

- [x] **Step 3: Run manual CLI QA**

Run `./run_kr_theme_projection.py --help`, a missing manifest invocation that creates no database/output, a synthetic ingest plus projection happy path, and exact replay. Confirm the outbox and summary are mode `600`; do not open provider credentials, network, LLM, broker, or domestic order routes.

- [x] **Step 4: Record verification and mark plan items**

Record exact focused/full test, lint, type, help, invalid-input, happy/replay, artifact-mode, and zero-external-call outcomes. Mark every completed checkbox.

- [x] **Step 5: Commit documentation and verification**

```bash
git add README.md CODEX_START_HERE.md docs/superpowers/plans/2026-07-16-kr-projection-output-guard.md docs/checkpoints/2026-07-16-kr-projection-output-guard-ko.md
git commit -m "docs: record KR projection output guard"
```

### Task 4: Independent Review Remediation

**Files:**
- Modify: `run_kr_theme_projection.py`
- Modify: `tests/test_kr_theme_projection_cli.py`
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/checkpoints/2026-07-16-kr-projection-output-guard-ko.md`
- Modify: `docs/superpowers/plans/2026-07-16-kr-projection-output-guard.md`

- [x] **Step 1: Reproduce reviewer findings**

Add regression coverage proving that direct output symlinks and hard-link aliases to the SQLite ledger are rejected before `KrThemeStore` opens, and that a new JSONL is private before the generic append helper can fail. The former guard accepted hard links; the latter had no file to protect until after append.

- [x] **Step 2: Harden KR-only preparation and collision checks**

Compare existing artifact and ledger device/inode pairs in addition to normalized paths. Create a new KR outbox through exclusive `os.open(..., 0o600)` and chmod an existing non-symlink outbox before append. Keep `contract_outbox.py` unchanged.

- [x] **Step 3: Verify remediation**

Run focused tests (`37 passed`), targeted Ruff/type gates, `git diff --check`, and full tests (`1587 passed`).

- [x] **Step 4: Record independent review outcome**

Record the corrected hard-link, pre-append permission, and symlink coverage in this plan and the checkpoint.

- [x] **Step 5: Commit review remediation**

```bash
git add run_kr_theme_projection.py tests/test_kr_theme_projection_cli.py README.md CODEX_START_HERE.md docs/checkpoints/2026-07-16-kr-projection-output-guard-ko.md docs/superpowers/plans/2026-07-16-kr-projection-output-guard.md
git commit -m "fix: harden KR projection artifact aliases"
```
