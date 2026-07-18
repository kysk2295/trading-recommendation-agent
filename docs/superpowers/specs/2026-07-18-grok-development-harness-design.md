# Grok Development Harness Design

- Status: approved for implementation
- Date: 2026-07-18
- Scope: repository-local development orchestration only
- Update: hardened in-place non-interactive bounded worker
- Current state: real in-place Grok worker with review hardening completed;
  summary consumption is `structuredOutput` only

## Goal

Use Grok for tightly scoped implementation work while Codex remains the planner,
independent reviewer, verifier, and the only actor that integrates changes into
`main`.

The harness must reduce repeated context and prevent an implementation worker
from changing unrelated code, publishing changes, reading credentials, or
touching the trading runtime.

## Non-goals

- It does not run a market-data collector, a backtest, or a broker operation.
- It does not load provider credentials or inspect their values.
- It does not replace the existing test, lint, type-check, or Git workflow.
- It does not grant Grok authority to merge, push, or commit `main` history.
- It does not rewrite historical commits or alter user-owned uncommitted files
  outside the preflight exception for pre-existing `.hermes/` / `.omo/`.
- It does not create a Git worktree, branch, or clone for the worker.
- The in-place worker may edit allow-listed files in the current main working
  tree, but it must not commit or push main history.
- It does not provide an OS sandbox. Credential reads, network calls, push,
  external writes, and detached `setsid` descendants remain residual risk.

## Chosen Architecture

The harness is a small repository-local Python CLI with JSON task contracts.
Each contract is immutable input to one Grok worker attempt and declares:

- the base Git commit and task identifier;
- exact allowed repository-relative paths;
- prohibited paths and side effects;
- required test, lint, type-check, and manual CLI verification commands;
- the expected worker summary format.

The CLI validates the contract before invoking Grok. It runs an in-place,
non-interactive worker against the exact current repository root on `main`
only. Linked worktrees and symlink repository roots are rejected. The generated
Grok command uses:

- `--cwd` on the repository root;
- `--always-approve`;
- `--permission-mode bypassPermissions`;
- `-p` single-turn prompt;
- `--output-format json` with a strict `--json-schema` for
  `changed_files`, `verification`, and `concerns`;
- `--no-plan`, `--no-subagents`, `--disable-web-search`, `--no-memory`;
- `--max-turns` from the contract.

The command must not use `--sandbox strict` and must not create a branch or
worktree. The worker may edit allow-listed files in the main working tree but
must not commit or push main history. Worker processes start in a new process
group with stdout redirected to a temporary regular file that is polled for
size and deadline; stderr goes to `DEVNULL`. Timeout or oversize kills the
process group and surviving descendants; the result never returns stderr.

After execution the harness verifies a workspace snapshot: `git rev-parse HEAD`
still equals `contract.base_commit`; Git refs/reflog plus a metadata-only full
`.git` topology/state inventory are unchanged (including commit-then-reset,
local config edits, and hook create/replace); logical Git index entries and
flags from `git ls-files --stage -v -z` are unchanged so `assume-unchanged` /
`skip-worktree` cannot hide out-of-scope edits; pre-existing user-owned
`.hermes` / `.omo` plus ignored files and directories (including empty ignored
directories) are unchanged by immutable metadata tuples
`(mode, uid, size, mtime_ns, ctime_ns)` only—file contents are never read for
snapshots; and every other visible worktree entry is fingerprinted except exact
allowed paths, required parents of allowed paths, ignored paths, and user-owned
roots. The binary `.git/index` blob is not fingerprinted (ordinary status
refresh may rewrite it). Full `.git` inventory rejects `index.lock`,
`sharedindex.*`, in-progress operation state, every internal symlink, and a
symlinked objects root. `.git/index` must be a current-user-owned regular file
with `st_nlink=1`. Every walk/stat/enumeration error fails closed. Unignored
empty directories outside user-owned roots are inventoried separately;
create/delete is fail-closed except for missing parent directories required by
allowed file paths. Preflight also rejects **pre-existing** `assume-unchanged` /
`skip-worktree` flags and sparse-checkout masking, and forces untracked
reporting with `git status --porcelain=v1 -z --untracked-files=all` so
`status.showUntrackedFiles=no` cannot hide dirty checkouts. Immediately before
worker launch the harness revalidates a clean snapshot and repository root;
before any post-worker Git inventory and again after independent verification
(success, nonzero, timeout, or side effect) it revalidates repository
symlink / `.git` topology, sparse-checkout absence, empty-directory inventory,
visible worktree metadata, and allow-listed changed paths. The repository path
and every allowed path must not include symlink components before or after the
worker. The harness then compares every Git-changed path against the contract
allow-list and fails closed on any extra path or worker commit. Timeout and
OSError paths use the same enforcement so out-of-contract edits cannot hide
behind a failed process.

