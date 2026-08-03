# Integrated Options Research Workbench Design

## 1. Product contract

This product is an evidence-first autonomous trading research workstation that turns reviewed,
read-only market data into traceable hypotheses, bounded experiments, paper recommendations, and
manual promotion decisions without opening a live-money path.

The workbench must never claim profitability from replay, synthetic, or backtest output. KIS and LS
remain read-only. Alpaca mutations remain limited to the exact
`https://paper-api.alpaca.markets` base URL and must continue to pass the existing endpoint guard,
risk kernel, reconciliation, and Paper lifecycle gates.

## 2. Approved benchmark contract

The approved direction is a hybrid of three exact benchmark surfaces:

| Benchmark | Exact reference | Borrowed behavior | Explicitly not copied |
| --- | --- | --- | --- |
| ORATS / Otto | <https://orats.com/otto> and <https://orats.com/> | One conversational agent invokes real scanners, strategy builders, and backtest tools; each tool call exposes parameters, progress, evidence, and results | Brand, copy, proprietary indicators, broker integrations, paid datasets |
| OptionStrat | <https://optionstrat.com/features> | Selecting expirations, strikes, and legs updates payoff, break-even, net Greeks, and scenario controls immediately; discovered flow opens in the same builder | Brand, templates as copied content, proprietary probability or optimizer formulas |
| Market Chameleon | <https://marketchameleon.com/Learn/OptionChain> | Calls on the left, strike in the center, puts on the right; expiration selection, configurable analytics, historical context, and contract drill-down | Brand, subscription copy, proprietary rankings or historical datasets |

The current Ember Operations Workstation remains the visual and safety system of record. Benchmark
behavior is adapted into its carbon-black, ember-accented, evidence-first UI rather than replacing
the product with another company's identity.

## 3. Current gaps that this design closes

1. The existing `Derivatives` workspace projects generic items but has no typed
   strike-by-expiration option-chain table.
2. Alpaca has an indicative US option chain and contract catalog, but KIS has only an overseas
   futures quote adapter and LS has only a news adapter.
3. Agent activity, research evidence, experiment chains, promotion evidence, and Paper state live
   in separate workspaces instead of one continuous research flow.
4. The dashboard does not project intraday promotion artifacts or a single immutable
   hypothesis-to-promotion timeline.
5. There is no interactive multi-leg strategy builder backed by the same contracts used for
   research and replay.

## 4. Information architecture

The existing nine top-level routes remain stable. `#derivatives` becomes the integrated Options
Research Workbench and gains five internal views. Existing deep links and workspace identities do
not change.

### 4.1 Market pulse

- Underlying spot and completed-bar timestamp
- Futures basis when an admissible futures source exists
- Expiration-level IV, term structure, skew, volume, and open-interest summaries
- Provider, entitlement, freshness, and redistribution status on every section
- Compact links to the existing `Markets` and `Data Sources` evidence

### 4.2 Unified option chain

- Expiration selector and bounded strike window
- Calls left, strike center, puts right
- Bid, ask, mid, spread, last, volume, open interest, IV, delta, gamma, theta, and vega
- Provider label and observation time per row
- Explicit `indicative`, `delayed`, `current`, `stale`, `blocked`, or `unavailable` state
- Contract and source-receipt drill-down through the existing Evidence Trace drawer
- No silent merge when two providers disagree; a selected source policy and comparison evidence are
  visible

### 4.3 Strategy builder and agent room

- Multi-leg builder supports calls, puts, and an optional underlying leg
- Selected legs are immutable value objects until the operator applies a deliberate edit
- Payoff curve, break-even points, bounded scenario table, net Greeks, maximum modeled gain/loss,
  and data-quality warnings update from deterministic code, not LLM arithmetic
- The `derivatives_research` agent can call approved tools to inspect the chain, construct a
  candidate, preregister a hypothesis, and request a bounded experiment
- Every tool call renders as an expandable receipt containing safe parameters, source hashes,
  progress, terminal result, and links to evidence; secrets, raw provider payloads, local paths, and
  account identifiers are excluded

### 4.4 Experiment lab

- One immutable chain from source receipts to hypothesis, dataset, code revision, trial, terminal,
  Reviewer decision, and lifecycle decision
- In-sample, out-of-sample, walk-forward, cost-adjusted, sample-size, overfit, and duplicate checks
- Regime and failure-segment breakdowns
- Results compare against preregistered baselines and never display replay as realized profit
- The agent may propose the next test but cannot rewrite completed evidence

