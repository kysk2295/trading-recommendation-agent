# Grok Development Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repository-local CLI that validates a bounded task contract, creates an isolated Git worktree, launches a single constrained Grok worker, and reports whether its changes remain within the approved scope.

**Architecture:** `development_harness` is independent of `trading_agent` and has no provider, credential, broker, or execution imports. A strict Pydantic task contract feeds a pure Git/orchestration module; `run_grok_task.py` is the only CLI surface. The bootstrap implementation runs Grok once in its own native worktree; all later implementation work uses this CLI.

**Tech Stack:** Python 3.12, Pydantic 2, stdlib `subprocess`/`pathlib`/`json`, pytest, Ruff, basedpyright, installed Grok CLI.

---

## File Structure

- Create `development_harness/__init__.py`: declares the isolated development-harness package.
- Create `development_harness/task_contract.py`: frozen task-contract models and path/command validation.
- Create `development_harness/grok_task_runner.py`: Git preflight, worktree creation, constrained Grok command construction, safe report parsing, and changed-path validation.
- Create `run_grok_task.py`: argparse CLI that loads one JSON contract and writes only JSON to stdout.
- Create `tests/test_development_harness_task_contract.py`: contract model tests.
- Create `tests/test_grok_task_runner.py`: temporary-Git-repository orchestration tests.
- Create `tests/test_run_grok_task_cli.py`: help, malformed contract, and dry-run CLI tests.
- Modify `pyproject.toml`: include the harness package and CLI in basedpyright coverage.
- Modify `README.md`: add a short developer-only harness section that explicitly says it is not a trading runtime.
- Create `docs/checkpoints/2026-07-18-grok-development-harness-ko.md`: tested operating contract and bootstrap limitation.

### Task 1: Define the Immutable Task Contract

**Files:**
- Create: `development_harness/__init__.py`
- Create: `development_harness/task_contract.py`
- Test: `tests/test_development_harness_task_contract.py`

- [ ] **Step 1: Write failing contract tests**

```python
from development_harness.task_contract import GrokTaskContract


def test_contract_accepts_relative_unique_allowed_paths() -> None:
    contract = GrokTaskContract.model_validate(_valid_contract())
    assert contract.allowed_paths == ("development_harness/task_contract.py", "tests/test_task_contract.py")


@pytest.mark.parametrize(
    "path",
    ("/tmp/outside.py", "../outside.py", ".hermes/state.json", ".git/config"),
)
def test_contract_rejects_unsafe_allowed_path(path: str) -> None:
    payload = _valid_contract()
    payload["allowed_paths"] = [path]
    with pytest.raises(ValueError, match="invalid Grok task contract"):
        GrokTaskContract.model_validate(payload)
```

- [ ] **Step 2: Run the contract tests and verify they fail**

Run: `uv run pytest tests/test_development_harness_task_contract.py -q`

Expected: FAIL because `development_harness` does not exist.

- [ ] **Step 3: Implement strict contract models**

```python
class GrokTaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    task_id: str
    base_commit: str
    objective: str
    allowed_paths: tuple[str, ...]
    required_commands: tuple[str, ...]
    manual_qa_commands: tuple[str, ...]
    max_turns: Annotated[int, Field(ge=1, le=24)] = 12
```

Reject non-lowercase task IDs, non-40-hex commits, empty/duplicate paths, absolute or traversal paths, protected roots `.git`, `.grok`, `.hermes`, secrets/environment filenames, and empty/unsafe command strings. Preserve only generic validation messages; contract errors must not echo objective or command content.

- [ ] **Step 4: Run the contract tests and verify they pass**

