# KR Multi-Source Cycle Coordinator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 네 terminal KR source run이 모두 존재할 때만 exact append-only collection cycle을 확정하고 source별 coverage를 redacted report로 남긴다.

**Architecture:** 새 coordinator는 기존 `KrThemeStore`, `KrSourceCollectionRun`, `KrCatalystCollectionCycle` 계약만 사용한다. Writer lease를 먼저 열어 schema migration을 보장하고 네 run에서 cycle을 결정적으로 투영하며, DB 전용 CLI는 provider·credential import 없이 coverage CSV와 한국어 요약을 mode 600으로 기록한다.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, Typer, pytest, Ruff, basedpyright

---

### Task 1: Exact Source-Run Finalization

**Files:**
- Create: `trading_agent/kr_source_cycle.py`
- Create: `tests/test_kr_source_cycle.py`

- [x] **Step 1: Write failing tests for complete, failed, missing, replay and invalid evidence paths**

Add tests that seed terminal source runs through `KrThemeStore.writer()` and assert:

```python
result = finalize_kr_source_cycle(store, collection_cycle_id=CYCLE_ID)
assert result.cycle is not None
assert result.cycle.complete is True
assert result.appended is True
assert result.missing_sources == ()
```

The same test module must also prove a failed run is copied unchanged, a missing source produces no cycle row, an exact restart is a no-op, conflicting pre-existing content fails, and observation time outside the derived cycle is rejected.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest -q tests/test_kr_source_cycle.py`

Expected: collection fails because `trading_agent.kr_source_cycle` does not exist.

- [x] **Step 3: Implement the minimal deterministic coordinator**

Create frozen result data and one public function:

```python
@dataclass(frozen=True, slots=True)
class KrSourceCycleFinalization:
    source_runs: tuple[KrSourceCollectionRun, ...]
    missing_sources: tuple[KrCatalystSource, ...]
    cycle: KrCatalystCollectionCycle | None
    appended: bool


def finalize_kr_source_cycle(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
) -> KrSourceCycleFinalization:
    with store.writer() as writer:
        runs = tuple(sorted(store.source_runs(collection_cycle_id), key=lambda item: item.source.value))
        present = {run.source for run in runs}
        missing = tuple(source for source in KrCatalystSource if source not in present)
        if missing:
            return KrSourceCycleFinalization(runs, missing, None, False)
        cycle = KrCatalystCollectionCycle(
            collection_cycle_id=collection_cycle_id,
            started_at=min(run.started_at for run in runs),
            completed_at=max(run.completed_at for run in runs),
            coverage=tuple(
                KrSourceCoverage(
                    source=run.source,
                    status=run.status,
                    record_count=run.record_count,
                    failure_code=run.failure_code,
                )
                for run in runs
            ),
        )
        appended = writer.append_cycle(cycle)
    return KrSourceCycleFinalization(runs, (), cycle, appended)