### 4.5 Promotion and operations

- Candidate states: generated, preregistered, testing, revise, rejected, held, eligible,
  manually approved, next-session active, suspended
- Required evidence, missing gates, operator approval, and next-session authority are separate rows
- Paper recommendation and reconciliation state are read-only projections from existing stores
- Runtime freshness, consecutive failures, model/heavy-experiment budgets, storage, backup, and soak
  status remain visible
- No control in this workspace can call a KIS or LS mutation endpoint or bypass Alpaca Paper guards

## 5. Canonical data contracts

### 5.1 Provider-neutral option contract

The backend introduces one canonical option identity and snapshot contract. It reuses the semantics
of the existing Alpaca `OptionSecurityMasterContract` while adding provider-neutral provenance.

Required identity fields:

- market and provider
- provider contract identifier and normalized root/underlying identifier
- call or put, exercise style, expiration, strike, multiplier, currency
- observation timestamp and source receipt hash

Required quote fields:

- bid/ask price and size, last price and timestamp
- volume and open interest when supplied
- provider IV and Greeks when supplied, each with method/source metadata
- freshness, entitlement, redistribution, and quote-quality state

Missing provider fields remain absent; they are never filled with zero. Calculated IV or Greeks use
a separate derived-analysis receipt containing model version and inputs so provider facts and local
calculations cannot be confused.

### 5.2 Unified chain snapshot

A chain snapshot binds one underlying, observation point, expiration set, selected provider policy,
bounded contracts, and capability evidence. It includes `total_count`, `projected_count`, and
`truncated` and preserves source-specific rows for comparisons.

The dashboard receives a strict Zod/Pydantic projection rather than generic untyped workspace
items. Invalid, stale, unlicensed, or cross-provider-inconsistent inputs fail at their section
boundary and do not collapse healthy sections.

### 5.3 Strategy scenario contract

A strategy scenario contains the selected canonical legs, entry prices, quantity, valuation time,
underlying scenario range, volatility shift, and time shift. Deterministic calculators produce the
payoff series and aggregate Greeks. The LLM consumes these results and evidence references; it does
not supply authoritative numeric results.

## 6. Provider integration

### 6.1 Alpaca

- Reuse the existing option-chain, contract-catalog, surface, skew, and term-structure stores.
- Preserve the existing research-shadow/indicative classification unless reviewed entitlement
  evidence proves otherwise.
- Contract catalog GETs may continue to use the exact Paper host, but no order authority is implied.

### 6.2 KIS

- Add explicitly reviewed read-only domestic option master/display-board/current-price/order-book/
  completed-chart contracts.
- Add explicitly reviewed read-only overseas option master/current-price/order-book/history
  contracts.
- Follow the current raw-receipt-first collection, bounded replay, strict endpoint, private
  credential, and entitlement-admission patterns.
- Do not add balance, account, position-changing, or order endpoints.

### 6.3 LS

- Add only official, explicitly reviewed `/futureoption/market-data` contracts required for current
  price, order book, completed trades/history, and option board.
- Add only official, explicitly reviewed `/overseas-futureoption/market-data` contracts needed for
  the master and quote/history surfaces.
- Never call `/stock/accno`, `/stock/order`, derivatives account/order paths, or WebSocket account
  registration types `1/2`.
- Until each contract and entitlement is reviewed, the production projection remains
  `unavailable` with an operator action; fixtures cannot promote it to available.

## 7. End-to-end data flow

```text
reviewed provider GET contract
  -> immutable raw receipt
  -> provider parser and typed store
  -> canonical option adapter
  -> capability, entitlement, freshness, and quality gate
  -> unified chain snapshot
  -> dashboard Workbench + deterministic strategy calculator
  -> agent tool receipt and preregistered hypothesis
  -> bounded experiment + independent Reviewer
  -> manual promotion decision
  -> next-session Paper-only authority, if every existing gate passes
```

The dashboard server remains a redacted GET/WebSocket projection. Provider collection, model calls,
and broker actions stay on the Mac mini side. Public dashboard routes remain read-only.

## 8. Failure and safety behavior

- Closed sessions and historical data may support research but cannot create a backdated current
  recommendation.
- A stale feed, missing spread, incomplete chain, unreviewed entitlement, invalid private file,
  hash mismatch, or schema mismatch produces a typed section-local terminal.
