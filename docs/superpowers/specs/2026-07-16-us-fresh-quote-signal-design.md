# US Fresh Quote Actionability Signal Design

- Status: approved
- Date: 2026-07-16
- Product boundary: read-only US market data, local recommendation output, and shadow/Paper research
- Broker mutation: none
- External delivery: none

## 1. Goal

The current KIS US scanner publishes a causal `OpportunitySnapshot` and an immutable conditional `TradeSignalEnvelope`. It does not re-check the quote after strategy evaluation, so the local card cannot claim that the displayed entry is feasible at publication time.

This milestone adds a bounded read-only KIS level-one quote check for each newly publishable US day signal. A quote that passes the current-session, freshness, spread, and entry-slippage gates produces a new immutable `current_quote_validated` signal and a distinct Korean card. The original conditional signal is never updated or deleted.

This is an actionability observation, not an order authorization. It does not import an Alpaca execution adapter, submit an order, send an external message, or change a strategy lifecycle state.

## 2. Official Provider Contract

Use the KIS official overseas-stock current level-one quote sample:

- endpoint: `GET /uapi/overseas-price/v1/quotations/inquire-asking-price`
- transaction ID: `HHDFS76200100`
- parameters: `AUTH`, `EXCD`, `SYMB`
- required provider fields: quote date `dymd`, quote time `dhms`, best bid `pbid1`, best ask `pask1`, bid size `vbid1`, and ask size `vask1`

Reference: `koreainvestment/open-trading-api`, `examples_llm/overseas_stock/inquire_asking_price`.

The adapter accepts only the exact KIS live and virtual-trading HTTPS origins with no base path, user info, query, or fragment. The shared client and each authenticated GET disable redirects, and transient 500/502/503/504 responses receive the existing single bounded read retry. It never calls a KIS account, balance, position, or order endpoint. Request headers, credentials, access tokens, raw authentication responses, and provider error bodies are never written to reports or exceptions.

## 3. Chosen Approach

### 3.1 Rejected: overwrite the conditional signal

The contract outbox is append-only and keyed by `signal_id`. Replacing a conditional payload with a quote-validated payload under the same ID would correctly raise a conflict and would erase the historical distinction between strategy observation and later quote observation.

### 3.2 Rejected: start with a persistent quote WebSocket

KIS `HDFSASP0` can provide a continuous US level-one stream, but it also requires subscription ownership, candidate churn, heartbeat, reconnect, raw receipt recovery, and sequence coverage. That is a later latency milestone after the REST semantics and evidence contract are proven.

### 3.3 Rejected: make Alpaca the first actionability source

Alpaca latest quotes are useful as a later independent cross-provider check, but IEX/SIP entitlement differences can change coverage by environment. The first vertical stays with the KIS discovery provider and records its exact source.

### 3.4 Adopted: bounded KIS REST quote per new signal

Only symbols with a newly publishable conditional signal are queried, once each per scan cycle and sequentially. The existing maximum candidate count bounds requests. Provider failures affect only quote actionability; they do not rewrite the underlying recommendation or turn a partial quote cycle into a validated signal.

## 4. Immutable Contracts

### 4.1 `UsQuoteSnapshot`

Persist normalized public market data to `us-quote-snapshots.v2.jsonl`:

```text
schema_version
quote_id
provider
market_id
exchange
symbol
provider_observed_at
received_at
bid
ask
bid_size
ask_size
spread_bps
```

`quote_id` is deterministic from provider, exchange, symbol, provider time, local receipt time, prices, and sizes. Two independent receipts of the same displayed provider quote therefore remain distinct observations. Exact replay is a no-op; the same ID with different content is a conflict. The snapshot contains no credential, token, request header, account field, or raw provider message.

### 4.2 `QuoteActionabilityAssessment`

Persist one terminal assessment per base signal and scan cycle to `quote-actionability-assessments.v2.jsonl`:

```text
assessment_id
base_signal_id
scan_started_at
evaluated_at
status
quote_id (optional)
derived_signal_id (optional)
```

Allow-listed statuses are:

- `validated_waiting`: quote is fresh and feasible but ask is below the stop trigger
- `validated_trigger_reached`: ask reached the trigger without exceeding allowed slippage
- `market_closed`
- `provider_failed`
- `invalid_quote`
- `future_quote`
- `stale_quote`
- `spread_too_wide`
- `setup_invalidated`
- `entry_slippage_exceeded`

Provider details are reduced to the allow-listed status. A failed assessment does not contain a quote ID unless a structurally valid normalized snapshot was stored first.

`assessment_id` is deterministic only from the complete base signal ID and `scan_started_at`. A scan cycle can therefore append exactly one terminal result for a base signal; a second status or evaluation payload under that cycle is a conflict instead of another terminal assessment.

The receipt-aware quote identity and one-terminal assessment identity are schema version 2 contracts. Version 1 quote and assessment JSONL files are legacy artifacts: the v2 writer neither revalidates nor overwrites them. Quote artifacts expose no path-parametrized standalone writer. Before writing any v2 quote batch, the sole batch writer validates artifact ID completeness, one scan cycle, base and quote evidence links, and every snapshot, derived signal/card, and assessment plan against existing targets; any incomplete batch, malformed file, or conflict aborts before the first append.

### 4.3 Derived quote-validated signal

The derived signal copies the immutable strategy lane, strategy version, symbol, entry, stop, targets, rationale, invalidation rule, and opportunity ID from the base conditional signal. It changes only the observation lineage needed for a new current-time claim:

