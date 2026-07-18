# M4 US Equity Always-On Data Vertical

**Owner:** Codex implements, verifies, documents, and integrates directly.

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

**Checkpoint (2026-07-18): Complete.** A replay-bound `BroadScannerSnapshot`
now feeds a pure versioned policy that emits only bounded quote/trade desired
state and ordered subscribe/unsubscribe actions. Ranking is stable by priority
score, source rank, instrument ID, and symbol. Hard capacity, incumbent minimum
residency, deterministic eviction, eviction cooldown, and symbol-lineage
consistency are fail-closed. Stale or non-regular-session input removes every
desired subscription without manufacturing cooldown evidence. The scanner,
candidate, and policy have no provider or order authority. Verification: 14
focused policy tests, 54 M4.0-M4.2 tests, full **2143-test** suite, Ruff,
basedpyright, compileall, and no-excuse all pass.

**Operational producer checkpoint (2026-07-19): Complete.** The existing KIS
US `OpportunitySnapshot` now has an opt-in producer path into M4.2. It stores
the exact Opportunity payload before point-in-time security-master resolution,
requires one active US equity/ETF instrument alias per symbol, publishes
immutable scanner-candidate Parquet, verifies it through DuckDB, and stores the
resulting replay identity with a durable scanner snapshot. Restart readers
reverify the canonical dataset and identity before returning the latest input.
The three KIS CLI paths are all-or-none and absent configuration preserves the
existing scanner. The checked-in foundation is a one-symbol fixture, not a
production universe; a raw-first current US security-master adapter remains the
next operational input dependency.
Focused projection and KIS contract tests total 14; the full 2176-test suite,
Ruff, basedpyright, compileall, manual CLI QA, and no-excuse all pass.

**Current security-master checkpoint (2026-07-19): Complete.** A separate
raw-first adapter now collects the official Alpaca Paper `GET /v2/assets`
response without opening account or order APIs. Exact bytes enter a private
append-only ledger before strict parsing. Active listed supported assets become
point-in-time instruments keyed by the stable Alpaca asset UUID and one
provider-symbol alias. The latest reader recomputes the raw payload hash and
receipt identity. External snapshots are limited to one day and can only be
combined with a ready non-fixture foundation. The actual read-only QA preserved
33,351 raw rows, projected 13,011 active instruments, and resolved an actual
symbol into the canonical scanner path. The next dependency is producing a
causal broad-scanner foundation without candidate-specific SIP evidence first.
Focused security-master and scanner integration tests total 25; the full
2187-test suite, Ruff, basedpyright, compileall, actual GET QA, and no-excuse
all pass.

**Broad-scanner foundation checkpoint (2026-07-19): Complete.** The circular
SIP dependency is removed: complete KIS up/down and volume coverage for AMS,
NAS, and NYS plus current NYSE halts and a one-day-current Alpaca instrument
snapshot deterministically produce the non-fixture ready foundation. Its ID
binds the exact Opportunity, security snapshot, and source coverage. The exact
foundation payload and optional security snapshot ID are stored in the same
append-only scanner projection row as the replay-bound snapshot. Schema v1
stores migrate forward before their first v2 projection, while legacy rows
without foundation evidence fail closed on replay. Both one-shot KIS scan and
the regular-session watch accept the operational security-master mode; watch
paths are all-or-none and never carry credential, arm, endpoint, or mutation
flags. A local E2E used the actual 13,011-instrument snapshot to produce one
canonical candidate and a ready three-source foundation without external I/O.
SIP remains downstream bounded feature evidence for selected candidates.

### M4.3: Read-Only Runtime Supervisor

Introduce provider-neutral read-only adapter and supervisor contracts. The
supervisor owns a single writer for raw receipt projection, records reconnect
and gap evidence, and passes only completed normalized events to M4.1.

**Acceptance:** fixture E2E proves normal cycle, restart recovery, duplicate
receipt idempotency, gap blocking, and no credential/account/order endpoint
access. A real provider smoke remains optional and regular-session-only.

**Checkpoint (2026-07-18): Complete.** A provider-neutral adapter exposes only
bounded desired subscriptions and a restart sequence. The supervisor owns a
mode-600 append-only SQLite projection under a non-blocking single-writer
lease, stores raw receipts before sequence evaluation, and persists checkpoint,
gap, and reconnect evidence. Exact duplicate receipts are idempotent;
conflicting duplicates and sequence gaps fail closed, while a new connection
epoch clears the prior gap block. Only persisted completed bars from a clean
epoch reach the M4.1 kernel. A blocked M4.2 policy never calls the adapter.
Fixture E2E covers normal collection, process restart, duplicate receipt, gap,
and reconnect recovery without provider, credential, account, or order access.
Verification: 8 focused tests, full **2151-test** suite, Ruff, basedpyright,
compileall, and no-excuse all pass.

