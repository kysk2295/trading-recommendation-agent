# Grok Development Harness Design

- Status: approved for implementation
- Date: 2026-07-18
- Scope: repository-local development orchestration only

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
- It does not grant Grok authority to merge, push, or modify `main`.
- It does not rewrite historical commits or alter user-owned uncommitted files.

## Chosen Architecture

The harness is a small repository-local Python CLI with JSON task contracts.
Each contract is immutable input to one Grok worker attempt and declares:

- the base Git commit and task identifier;
- exact allowed repository-relative paths;
- prohibited paths and side effects;
- required test, lint, type-check, and manual CLI verification commands;
- the expected worker summary format.

The CLI validates the contract before invoking Grok. It creates an isolated
Git worktree on a unique local branch, writes the bounded prompt there, and
starts the installed `grok agent` command from that worktree. Grok may create
a local commit only in that worktree. It must not push, modify `main`, read a
credential file, call a network provider, or make a Paper mutation.

The harness captures only safe orchestration metadata: task ID, base commit,
worktree path, worker exit status, changed paths, and commands requested by
the contract. It never captures credentials, raw provider payloads, or account
identifiers. The worker transcript remains local and is not committed.

## Workflow

```text
Codex writes plan and task contract
  -> harness validates contract and creates worktree
  -> Grok implements one bounded task with TDD
  -> harness reports the local diff and worker status
  -> Codex checks contract compliance and reviews the diff
  -> Codex independently runs verification
  -> Codex cherry-picks or recreates the accepted change on main
  -> Codex commits and pushes main
```

The worker result is never an automatic merge. A failed worker, changed path
outside the allow-list, dirty base checkout, missing expected summary, or
verification failure rejects the attempt. The isolated worktree is retained for
inspection until Codex explicitly removes it.

## Existing-Code Protection

Existing repository files are not replaced wholesale. A task may change an
existing file only when that exact path is in `allowed_paths`; otherwise the
harness rejects the result. Existing Git history remains intact and accepted
work appears as a new, small commit.

Before every run the harness records the `main` status. It refuses to use a
dirty checkout except for an explicit, pre-existing ignored or allow-listed
user-owned path. For this repository `.hermes/` is not a harness artifact,
must never be staged, and remains outside every worker allow-list.

## Safety Model

All worker prompts repeat the project product boundary: Alpaca live trading is
forbidden, Alpaca Paper mutation is outside harness scope, KIS and LS are
read-only, and secrets must not be read, emitted, or committed. The initial
harness itself has no imports from `trading_agent` provider, credential, broker,
or execution modules.

The default task policy forbids shell commands that contact a provider or
write outside the isolated worktree. Future task contracts may loosen neither
the live-trading boundary nor credential boundary.

## Verification

The implementation must use TDD and cover at least:

- valid and invalid task-contract parsing;
- rejection of absolute paths, traversal paths, duplicate paths, empty allow
  lists, and prohibited overlap;
- dirty-checkout behavior and preservation of `.hermes/`;
- dry-run worktree planning without running Grok;
- changed-path allow-list comparison;
- a safe failure when the Grok binary is absent or returns a nonzero status;
- CLI `--help`, invalid input, and dry-run happy path.

Codex independently reviews each Grok diff for plan compliance, unintended
scope, secret exposure, provider/broker imports, test quality, and regressions.
Acceptance requires targeted tests, full `pytest`, Ruff, basedpyright, and
the task-specific manual QA specified by the contract.

## First Use

The first real worker task will be a small M4 contract-only increment, not a
network collector or execution change. It will bind a verified canonical replay
result to a research input identity. This proves the harness before it is used
for an always-on US market-data component.
