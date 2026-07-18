# M4 US Equity Always-On Data Vertical

**Owner:** Codex design; Grok implements bounded tasks; Codex reviews and integrates.

## Purpose

Milestone 4 turns the completed M2/M3 contracts into a US read-only data
vertical. It must make a live candidate, its features, and any later
opportunity or conditional signal traceable to the exact verified dataset that
was available at evaluation time. It does not create an order path, access an
account, or broaden any Paper risk limit.

## Fixed Design Decisions

1. **Provenance precedes features.** Every research input begins with an
   exact `CanonicalDatasetReplay` result. A deterministic
   `ResearchInputIdentity` binds that verified result to one research scope;
   it never accepts a path, raw payload, provider client, or replay-like
   substitute.
2. **Completed events only.** The shared indicator kernel consumes normalized,
   completed bar/trade inputs. It cannot fill a gap with a current quote or use
   an in-progress bar to publish a feature snapshot.
3. **Broad-to-narrow is declarative.** A broad scanner proposes desired
   symbols. A separate pure subscription policy computes the bounded desired
   quote/trade set, eviction, and cooldown. Provider adapters only enact that
   desired state and have no strategy or recommendation authority.
4. **Snapshots are append-only evidence.** A feature snapshot records its
   `ResearchInputIdentity`, source event range, indicator semantic version,
   calculation time, freshness outcome, and quality/gap state. A failed or
   incomplete input creates a blocked result, never an inferred value.
5. **Runtime recovery is observable.** Restart, subscription failure, and
   event gaps become explicit status/evidence records. Recovery may resume
   collection but cannot retroactively publish a missing snapshot.
6. **Existing US opportunity and trade-signal behavior remains conditional.**
   M4 only adds an evidence reference gate. It does not change strategy
   thresholds, recommendation semantics, or Paper execution authority.

## Delivery Slices

### M4.0: Replay-Bound Research Input Identity

Add a pure immutable contract:

- accepts only an exact verified `CanonicalDatasetReplay` instance and a
  bounded scope identifier;
- stores dataset and raw-manifest lineage plus a deterministic SHA-256 identity;
- rejects malformed scopes, subclasses, and lookalike objects with one
  sanitized error;
- performs no filesystem or network I/O.

**Acceptance:** deterministic equality, scope isolation, hostile-input
rejection, focused tests, Ruff, basedpyright, and a module compile QA.

### M4.1: Completed-Event Indicator Kernel

Add pure typed inputs and a versioned kernel for completed one-minute bars and
trades. It produces VWAP, ATR, RSI, MACD, RVOL, breakout, and freshness/gap
flags without provider-specific indicator values.

**Acceptance:** same ordered events produce byte-stable snapshots; an
incomplete sequence is blocked; same-bar stop/target collision remains outside
this kernel and retains the existing stop-first execution rule.

**Checkpoint (2026-07-18): Complete.** `CompletedMinuteBar` and the pure
`IntradayFeatureSnapshot` kernel now bind every result to a
`ResearchInputIdentity`. It accepts only contiguous completed one-minute bars,
blocks gaps, stale data, insufficient history, and malformed bar fields with
null indicators, and computes deterministic Decimal VWAP, Wilder ATR/RSI,
MACD, RVOL, and strict breakout evidence for ready snapshots. Verification:
24 focused tests, the full 2013-test suite, Ruff, and basedpyright all pass.

### M4.2: Candidate and Dynamic Subscription Policy

Add a pure policy that consumes broad scanner candidates and emits a bounded
desired US quote/trade subscription set. It has stable ranking, explicit
capacity, deterministic eviction, cooldown, and no provider import.

**Acceptance:** a candidate cannot subscribe itself; ties and capacity pressure
are deterministic; stale or closed-session input has no desired subscription.

### M4.3: Read-Only Runtime Supervisor

Introduce provider-neutral read-only adapter and supervisor contracts. The
supervisor owns a single writer for raw receipt projection, records reconnect
and gap evidence, and passes only completed normalized events to M4.1.

**Acceptance:** fixture E2E proves normal cycle, restart recovery, duplicate
receipt idempotency, gap blocking, and no credential/account/order endpoint
access. A real provider smoke remains optional and regular-session-only.

### M4.4: Evidence-Gated US Opportunity Projection

Project an eligible feature snapshot into the existing US
`OpportunitySnapshot` / conditional `TradeSignalEnvelope` path by reference,
not by copying mutable indicators. Missing, stale, or gap-marked evidence
blocks publication with an auditable reason.

**Acceptance:** existing scanner behavior is unchanged absent M4 evidence;
valid fixture evidence preserves conditional-only signal semantics; invalid
evidence is fail-closed.

## Integration Rules

- One Grok task implements one delivery slice or a smaller testable part.
- A task may edit only paths specified by a Codex-written contract. It runs
  in-place on `main` through `run_grok_task.py` (no Git worktree/branch/clone)
  with local tests and static checks, and never provider, broker, or credential
  calls.
- The harness does not enable an OS sandbox. Credential, network, push, and
  external-write prevention remain prompt/contract residual risk; Codex review
  is the final control before integration.
- Codex reviews the diff, runs targeted and full verification, updates this
  plan/checkpoint documentation, and alone commits/pushes `main`.
- Failed in-place worker edits stay uncommitted in the working tree for
  diagnosis and are never auto-merged.

## Non-Goals

- No live-trading endpoint, real-money transaction, credential persistence, or
  provider account mutation.
- No claim that fixture, replay, or Paper output is profitable.
- No direct order authority for scanner, subscription policy, indicators, or
  evidence projection.
