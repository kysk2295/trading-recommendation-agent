# Lane Control-Plane Contracts Design

**Status:** Approved architecture from the source-thread delegation, narrowed here into the first incremental implementation checkpoint.

**Goal:** Add enforceable lane identity, policy, risk, account isolation, experiment scope, and finalized daily snapshot contracts around the existing `intraday_momentum` Paper execution system without moving the current execution core or enabling any new broker authority.

## Chosen Boundary

The first lane checkpoint uses a separate append-only lane registry beside the existing per-lane execution ledger.

- Existing execution SQLite remains the `intraday_momentum` lane ledger and keeps its current single Writer, account binding, mutation arm, and Paper-only broker guards.
- A new lane registry stores immutable manifests, dedicated Paper account bindings, pre-registered experiment scopes, and finalized lane daily snapshots.
- `swing_momentum` starts as shadow-only and `market_regime` starts as signal-only, so neither receives a Paper account binding or broker mutation authority.
- The registry exposes a query-only reader for the independent Reviewer. It does not place orders.
- Portfolio Manager is not implemented in this checkpoint. Its only future input is the finalized `LaneDailySnapshot` table, and it remains disabled until at least two lanes have champions.

This was chosen over two alternatives:

1. Adding lane columns to every existing execution table now would create the prohibited big-bang migration and risk the already validated intraday state machine.
2. Creating separate services or repositories per lane would fragment the shared verification kernel and experiment lineage.

## Shared Contracts

### `LaneId`

The closed initial set is `intraday_momentum`, `swing_momentum`, and `market_regime`. Unknown text is rejected rather than silently creating a new execution domain.

### `LaneExecutionPolicy`

Execution behavior is a tagged union of distinct policy/state-machine contracts, not an `overnight` boolean.

- Intraday: `intraday_flat_by_close_v1`, regular-session Alpaca Paper authority, entry cutoff 30 minutes before close, mandatory flatten start 5 minutes before close.
- Swing: `swing_shadow_multisession_v1`, shadow-only authority, explicit multi-session position states, no broker account.
- Market regime: `regime_signal_publish_v1`, signal publication only, no order states and no broker account.
- A future directly traded regime/ETF policy must be a new manifest version with its own dedicated Paper account. Signal-only manifests can never be bound to an account.

### `LaneRiskContract`

The contract records whether limits are enforced against `broker_paper`, `shadow`, or `none`. The active intraday pilot is pinned to the already approved smoke limits: maximum notional USD 100, planned risk USD 10, one position, daily loss USD 30, and minimum 20 bp per side. This checkpoint must not widen them.

Shadow-only swing uses the same conservative numerical envelope for comparability but has no broker authority. Signal-only regime has zero order, position, notional, and risk capacity.

### `LaneManifest`

A manifest combines one lane ID, one versioned execution policy, one risk contract, a stable ledger namespace, allowed strategy IDs, and account-binding mode. Its key is a SHA-256 over canonical JSON. A lane version cannot be rewritten under the same identity.

### `LaneAccountBinding`

A binding contains only the lane ID, an existing execution-ledger fingerprint, an Alpaca account fingerprint, the fixed Paper base URL, and an aware binding timestamp. It never stores an account number, API key, secret, or raw credential. Registry constraints enforce:

- one binding per broker-authorized lane in this initial version;
- one Paper account fingerprint per lane;
- one execution-ledger fingerprint per lane;
- no binding for shadow-only or signal-only manifests;
- exact `https://paper-api.alpaca.markets` only.

### `ExperimentScope`

Every hypothesis has one immutable scope.

- Single-lane scope contains exactly one lane and no source hypotheses or combination rule.
- Cross-lane scope contains at least two unique lanes, at least two source hypotheses, a new hypothesis ID distinct from every source, a non-empty pre-registered combination rule, and an aware registration timestamp.
- Daily evaluation can include a scope only when it was registered before that market session opened.
- The registry keeps `hypothesis_id` unique, so a result cannot be moved between lanes after outcomes are known.

### `LaneDailySnapshot`

The lane Writer can append one deterministic finalized snapshot per lane and market date. It includes manifest and experiment-scope keys, source-ledger generation/hash, champion versions, data-quality state, incidents, conservative equity/PnL/risk, and open order/position counts.

- Intraday final snapshots require zero open orders, positions, and planned open risk.
- Signal-only regime snapshots require all broker/exposure fields to be zero.
- Allocation eligibility requires complete data, no incidents, and at least one champion version.
- Snapshots do not contain order commands. A future Portfolio Manager can only read them after finalization.

## Append-Only Registry

The registry uses a separate SQLite schema with four tables:

- `lane_manifests`
- `lane_account_bindings`
- `experiment_scopes`
- `lane_daily_snapshots`

Every table has update/delete rejection triggers. Exact replay is idempotent; an immutable identity with different content raises a typed conflict. Reader connections use `mode=ro` and `PRAGMA query_only = ON`. The Writer uses a non-blocking file lock, matching the existing execution-store pattern.

## Existing-System Integration

1. Default manifests and current intraday strategy scopes are registered by a local bootstrap CLI with no network access.
2. An optional existing intraday execution database can be bound by reading its current schema and already stored account fingerprint. Output is redacted and never prints the fingerprint or local path.
3. `DailyResearchRecord` moves to schema v2, embeds `ExperimentScope`, includes its key in `record_id`, and only accumulates prior rows from the same immutable scope. Schema-v1 JSONL rows are projected as the historical intraday single-lane scope when read; original files are not rewritten.
4. The entry and safety smoke CLIs consume the intraday pilot risk contract instead of maintaining duplicate local limit constants.
5. Existing order intent, broker event, mutation, recovery, and safety tables are unchanged in this checkpoint.

## Failure Behavior

The registry fails closed on malformed hashes, naive timestamps, unsupported policy/lane combinations, duplicate account or ledger use, unregistered manifest/scope references, post-session experiment registration, a non-flat intraday final snapshot, or any attempt to grant broker authority to a signal-only manifest.

No error or report includes account fingerprints, account identifiers, credentials, broker order IDs, or raw payloads.

## Verification

- Unit tests cover every contract invariant and deterministic key.
- Store tests cover append-only triggers, exact replay, conflicts, account/ledger uniqueness, and query-only reading.
- Daily-record tests cover schema-v1 projection, scope-isolated accumulation, pre-registration timing, and schema-v2 output.
- CLI tests cover help, bad paths, registry-only bootstrap, bound-intraday bootstrap, idempotent replay, and redaction.
- Existing Paper smoke and full regression suites must remain green, together with Ruff, changed-file formatting, basedpyright, and manual CLI QA.
- This checkpoint performs zero Alpaca POST/DELETE calls and does not require credentials or market hours.

## Deferred Work

- A directly traded `swing_momentum` or ETF-rotation `market_regime` state machine and dedicated Paper account.
- Reviewer promotion-event persistence and champion projection.
- Portfolio Manager risk-budget allocation, which remains gated on two lane champions.
- ORB daily broker/shadow snapshot production and the scheduled forward-validation loop, implemented after these contracts are durable.
