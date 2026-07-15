# Paper Protective OCO Resize Implementation Plan

**Goal:** Safely resize protection after additional entry fills by staging OCO cancel, current-epoch terminal reconciliation, and a uniquely identified replacement OCO without ever issuing cancel and replacement POST in one operating-session call.

**Architecture:** Extend the existing append-only mutation ledger with a distinct `cancel_protective_oco` operation tied to the immutable source OCO plan and a previously reconciled parent broker order. A pure lifecycle planner consumes the current open OCO inventory plus current-epoch recent nested OCO history, proves any protective-leg fills against the exact broker position, and returns one of exact coverage, cancel-for-resize, new OCO submission, no position, or fail-closed. Replacement client IDs are deterministic from the parent intent, predecessor plan key, and desired plan values so every replacement is unique and restart-stable.

**Safety Boundary:** The public operating session still requires `PaperMutationArm`. Protection mutations additionally require a current Alpaca clock, local regular-session agreement, current WSS/REST receipts, and must stop before the EOD flatten window. Real Alpaca Paper POST/DELETE remains disabled while the market is closed; verification uses fake brokers and fixtures.

---

### Task 1: Define The Pure OCO Lifecycle

- [x] Add failing tests for exact existing coverage, additional-fill cancel, pending-cancel wait, terminal cancel replacement, full exit noop, and both-leg fill fail-closed.
- [x] Generate a distinct deterministic client order ID for every predecessor plan while preserving idempotency for the same desired replacement.
- [x] Accept broker-adjusted remaining OCO coverage after one leg partially fills when both remaining legs exactly cover the current position.
- [x] Keep all unmatched, duplicate, non-finite, and position-unexplained states fail-closed.

### Task 2: Add Append-Only Protective Cancel Mutation

- [x] Bump the execution ledger to schema v9 and migrate v8 rows without rewriting immutable evidence.
- [x] Add `cancel_protective_oco` intent validation, source validation against a reconciled parent OCO leg, deterministic mutation keying, and reader round trips.
- [x] Execute the existing Paper-only DELETE adapter through a dedicated executor method.
- [x] Recover timeout/restart by exact broker order ID and never resend while ambiguous or already acknowledged.

### Task 3: Wire The Staged Operating State Machine

- [x] Add a current regular-session protection mutation gate with no entry-bar dependency.
- [x] Return a typed cancel-stage execution and stop before replacement POST.
- [x] On a later invocation, use the newly reconciled exact position and historical leg fills to submit only the replacement plan.
- [x] Map post-mutation reconciliation failure to the existing typed exit-2 error boundary.

### Task 4: Update The Protective OCO Smoke CLI

- [x] Report cancel-for-resize as `incomplete` with exit 2 and no broker IDs, request IDs, mutation keys, account fingerprint, credentials, or raw payload.
- [x] Report exact coverage/no remaining position as a no-op and replacement POST ACK only after its own current-epoch reconciliation.
- [x] Add bad-arm, closed-session, cancel timeout, replacement timeout, restart recovery, and cancel/fill race tests.

### Task 5: Verify And Checkpoint

- [x] Run focused and full pytest, `uv run ruff check .`, changed-file format checks, `uv run basedpyright`, and `git diff --check`.
- [x] Run `--help`, malformed input, and fake staged cancel/replacement CLI QA while confirming the NYSE session is closed before suppressing real mutation.
- [x] Update README, `CODEX_START_HERE.md`, and a Korean checkpoint with exact test counts and actual Paper POST/DELETE count.
- [ ] Commit and push one complete checkpoint, then verify clean `0 0` origin alignment.