Every harness Git subprocess, the worker, and independent verification strip
every ambient environment key whose name starts with `GIT_` (fail-closed; not an
allow-list) via `development_harness/grok_process_env.py`. Preflight and
post-worker topology checks also reject a non-regular, non-owned, multi-link, or
symlinked `.git/index` and require the effective
`git rev-parse --git-path index` path to resolve exactly to the
repository-owned `.git/index`. The worker process itself receives the same
cache-disabled environment helper used for verification
(`PYTHONDONTWRITEBYTECODE`, and `PYTEST_ADDOPTS` set exactly to
`-p no:cacheprovider`, discarding inherited pytest options fail-closed). Ruff
cache is disabled by injecting the documented `ruff check --no-cache` flag into
both the commands shown to the worker and the independent offline re-run
(contract input may omit it). Before `completed`, the harness independently
re-runs every required and manual QA command with `uv run --offline` under that
prepared environment, each inside a new process group
(`grok_verification_process.py`) that receives the caller-prepared env, strips
`GIT_*` only, and reaps ordinary background descendants on success, failure, and
timeout. Worker-claimed verification is never enough. Task contracts require
pytest, Ruff, and basedpyright in `required_commands` plus meaningful
task-specific manual QA using a safe repository-relative Python CLI `--help`
command, not `python -c pass`. Snapshot orchestration lives in
`grok_workspace_fingerprint.py` with full `.git` topology helpers in
`grok_git_control.py`, path metadata helpers in `grok_path_metadata.py`, and
visible worktree/empty-directory helpers in `grok_worktree_metadata.py`;
command/prompt construction lives in `grok_command.py`; path-safety preflight
remains in `grok_workspace_guard.py` with stable public re-exports; repository
root and index topology checks live in `grok_worktree_topology.py`.

The harness captures only safe orchestration metadata: task ID, base commit,
worker exit status, changed paths, and the bounded structured summary parsed
from top-level `structuredOutput`. It never depends on `text` being a single
JSON document, and never exposes raw stdout/stderr, the prompt, the objective,
absolute paths, credentials, or provider payloads. The worker transcript
remains local and is not committed.

Summary `changed_files` may appear in any order, but must contain exactly the
same unique paths as the Git-derived `changed_paths`, each path must be in the
contract allow-list, `verification` must equal the exact unique set of
required+manual commands (not a subset, empty list, or duplicates), and
`concerns` must come from a small fixed enum. Omissions, extras, duplicates,
and unsafe tokens produce `worker_failed` without exposing the summary.
Contract commands are validated strictly (pytest/ruff/basedpyright and a
narrow set of python forms only; `compileall` is not permitted).

Contract validation also bounds path counts/lengths and command counts/lengths.

## Workflow

```text
Codex writes plan and task contract
  -> harness validates contract and preflights main checkout
  -> Grok implements one bounded task in-place with TDD
  -> harness checks snapshot, paths, summary enums
  -> harness independently re-runs required and manual QA offline
  -> harness reports changed paths and structured summary
  -> Codex checks contract compliance and reviews the diff
  -> Codex independently runs verification
  -> Codex commits accepted work on main
  -> Codex pushes main only after review
```

The worker result is never an automatic merge. A failed worker, changed path
outside the allow-list, dirty base checkout, missing expected summary, snapshot
drift, verification failure, or failed offline re-run rejects the attempt.

## Existing-Code Protection

Existing repository files are not replaced wholesale. A task may change an
existing file only when that exact path is in `allowed_paths`; otherwise the
harness rejects the result. Existing Git history remains intact and accepted
work appears only when Codex creates a new, small commit.

Before every run the harness records the checkout status and a workspace
snapshot. It requires the exact contract base commit on `main` and refuses to
proceed unless the checkout is clean or contains only pre-existing untracked
user-owned `.hermes/` and/or `.omo/` state. Those paths are not harness
artifacts, must never be staged, remain outside every worker allow-list, and
must not change metadata or content under the worker.

## Safety Model

All worker prompts repeat the project product boundary: Alpaca live trading is
forbidden, Alpaca Paper mutation is outside harness scope, KIS and LS are
read-only, and secrets must not be read, emitted, or committed. The harness
itself has no imports from `trading_agent` provider, credential, broker, or
execution modules.