**Provider bridge checkpoint (2026-07-18): Fixture complete, production smoke pending.**
The first actual provider adapter polls one bounded symbol from Alpaca's SIP
minute-bars GET endpoint only during its current regular session. Its context
binds one exact instrument ID and symbol, so a desired-symbol rotation cannot
reuse another instrument's source-level checkpoint. Every
paginated response body is persisted to a separate mode-600 append-only
evidence store before canonical Parquet publication and DuckDB replay. The
verified replay identity then reaches the existing supervisor; same-minute
retry is idempotent, restart resumes after the durable sequence, and a missing
provider minute remains a fail-closed sequence gap. The adapter receives the
full durable checkpoint: a later full-session response opens a new recovery
epoch only when every sequence from session open is present, while an
incomplete backfill preserves the blocked epoch. Noncanonical base URLs,
redirects, closed sessions, and multi-symbol input are rejected before unsafe
follow-up calls. This is completed-bar polling, not quote/trade streaming, and
it imports no account or order path. Eight focused provider tests and 186 M4
regression tests pass. A regular-session external GET smoke and soak remain
pending because this checkpoint was completed on a Saturday.

**Per-instrument runtime fleet checkpoint (2026-07-19): Fixture complete.**
The bounded desired set now creates one fixed-context SIP adapter, runtime
ledger, raw evidence ledger, canonical root, and writer owner per instrument.
Owner paths use an instrument/symbol SHA-256 under private mode-700 roots;
runtime and evidence databases remain mode 600. A two-candidate fixture cycle
produces two independent ready feature bindings and reaches the existing M4.4
gate. A sequence gap or provider failure degrades only its exact owner and
withholds that binding while the other owner continues. A fresh fleet process
reuses each deterministic path and checkpoint, adding only 15 new bars after
the first 20. Request coverage mismatch and symlinked owner roots block before
HTTP. The fleet imports no account or order authority. Production activation
remains blocked until expected cumulative volume is supplied by a causal,
historically derived intraday volume-profile contract rather than an estimate
from the current KIS cumulative volume.

### M4.4: Evidence-Gated US Opportunity Projection

Project an eligible feature snapshot into the existing US
`OpportunitySnapshot` / conditional `TradeSignalEnvelope` path by reference,
not by copying mutable indicators. Missing, stale, or gap-marked evidence
blocks publication with an auditable reason.

**Acceptance:** existing scanner behavior is unchanged absent M4 evidence;
valid fixture evidence preserves conditional-only signal semantics; invalid
evidence is fail-closed.

**Checkpoint (2026-07-18): Complete.** An opt-in typed gate consumes the
existing US `OpportunitySnapshot` plus one exact M4 feature binding per
candidate. Missing, extra, gap-marked, stale, insufficient-history,
noncausal, and expired evidence cannot produce a gated Opportunity. A ready
snapshot contributes only a canonical SHA-256 evidence reference; indicator
values are not copied into candidate or signal fields. The derived Opportunity
ID binds the complete base payload, evaluation time, and sorted evidence set.
Only `UsFeatureGateReady` reaches the unchanged publication implementation,
which retains `conditional` actionability and no quote validation. Existing
scanner and publication APIs remain unchanged. Verification: 8 focused tests,
full **2159-test** suite, fixture library E2E, Ruff, basedpyright, compileall,
and no-excuse all pass.

## Integration Rules

- Codex implements each delivery slice directly on `main` with TDD and a narrow
  changed-file scope; the Grok development harness is not used for M4 work.
- Pure contract/kernel slices never call provider, broker, credential, account,
  or order paths. Runtime slices begin with local fixtures and read-only adapter
  contracts.
- Every slice runs focused tests, Ruff, basedpyright, compile QA, relevant
  manual QA, and the full repository suite before its checkpoint commit.
- Codex updates this plan and README, checks the working tree before and after,
  and alone commits and pushes `main`.

## Non-Goals

- No live-trading endpoint, real-money transaction, credential persistence, or
  provider account mutation.
- No claim that fixture, replay, or Paper output is profitable.
- No direct order authority for scanner, subscription policy, indicators, or
  evidence projection.
