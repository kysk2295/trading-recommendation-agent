# US Swing Shadow Trial And Reviewer Design

- Status: approved for implementation
- Date: 2026-07-16
- Scope: `us_equities/swing_trading/new_high_momentum` forward-only research lineage
- Out of scope: provider access, Paper account/order access, lifecycle transition, champion, allocation

## Goal

Connect each newly observed US swing new-high/RVOL shadow signal to one preregistered global `shadow_forward` trial, immutable terminal evidence from the existing swing shadow ledger, and an independent Reviewer record. The feature must make no trading authority change.

## Decisions

1. A trial represents one `TradeSignalEnvelope`, not an aggregate day. `signal_id` is the stable external identity and the trial ID is a deterministic hash of the signal ID and strategy version.
2. Registration is prospective. A new trial may be created only after its `signal_created` event and before the signal's next regular-session open. A request after that open can only replay the exact existing registration; it cannot backdate a trial.
3. The first registration atomically appends the matching strategy version and one `experimental_shadow` lifecycle registration when they do not already exist. The global hypothesis and its `ResearchHypothesisCard` must already be exact and source-bound.
4. `SwingShadowReader` remains the terminal source. `expired`, `stopped`, `targeted`, and `time_exit` are observed terminal outcomes; a missing terminal stays open. `expired` is a completed no-entry observation, not a zero-return substitution.
5. The Reviewer writes one immutable event to a new swing-specific review ledger. It reads the global experiment ledger and swing shadow ledger query-only, verifies every artifact hash again, and always sets lifecycle, champion, allocation, and order authority to false.
6. The first operational interface is a local-only CLI with `register`, `start`, `finalize`, and `review`. It has no credential, endpoint, provider, Paper, arm, force, or scheduling option. `run_us_swing_shadow.py` remains unchanged in this milestone.

## Contracts

### Canonical Swing Research Contract

`trading_agent/swing_research_contract.py` defines exactly one contract for `new_high_momentum`:

- hypothesis ID: `H-SWING-NEW-HIGH-RVOL-001`
- lane: `LaneId.SWING_MOMENTUM`
- strategy version: `new_high_rvol_20d_1p5_v1`
- experiment scope: the exact source-bound single-lane scope used by `examples/research/us-swing-new-high-rvol-v1.json`
- parameter, data, cost, and shadow-only portfolio contracts are explicit immutable tuples. The cost contract states that execution costs are not modelled; that fact is a Reviewer blocker rather than an implied performance claim.

### Trial Registration

`register_swing_shadow_trial(...)` accepts an `ExperimentLedgerStore`, query-only `SwingShadowReader`, one signal ID, an aware `registered_at`, and the exact runtime code version.

It verifies all of the following before taking the global Writer lease:

- the swing shadow ledger is initialized and has exactly one matching `signal_created` event;
- the signal lane, strategy version, evidence reference, source key and next regular session match the canonical contract;
- the global `HypothesisRegistration` and `ResearchHypothesisCard` match the canonical source-bound contract;
- new registration is in `[signal.observed_at, next_regular_open)` and its lifecycle registration becomes effective on that next session;
- an existing version, lifecycle event, or trial is exact. Any mismatch is an immutable conflict.

The trial `data_version` is SHA-256 over canonical signal and `signal_created` event bytes. Its evidence budget names one signal, one creation event, and one terminal event. The Writer then appends missing version/lifecycle/trial entries in a single transaction.

### Start And Finalize

`start_swing_shadow_trial(...)` accepts only the trial ledger, shadow reader, signal ID, and an aware time in the trial's planned regular session. It appends exactly one global `started` event and exact replay preserves the original timestamp.

`finalize_swing_shadow_trial(...)` requires a started global event, an observed terminal swing event, and an aware timestamp no earlier than the terminal observation. It hashes the canonical signal plus every swing event through the terminal and appends one `completed` global event. It rejects a missing terminal, changed source/evidence, terminal sequence inconsistency, outside planned end, or a conflicting second terminal. No incomplete source is recast as a zero return or terminal event.

### Independent Review

`SwingShadowReviewEvent` includes trial ID, signal ID, strategy version, experiment scope key, global terminal event key, canonical evidence hashes, terminal kind, reviewer version, reasons, blockers, and authority booleans. A separate `SwingShadowReviewStore` is owner-only, append-only, single-writer, and query-only for readers.

`review_swing_shadow_trial(...)` requires the completed global trial and exact swing signal/events. It emits `continue_collection` only; blockers always include `automatic_state_change_forbidden`, `paper_authority_forbidden`, `cost_model_unmodeled`, and `forward_sample_insufficient`. It never appends lifecycle events and never writes either source ledger.

## CLI

`run_swing_shadow_trial.py` exposes four subcommands:

```text
register --experiment-ledger --shadow-ledger --signal-id --code-version --output-dir
start    --experiment-ledger --shadow-ledger --signal-id --output-dir
finalize --experiment-ledger --shadow-ledger --signal-id --output-dir
review   --experiment-ledger --shadow-ledger --review-ledger --signal-id --output-dir
```

Each report contains only the operation, created/replayed status, terminal/review class, and `external broker mutation: 0`; it excludes paths, keys, hashes, source text, account information, and credentials. Reports and ledgers use mode `600`.

## Verification

- Contract tests cover exact source card/version, pre-open-only registration, replay, lifecycle effective date, source mismatch, and no provider/Paper imports.
- Trial tests cover start window, no terminal without observed swing terminal, exact terminal hashes, no-entry expiry, evidence tampering, and immutable conflict.
- Reviewer tests cover query-only source validation, replay, append-only triggers, and all authority booleans remaining false.
- CLI tests cover help, bad source with no ledger creation, fixture register/start/finalize/review happy path and replay, private artifacts, and no provider/Paper imports.
- Finish with focused tests, full pytest, Ruff, basedpyright, CLI help/bad/happy QA, an independent code review, and small commits.

## Safety Boundary

- No Alpaca, KIS, LS, OpenDART, HTTP, WebSocket, secret loader, broker, execution-store, mutation adapter, or Portfolio Manager import is permitted in the new modules.
- No new Paper account binding, order, cancellation, position, live endpoint, risk limit, champion, allocation, or lifecycle transition is created.
- Existing historical swing shadow rows are not represented as preregistered trials. Only a registration made before the next regular open may create a new global trial.