The worker is bounded by contract allow-lists, required verification commands,
a strict JSON summary schema, disabled web search/subagents/memory/plan mode,
process-group timeout kill, workspace snapshots, offline independent command
re-run, and fail-closed changed-path comparison. Future task contracts may
loosen neither the live-trading boundary nor the credential boundary.

### Residual risk without OS sandbox

Because the harness preserves direct main execution with
`bypassPermissions` and does not enable `--sandbox strict`, the following remain
prompt-and-contract residual risk only (not OS-enforced):

- credential reads;
- network calls;
- `git push` and other remote publication;
- writes outside the repository;
- worker descendants that call `setsid` (or equivalent) and detach from the
  process group, so timeout/oversize group kill cannot reliably reap them.

Post-conditions catch in-repo and Git-database damage, but they are not an OS
sandbox.

## Verification

The implementation must use TDD and cover at least:

- valid and invalid task-contract parsing with count/length bounds;
- rejection of absolute paths, traversal paths, duplicate paths, empty allow
  lists, `.omo` / `.hermes`, and prohibited overlap;
- dirty-checkout behavior and preservation of `.hermes/` / `.omo/`;
- main-only preflight and rejection of linked worktrees/symlink roots;
- Git refs/reflog and full metadata-only `.git` topology/state inventory change
  detection (binary index excluded; index.lock/sharedindex/operation
  state/internal symlink/symlinked objects root rejected), including
  commit-reset, config edits, and hook create/replace;
- logical index entry/flag fingerprinting via `git ls-files --stage -v -z`;
- rejection of pre-existing assume-unchanged/skip-worktree and sparse masking;
- `.git/index` current-user-owned regular single-link topology;
- ignored directory inventory including empty ignored directories;
- visible worktree entry metadata fingerprint excluding allowed paths/parents,
  ignored paths, and user-owned roots;
- fail-closed walk/stat/enumeration errors;
- contract requirement for pytest + Ruff + basedpyright and meaningful manual QA;
- rejection of symlink components on the repository path and allowed paths;
- sanitization of ambient Git routing variables for harness Git/worker/verification;
- launch-time clean snapshot/root revalidation and post-worker topology checks;
- offline verification with bytecode/pytest/Ruff caches disabled;
- independent verification process-group isolation and ordinary descendant reap;
- dry-run planning without invoking Grok or changing Git state;
- changed-path allow-list comparison, including rename old/new sides;
- summary path/verification/concern contract-safe enum matching;
- structured summary parsing from `structuredOutput` only with JSON depth bound;
- process-group kill on timeout and stdout size bound;
- fingerprinting of `.git/shallow`, `.git/info/grafts`, and
  `.git/info/sparse-checkout` control paths;
- forced untracked status reporting despite `status.showUntrackedFiles=no`;
- unignored empty-directory create/delete detection with allowed-parent exception;
- post-workspace validation after independent verification success, nonzero,
  timeout, or side effect;
- independent offline re-run of required and manual QA before completed;
- a safe failure when the Grok binary is absent, times out, or returns nonzero,
  with allow-list enforcement on timeout/OSError paths;
- CLI `--help`, invalid input, and dry-run happy path without `--worktree-root`.

Codex independently reviews each Grok diff for plan compliance, unintended
scope, secret exposure, provider/broker imports, test quality, and regressions.
Acceptance requires targeted tests, full `pytest`, Ruff, basedpyright, and
the task-specific manual QA specified by the contract. The final full-suite
baseline is **2129 passed** from Codex's full-suite rerun.
Local Codex checkpoint commits are for exact-SHA review only; workers never
commit; only remote push waits for all reviewers PASS.

## Status Notes

A real in-place Grok worker run and the subsequent review hardening completed
successfully with `structuredOutput`-only consumption. Final review blockers
and local harness invariants (pre-existing index/sparse masking, fail-closed
`GIT_*` stripping, launch/post-worker revalidation including current-user regular
single-link repo-owned index, full `.git` topology/state inventory with
lock/sharedindex/operation/symlink rejects, visible worktree metadata
fingerprints, fail-closed walk/stat/enumeration, pytest/Ruff/basedpyright plus
meaningful manual QA contracts, verification process groups, module splits under
250 pure LOC, and release checklist) are closed while preserving the documented
no-OS-sandbox and detached-`setsid` residuals. Local postcheck bypasses are also
closed: forced untracked reporting, always-on post-verification workspace
validation, sparse-checkout fingerprint/reject after worker and verification,
and unignored empty-directory inventory with allowed-parent exception only. An
earlier bootstrap HTTP `402` spending-limit stop remains historical context only;
it is not the current limitation of this harness.