- `signal_id`: bounded `us-quote-signal:<sha256>` identity derived from the complete base signal ID and quote evidence ID
- `observed_at`: local quote receipt/evaluation time
- `valid_until`: earlier of the base signal expiry and quote expiry
- `actionability`: `current_quote_validated`
- `quote_validation`: exact bid, ask, provider time, quote expiry, spread, and configured maximum
- `evidence_refs`: original opportunity, recommendation, base signal, and quote snapshot

The quote-validated signal is a new immutable observation. It is not an in-place promotion of the base signal and cannot confer broker execution permission.

## 5. Causality And Risk Gates

The first version uses fixed conservative gates:

- evaluation and provider quote must be in the current NYSE regular session
- provider timestamp is interpreted in `America/New_York`
- provider timestamp must not be in the future
- receipt age from provider timestamp must be strictly less than 5 seconds
- bid and ask must be finite, positive, and `bid <= ask`
- bid and ask sizes must be non-negative integers
- spread must be at most 25 basis points
- the setup is invalid if the current bid is at or below the stop
- for a long signal, ask may not exceed the entry trigger by more than 20 basis points
- quote expiry is provider timestamp plus 5 seconds
- derived signal expiry cannot exceed the base signal or opportunity expiry

An ask below the stop trigger produces `validated_waiting`. An ask at or above the trigger and within the 20 bp allowance produces `validated_trigger_reached`. Neither state submits an order.

These gates are stricter than the existing scanner's broad candidate spread screen and do not enlarge any Paper notional, loss, position, or cost limit.

## 6. Data Flow

```text
complete KIS ranking coverage
-> OpportunitySnapshot
-> current-session minute bars
-> immutable strategy recommendation
-> conditional TradeSignal publication
-> unique signal symbols
-> KIS REST level-one quote
-> normalized quote snapshot
-> terminal actionability assessment
-> optional derived current-quote-validated signal and Korean card
```

The quote request runs while the existing KIS client and token are open. Output append happens only after model validation. A quote failure never causes a synthetic quote, a current-actionability claim, or an order fallback.

## 7. CLI And Reporting

`run_kis_paper_scan.py` keeps its existing options and outputs. It adds aggregate terminal counts for quote attempts, validated waiting signals, validated triggered signals, and blocked assessments. It does not print bid, ask, symbol, provider text, credential state, or quote IDs in the terminal summary.

The existing conditional card remains unchanged. A quote-validated card adds:

- provider-observed quote time
- current bid and ask
- spread
- trigger state (`waiting` or `reached`)
- the unchanged entry trigger, stop, targets, and expiry

The card continues to state that the output is a research and Paper forward-validation candidate, not a profit claim or automatic order.

## 8. Restart And Conflict Behavior

- Exact quote, assessment, signal, and card replay is a no-op.
- Same identity with different payload is fail-closed.
- An existing conditional signal is never overwritten by a later quote.
- A new provider quote creates a new derived signal identity only while the base signal is still valid.
- A restart after base expiry records no new quote request or derived signal.
- A provider exception is sanitized before it can reach a report or CLI error.

## 9. Testing

### Contract and adapter tests

- exact endpoint, transaction ID, and three parameters
- valid split outputs and New York timestamp parsing across DST
- missing, malformed, future, stale, crossed, zero, non-finite, and negative-size quote rejection
- credential and provider payload redaction on HTTP and parse failures
- exact approved base validation and cross-origin redirect suppression
- one bounded retry for transient KIS server errors

### Projection and outbox tests

- waiting and trigger-reached derived signals
- strict 5-second freshness boundary: 4.999 seconds passes and 5 seconds is stale
- 25 bp spread boundary
- 20 bp entry-slippage boundary
- setup invalidated at the stop
- sorted complete evidence lineage
- receipt-aware quote ID, one-terminal-per-cycle assessment ID, exact replay no-op, and conflicting replay rejection

### Orchestration tests

- no quote request without a newly eligible conditional signal
- one sequential request per unique symbol
- one symbol failure does not validate that signal or corrupt other assessments
- closed market and expired base signal make zero provider calls
- no broker, account, order, external-message, or lifecycle imports

### Required verification

- focused pytest during TDD
- full pytest
- Ruff
- basedpyright
- CLI `--help`
- invalid `--top 0` before credential loading
- deterministic fake-provider happy path
- one read-only regular-session KIS smoke when current-session conditions and rotated local credentials are available

## 10. Completion Criteria

1. Every current-actionability claim has a stored causal quote snapshot and terminal assessment.
2. Conditional and quote-validated observations remain separately immutable.
3. Stale, future, wide-spread, invalidated, or slipped quotes cannot produce a validated signal.
4. The user card distinguishes trigger waiting from trigger reached and shows the current bid/ask observation time.
5. Existing scanner, recommendation, outbox, Paper safety, lane, Reviewer, and experiment-ledger tests remain green.
6. KIS remains read-only and Alpaca Paper mutation count remains zero for this milestone.

## 11. Follow-On Work

After this checkpoint:

1. add an external delivery adapter with an explicit destination allow-list and durable acknowledgement
2. add a persistent KIS quote stream only if REST latency/coverage evidence justifies it
3. compare KIS actionability against independently licensed Alpaca SIP evidence
4. run the already-armed Alpaca Paper entry/OCO/EOD smoke as a separate explicit operational checkpoint