Run: `uv run pytest tests/test_development_harness_task_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the contract slice in the worker worktree**

```bash
git add development_harness/__init__.py development_harness/task_contract.py tests/test_development_harness_task_contract.py
git commit -m "feat: add Grok task contract"
```

### Task 2: Add Git Preflight and Scoped Worker Invocation

**Files:**
- Create: `development_harness/grok_task_runner.py`
- Test: `tests/test_grok_task_runner.py`

- [ ] **Step 1: Write failing orchestration tests using a temporary Git repository**

```python
def test_prepare_dry_run_creates_no_worktree_and_returns_planned_command(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    plan = prepare_grok_task(_contract(repo), repo=repo, worktree_root=tmp_path / "workers", dry_run=True)
    assert plan.worktree_path == tmp_path / "workers" / "m4-replay-input"
    assert "--sandbox" in plan.command
    assert not plan.worktree_path.exists()


def test_changed_paths_rejects_path_outside_contract() -> None:
    with pytest.raises(GrokTaskRunnerError, match="worker changed a path outside the contract"):
        assert_changed_paths_allowed(("README.md",), ("development_harness/task_contract.py",))
```

- [ ] **Step 2: Run the runner tests and verify they fail**

Run: `uv run pytest tests/test_grok_task_runner.py -q`

Expected: FAIL because `grok_task_runner` does not exist.

- [ ] **Step 3: Implement fail-closed preflight and command construction**

```python
def build_grok_command(
    contract: GrokTaskContract,
    *,
    grok_binary: str,
    worktree: Path,
    prompt: str,
) -> tuple[str, ...]:
    return (
        grok_binary, "--cwd", str(worktree), "-p", prompt, "--output-format", "json",
        "--max-turns", str(contract.max_turns), "--no-subagents", "--disable-web-search",
        "--sandbox", "strict", "--disallowed-tools", "web_search,web_fetch,Agent",
        "--deny", "Bash(git push*)", "--deny", "Bash(curl *)", "--deny", "Bash(wget *)",
    )
```

Use the actual supported one-shot form `grok -p <prompt>` rather than shell interpolation: invoke `subprocess.run` with an argument tuple, `cwd=worktree`, a bounded timeout, and `capture_output=True`. The prompt must include objective, exact allowed paths, required commands, the full product safety boundary, and a machine-readable summary request. Preflight must require a matching HEAD, accept only a clean checkout plus the pre-existing `?? .hermes/` entry, reject an existing branch/worktree destination, and never remove any worktree. Parse `git status --porcelain -z` and reject every worker-added or modified path outside `allowed_paths`.

- [ ] **Step 4: Run the runner tests and verify they pass**

Run: `uv run pytest tests/test_grok_task_runner.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the runner slice in the worker worktree**

```bash
git add development_harness/grok_task_runner.py tests/test_grok_task_runner.py
git commit -m "feat: add scoped Grok task runner"
```

### Task 3: Expose a Safe CLI and Fixture Contract

**Files:**
- Create: `run_grok_task.py`
- Test: `tests/test_run_grok_task_cli.py`

- [ ] **Step 1: Write failing CLI tests**

```python
def test_cli_dry_run_emits_safe_plan(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    contract = _write_contract(tmp_path, base_commit=_head(repo))
    result = _run_cli("--contract", str(contract), "--worktree-root", str(tmp_path / "workers"), "--dry-run", cwd=repo)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "planned"
    assert "objective" not in result.stdout


def test_cli_rejects_invalid_contract_without_echoing_contents(tmp_path: Path) -> None:
    contract = tmp_path / "bad.json"
    contract.write_text('{"unsafe": "secret-like-value"}', encoding="utf-8")
    result = _run_cli("--contract", str(contract), "--dry-run")
    assert result.returncode == 1
    assert "secret-like-value" not in result.stderr
```

- [ ] **Step 2: Run the CLI tests and verify they fail**

Run: `uv run pytest tests/test_run_grok_task_cli.py -q`

Expected: FAIL because `run_grok_task.py` does not exist.

- [ ] **Step 3: Implement the argparse CLI**

```python
parser.add_argument("--contract", type=Path, required=True)
parser.add_argument("--worktree-root", type=Path, required=True)
parser.add_argument("--grok-binary", default="grok")
parser.add_argument("--dry-run", action="store_true")
```

Load UTF-8 JSON with a size cap, validate through `GrokTaskContract`, and emit only a safe report containing `schema_version`, `task_id`, `base_commit`, `status`, `worktree_id`, `changed_paths`, and `worker_exit_code`. Do not expose task objective, worker stdout/stderr, command arguments, absolute paths, prompt text, or credentials. `--dry-run` must not call Grok or add a Git worktree.

- [ ] **Step 4: Run CLI tests and manual CLI QA**

Run:

```bash
uv run pytest tests/test_run_grok_task_cli.py -q
uv run python run_grok_task.py --help
uv run python run_grok_task.py --contract /does-not-exist --worktree-root /tmp/grok-harness --dry-run
uv run python run_grok_task.py --contract examples/development-harness/fixture-task-contract.json --worktree-root /tmp/grok-harness --dry-run
```

Expected: tests pass; help exits `0`; missing contract exits `1`; fixture dry-run exits `0` and creates no worktree.

- [ ] **Step 5: Commit the CLI slice in the worker worktree**

```bash
git add run_grok_task.py examples/development-harness/fixture-task-contract.json tests/test_run_grok_task_cli.py
git commit -m "feat: add Grok task harness CLI"
```

### Task 4: Document and Verify the Harness

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Create: `docs/checkpoints/2026-07-18-grok-development-harness-ko.md`

- [ ] **Step 1: Add a failing static-coverage assertion**

```python
def test_pyproject_includes_harness_in_basedpyright() -> None:
    pyproject = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"development_harness"' in pyproject
    assert '"run_grok_task.py"' in pyproject
```

- [ ] **Step 2: Run the assertion and verify it fails**

Run: `uv run pytest tests/test_run_grok_task_cli.py::test_pyproject_includes_harness_in_basedpyright -q`

Expected: FAIL before the configuration update.

- [ ] **Step 3: Make the configuration and documentation changes**

Add `development_harness` and `run_grok_task.py` to the existing basedpyright `include` list. README must identify this as a developer-only orchestration tool, require Codex review before `main` integration, and state that it has no market-data, credential, broker, or Paper mutation authority. The checkpoint must list the native-Grok bootstrap exception, the retained-worktree rule, the exact validation commands, and the fact that no provider or broker call was made.

- [ ] **Step 4: Run full verification**

Run:

```bash
uv run pytest tests/test_development_harness_task_contract.py tests/test_grok_task_runner.py tests/test_run_grok_task_cli.py -q
uv run pytest -q
uv run ruff check .
uv run basedpyright
git diff --check
```

Expected: all tests pass, Ruff is clean, basedpyright reports `0 errors, 0 warnings, 0 notes`, and `git diff --check` exits `0`.

- [ ] **Step 5: Commit the documentation and verification slice in the worker worktree**

```bash
git add pyproject.toml README.md docs/checkpoints/2026-07-18-grok-development-harness-ko.md tests/test_run_grok_task_cli.py
git commit -m "docs: record Grok harness operation"
```

### Task 5: Codex Reconciliation and Main Integration

**Files:**
- Review: all worker changes only

- [ ] **Step 1: Inspect the worker branch without trusting its summary**

Run:

```bash
git diff --name-status main...grok/grok-development-harness
git diff --check main...grok/grok-development-harness
git log --oneline main..grok/grok-development-harness
```

Reject any path outside the plan, secret-like literal, `trading_agent` provider/credential/broker/execution import, network command, `git push`, or automated `main` mutation.

- [ ] **Step 2: Run Codex's independent verification from the worker worktree**

Run the full Task 4 verification commands without reusing the worker result. Also run the CLI help, malformed contract, and fixture dry-run manually.

- [ ] **Step 3: Integrate only an accepted worker commit**

```bash
git switch main
git cherry-pick <accepted-worker-commit>
git push origin main
```

Do not stage `.hermes/`. Leave the worker worktree intact after integration until the result has been reported.

## Plan Self-Review

- Scope coverage: contract validation, isolated launch, changed-path checking, CLI, TDD, documentation, and independent review each have an implementation task.
- No-placeholder scan: task paths, commands, failure expectations, and acceptance conditions are explicit.
- Type consistency: every task uses `GrokTaskContract`, `prepare_grok_task`, `assert_changed_paths_allowed`, and the `run_grok_task.py` CLI named in the file structure.
- Bootstrap boundary: only the initial harness implementation uses Grok's native worktree support; no later feature bypasses the harness.