```

Open the Writer lease before calling `store.source_runs()`. Sort by source value, return without mutation when a source is missing, derive min/max times and exact coverage when all four exist, then call `writer.append_cycle()` so the existing store performs count/time/conflict validation.

- [x] **Step 4: Run focused tests and static checks**

Run:

```bash
uv run pytest -q tests/test_kr_source_cycle.py tests/test_kr_theme_store.py tests/test_kr_source_collection_models.py
uv run ruff check trading_agent/kr_source_cycle.py tests/test_kr_source_cycle.py
uv run basedpyright trading_agent/kr_source_cycle.py tests/test_kr_source_cycle.py
```

Expected: all pass with zero type errors and warnings.

- [x] **Step 5: Commit the coordinator**

```bash
git add trading_agent/kr_source_cycle.py tests/test_kr_source_cycle.py
git commit -m "feat: finalize exact KR source cycles"
```

### Task 2: DB-Only Coverage CLI

**Files:**
- Create: `run_kr_source_cycle.py`
- Create: `tests/test_kr_source_cycle_cli.py`
- Reuse: `trading_agent/private_report.py`

- [x] **Step 1: Write failing CLI tests**

Cover `--help`, malformed cycle ID, all-success completion, missing source nonzero/no cycle, terminal-failed nonzero/preserved cycle, exact restart, mode-600 files and report redaction. Patch network and credential loaders to raise if called.

```python
runner = CliRunner()
result = runner.invoke(
    app,
    [
        "--collection-cycle-id",
        CYCLE_ID,
        "--database",
        str(database),
        "--output-dir",
        str(output),
    ],
)
assert result.exit_code == 0
assert (output / "kr_source_cycle_coverage.csv").stat().st_mode & 0o777 == 0o600
```

- [x] **Step 2: Run the CLI tests and verify RED**

Run: `uv run pytest -q tests/test_kr_source_cycle_cli.py`

Expected: collection fails because `run_kr_source_cycle.py` does not exist.

- [x] **Step 3: Implement strict CLI validation and aggregate reporting**

Expose only:

```text
--collection-cycle-id
--database
--output-dir
```

Write `kr_source_cycle_coverage.csv` with `source,status,record_count,failure_code` and `kr_source_cycle_summary_ko.md` with aggregate counts only through `write_private_report()`. Do not include cycle ID, paths, receipt IDs, hashes, raw payloads or provider text. Return 0 only for a complete cycle, and nonzero for missing, failed, invalid or conflicting evidence.

- [x] **Step 4: Run focused tests and static checks**

Run:

```bash
uv run pytest -q tests/test_kr_source_cycle.py tests/test_kr_source_cycle_cli.py
uv run ruff check run_kr_source_cycle.py tests/test_kr_source_cycle_cli.py
uv run basedpyright run_kr_source_cycle.py tests/test_kr_source_cycle_cli.py
```

Expected: all pass with zero type errors and warnings.

- [x] **Step 5: Commit the CLI**

```bash
git add run_kr_source_cycle.py tests/test_kr_source_cycle_cli.py
git commit -m "feat: add KR source cycle CLI"
```

### Task 3: Documentation, Checkpoint and End-to-End Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-15-kr-source-cycle-coordinator.md`
- Create: `docs/checkpoints/2026-07-15-kr-source-cycle-coordinator-ko.md`

- [x] **Step 1: Document the implemented boundary and usage**

Describe that the coordinator consumes existing terminal source runs, does not call providers, does not synthesize missing evidence, and cannot by itself make an eligible day while production news/KIS/volume adapters are absent. Add a DB-only CLI example and link the focused design, plan and checkpoint.

- [x] **Step 2: Run the full automated gates**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
```

Expected: all tests pass; Ruff passes; basedpyright reports zero errors and warnings.

- [x] **Step 3: Run manual CLI QA**

Run `./run_kr_source_cycle.py --help`, one malformed ID, one missing-source DB, one all-success four-run DB and the same all-success cycle again. Verify success creates one cycle, restart creates zero new rows, missing creates no cycle, reports are mode 600 and reports contain no cycle ID/path/hash/provider content.

- [x] **Step 4: Record the exact checkpoint**

The Korean checkpoint must list tests and CLI outcomes, state that actual OpenDART/news/KIS/Alpaca/LLM/external calls were zero, and identify production news read-only collection as the next milestone. Mark every completed plan checkbox.

- [x] **Step 5: Commit documentation**

```bash
git add README.md docs/checkpoints/2026-07-15-kr-source-cycle-coordinator-ko.md docs/superpowers/plans/2026-07-15-kr-source-cycle-coordinator.md
git commit -m "docs: record KR source cycle milestone"
```

### Task 4: Main Integration

**Files:**
- Verify all files changed by Tasks 1-3

- [x] **Step 1: Review the complete diff and repository status**

Run `git diff main...HEAD --check`, inspect every changed file, and confirm no credentials, provider payloads, account code or unrelated work entered the branch.

- [x] **Step 2: Re-run branch gates from a clean status**

Run full pytest, Ruff and basedpyright once more and preserve exact results.

- [x] **Step 3: Fast-forward verified commits to `main`**

Update local `main`, merge the feature branch without rewriting unrelated work, and rerun full gates plus CLI QA on merged `main`.

- [x] **Step 4: Push and verify remote `main`**

Push `origin/main`, confirm local `HEAD`, `origin/main` and `git ls-remote origin refs/heads/main` are identical, then remove only the owned temporary worktree and branch.
