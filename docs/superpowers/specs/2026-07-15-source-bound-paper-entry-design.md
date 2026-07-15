# Source-bound Alpaca Paper entry smoke design

Date: 2026-07-15

Status: approved A-stage safety scope, incremental implementation around the existing intraday lane

## Problem

The current armed entry CLI accepts recommendation identity, prices, spread, and bar timestamps as free-form arguments. The active operating session rechecks that those values describe the just-completed one-minute bar, but the CLI does not prove that they came from the immutable KIS watch source. An operator or wrapper could therefore fabricate a self-consistent current timestamp set and bypass source lineage while still satisfying the runtime shape checks.

The first regular-session Paper POST must remain impossible unless the order is derived from the exact point-in-time ORB recommendation and candidate input already stored by the watch process.

## Considered approaches

### 1. New signed candidate manifest

The watch process could emit a new manifest that is later consumed by the entry CLI. This gives a narrow input, but adds another projection, signing or hash lifecycle, and atomicity rules between the existing SQLite rows and the manifest. It is unnecessary for the first smoke because the append-only source rows already exist.

### 2. Query the watch SQLite directly

The entry CLI can open the existing `paper_recommendations.sqlite3` in read-only/query-only mode and derive the admission request from the recommendation, candidate input snapshot, and first-observed minute bar. This keeps one source of truth and lets the existing operating-session gate independently revalidate freshness after WSS and REST recovery.

This is the selected approach.

### 3. Keep free-form inputs and rely on the runbook

This has the smallest code diff but leaves an avoidable production bypass surface. It is rejected.

## Architecture

Add a focused source module that owns no network client and performs no writes. It receives a watch database path and an aware evaluation time, opens the file with SQLite `mode=ro` plus `PRAGMA query_only = ON`, and returns one existing `PaperOrderAdmissionRequest`.

The loader joins data by semantic identity in Python rather than comparing ISO text:

- recommendation strategy is `opening_range_breakout` and state is `setup`;
- candidate input has the same symbol and its `observed_at` is the same instant as recommendation `created_at`, even if offsets differ;
- candidate minute bar has the same exchange, symbol, and latest-completed-bar instant;
- bar start is exactly the prior minute relative to the evaluation time in New York;
- bar completion is no later than first observation, which is no later than recommendation creation and evaluation;
- recommendation age is at most 30 seconds;
- symbol, recommendation ID, prices, targets, spread, and completed-bar volume are valid;
- exactly one candidate survives all checks.

The resulting request uses the recommendation ID as the Paper intent and Alpaca `client_order_id`, uses the stored prices and timestamps unchanged, and fixes `liquidity_allowed_quantity` to 1 for the first smoke. The existing intraday pilot risk contract still caps notional at 100 USD and planned risk at 10 USD.

## CLI contract

`run_alpaca_paper_entry_smoke.py` keeps:

- `--arm-paper-mutation ARM_ALPACA_PAPER_ONLY`
- `--database`
- `--output-dir`

It adds required `--watch-database` and removes the free-form intent, symbol, side, price, timestamp, liquidity, and spread options.

The execution ledger must be initialized before the watch source is read. Source loading happens before credential loading, WSS, REST, or mutation broker creation. A source failure writes only a generic error class and returns 2. A valid source is passed to the existing `PaperOperatingSession.execute_entry`, whose current market clock, WSS epoch, broker state, portfolio, and exact current-bar gates remain authoritative.

## Failure policy

- Missing or unreadable source database does not create a file and never loads credentials.
- Missing tables, malformed rows, stale or future timestamps, invalid values, zero or multiple current candidates, and source changes are fail-closed.
- Error text never includes a path, recommendation ID, symbol, account identity, broker identity, or raw row.
- No fallback to command-line values or a fixture flag exists in the production CLI.
- A source that becomes stale while the operating session opens is rejected again by the existing admission gate before POST.

## Testing

Source tests cover:

- one valid candidate with different timezone offsets for the same instant;
- missing database without file creation;
- missing schema and malformed values;
- stale, future, unfinished, or wrong-minute observations;
- duplicate current candidates;
- read-only/query-only behavior;
- recommendation ID, prices, bar times, spread, and fixed one-share liquidity projection.

CLI tests cover:

- direct `--help` with the new contract;
- old free-form options rejected by argparse;
- wrong arm rejected before source, credentials, or network;
- source rejection before credentials and WSS with redacted output;
- fake source plus fake operating session happy path;
- existing blocked, acknowledged, and runtime-error behavior.

Full pytest, Ruff, changed-file format, basedpyright, and direct CLI QA must pass. No Alpaca Paper POST/DELETE is performed during implementation or validation.

## Non-goals

- No lane contract, strategy rule, risk limit, OCO lifecycle, Reviewer, or Portfolio Manager change.
- No automatic strategy selection when multiple current candidates exist.
- No actual broker mutation while credentials or regular-session prerequisites are absent.
- No claim that the first smoke is a profitability sample or promotion signal.