- Same-bar stop/target collisions continue to resolve to the stop.
- Provider disagreement is rendered and traced; one value is not silently chosen without a
  declared source policy.
- Model, provider, and heavy-experiment budgets remain bounded and observable.
- Replaying the same receipt, hypothesis, experiment, or approval is idempotent and does not create
  duplicate lifecycle events.
- No UI wording may imply that backtest, replay, Paper, or synthetic output guarantees future
  performance.

## 9. Delivery decomposition

This program is too large for one safe implementation pass. It is delivered as dependency-ordered
subprojects, each with its own tests, real-surface evidence, atomic commit, and review gate.

1. **Workbench foundation:** canonical chain and promotion projection schemas, `#derivatives`
   five-view shell, fixture-backed populated/stale/blocked states, and Evidence Trace integration.
2. **Existing Alpaca binding:** map current Alpaca stores into the canonical chain and strategy
   calculator without upgrading indicative entitlement.
3. **KIS option supply:** domestic first, overseas second; raw receipts, stores, adapters,
   entitlement gates, CLIs, and production projection.
4. **LS option supply:** reviewed domestic first, reviewed overseas second; the same raw-first and
   fail-closed boundary.
5. **Agent and strategy builder:** deterministic multi-leg calculator, tool receipts, hypothesis
   registration, and experiment launch.
6. **Experiment and promotion integration:** immutable experiment detail, Reviewer outcome,
   approval evidence, next-session control, Paper projection, and operations status.
7. **Frozen-tree quality gate:** full targeted regression, provider endpoint guards, Ruff,
   basedpyright, TypeScript checks, browser QA, accessibility, performance, code review, manual QA,
   and gate review.

The first implementation plan covers subproject 1 only. Later subprojects start from its shipped
contracts instead of changing them in parallel.

## 10. First implementation slice and visible proof

The first benchmark-driven batch contains four tightly related tasks under one visual theme:

1. Add typed option-chain and promotion summary projections to the dashboard schema and fixtures.
2. Expand `#derivatives` into the five-view Workbench shell without changing the nine top-level
   routes.
3. Render a Market Chameleon-style bounded chain table and ORATS-style traceable agent/experiment
   panels from production-shaped fixture data.
4. Add OptionStrat-style selected-leg composition with deterministic fixture-backed payoff output;
   production interaction remains disabled when chain authority is blocked.

Required browser proof:

- current-before and implementation-after screenshots at 375, 768, and 1280 pixels
- calls-left/strike-center/puts-right chain visible at desktop
- mobile reflow without page-level horizontal scrolling; the labeled table viewport may scroll
- keyboard expiration, row, leg-selection, internal-view, and Evidence Trace interactions
- populated, stale, blocked, unavailable, corrupt, and valid-empty states
- no console error, axe violation, secret/path leak, or public mutation control
- production build and real-browser Lighthouse audits under the repository's frontend quality gate

## 11. Test strategy

- **Python unit tests:** canonical option identity, adapter mapping, entitlement/freshness precedence,
  promotion projection, derived-analysis separation, replay and malformed input.
- **Provider integration tests:** HTTP-level fixtures prove exact host/path, GET-only behavior,
  redirect rejection, response bounds, pagination, raw-first persistence, and no credential/log leak.
- **TypeScript unit tests:** Zod boundary parsing, chain grouping, source-state precedence, strategy
  leg selection, payoff projection, and trace traversal.
- **Snapshot integration tests:** Python redacted snapshot is parsed by the TypeScript schema and
  renders all five internal views.
- **E2E browser tests:** production Bun server, real route navigation, keyboard and focus behavior,
  responsive layouts, reconnect/stale handling, and trace drawer focus restoration.
- **Safety regressions:** exact Alpaca Paper endpoint guard; KIS/LS read-only contract allowlists;
  no account/order endpoint; no full-universe or `data/regend_us_stocks` load; no real provider or
  broker mutation during QA.

## 12. Completion definition

The overall program is complete only when admissible KIS, LS, and Alpaca option data can be
normalized and inspected in the Workbench; the derivatives agent can create a traceable strategy
hypothesis and bounded experiment through real tools; results reach immutable Reviewer and manual
promotion gates; and the browser shows the full evidence path without weakening Paper-only safety.

External entitlement absence is not hidden. When a provider account lacks access, the adapter,
contract review, and operator action must be complete and the corresponding production state must
truthfully remain blocked or unavailable.
