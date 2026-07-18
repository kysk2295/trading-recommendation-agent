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
still equals `contract.base_commit`; Git refs/reflog/object inventory (including
symlink entries under `.git/objects`) and local Git control-path metadata are
unchanged (including commit-then-reset, local config edits, and hook
create/replace); logical Git index entries and flags from
`git ls-files --stage -v -z` are unchanged so `assume-unchanged` /
`skip-worktree` cannot hide out-of-scope edits; and pre-existing user-owned
`.hermes` / `.omo` plus ignored files and directories (including empty ignored
directories) are unchanged by immutable metadata tuples
`(mode, uid, size, mtime_ns, ctime_ns)` only—file contents are never read for
snapshots. Control paths include `.git/HEAD`, `.git/config`, optional
`.git/config.worktree`, `.git/packed-refs`, `.git/info/exclude`, and every
entry under `.git/hooks` without following symlinks. The binary `.git/index`
blob is not hashed (ordinary status refresh may rewrite it); only the stable
logical index listing is fingerprinted. The repository path and every allowed
path must not include symlink components before or after the worker. The
harness then compares every Git-changed path against the contract allow-list
and fails closed on any extra path or worker commit. Timeout and OSError paths
use the same enforcement so out-of-contract edits cannot hide behind a failed
process.

The worker process itself receives the same cache-disabled environment helper
used for verification (`PYTHONDONTWRITEBYTECODE`, and `PYTEST_ADDOPTS` set
exactly to `-p no:cacheprovider`, discarding inherited pytest options
fail-closed). Ruff cache is disabled by injecting the documented
`ruff check --no-cache` flag into both the commands shown to the worker and the
independent offline re-run (contract input may omit it). Before `completed`, the
harness independently re-runs every required and manual QA command with
`uv run --offline` under that environment and those rewritten commands.
Worker-claimed verification is never enough. Workspace snapshot fingerprint
logic (index/Git DB/user-owned/ignored metadata) lives in
`development_harness/grok_workspace_fingerprint.py`; path-safety preflight
remains in `grok_workspace_guard.py` with stable public re-exports.

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
- Git refs/reflog/object (including object-store symlinks) and local
  control-path metadata change detection, including commit-reset, config
  edits, and hook create/replace;
- logical index entry/flag fingerprinting via `git ls-files --stage -v -z`;
- ignored directory inventory including empty ignored directories;
- rejection of symlink components on the repository path and allowed paths;
- offline verification with bytecode/pytest/Ruff caches disabled;
- dry-run planning without invoking Grok or changing Git state;
- changed-path allow-list comparison, including rename old/new sides;
- summary path/verification/concern contract-safe enum matching;
- structured summary parsing from `structuredOutput` only with JSON depth bound;
- process-group kill on timeout and stdout size bound;
- independent offline re-run of required and manual QA before completed;
- a safe failure when the Grok binary is absent, times out, or returns nonzero,
  with allow-list enforcement on timeout/OSError paths;
- CLI `--help`, invalid input, and dry-run happy path without `--worktree-root`.

Codex independently reviews each Grok diff for plan compliance, unintended
scope, secret exposure, provider/broker imports, test quality, and regressions.
Acceptance requires targeted tests, full `pytest`, Ruff, basedpyright, and
the task-specific manual QA specified by the contract.

## Status Notes

A real in-place Grok worker run and the subsequent review hardening completed
successfully with `structuredOutput`-only consumption. An earlier bootstrap
HTTP `402` spending-limit stop remains historical context only; it is not the
current limitation of this harness.
