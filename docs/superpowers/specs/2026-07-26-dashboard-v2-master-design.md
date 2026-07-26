# Dashboard v2 Master Design — Ember Operations Workstation

Date: 2026-07-26

Status: approved implementation contract
Primary design contract: `dashboard/DESIGN.md`

## 1. Outcome and non-goals

Dashboard v2 exposes the Quant Research OS as nine truthful, navigable workspaces:
Command Center, Overview, Markets, Data Sources, Research, Strategies, Derivatives, Paper, and
System. Every non-presentational value is a bounded, redacted, read-only projection of an
authoritative receipt/read model and opens an Evidence Trace. When authority is missing or invalid,
the section renders the exact unavailable, blocked, error, corrupt, stale, or empty condition; it
does not invent a value.

V2 does not add live-money trading, a provider mutation path, a direct public order control,
periodic polling, a Railway worker, automatic model execution, a paid retry, or a second design
system. The referenced finance pages are inspiration only; no logo, mark, wording, branded asset,
or copied layout ships.

## 2. Locked system boundaries

### 2.1 Public and private routes

Public, keyless, read-only:

- `GET /`, `GET /showcase`, and static same-origin assets;
- `GET /api/health`;
- `GET /api/snapshot`;
- public snapshot WebSocket `/api/realtime/view`.

Private:

- publisher WebSocket `/api/realtime/publish` authenticates with the existing ingest bearer;
- pairing consumes a single-use, short-lived publisher-issued ticket;
- operator endpoints require the existing `Secure; HttpOnly; SameSite` cookie;
- `POST /api/agents/:agentId/interactions` is operator-only;
- authenticated interaction events are never broadcast on the public viewer channel.

Browser JavaScript never sees an ingest token, operator bearer, Hermes session ID, local binding
identifier, broker account identifier, or credential state.

### 2.2 Trading and provider safety

- Real-money trading is permanently prohibited.
- Alpaca mutation remains Paper-only and must reject any base URL other than
  `https://paper-api.alpaca.markets` before network I/O.
- Dashboard public and operator UI has no order form, cancel action, flatten action, or generic
  provider-call action.
- KIS, LS, FRED/ALFRED, Treasury, CFTC, OpenDART, and all providers other than Alpaca Paper remain
  read-only. LS account/order endpoints and WebSocket registration types `1/2` remain prohibited.
- A command that requests Paper work is still only intent: the local executor must pass the
  existing Paper URL, operator-session, arm, risk, reconcile, protective-OCO, cutoff, and EOD-flat
  gates. Release QA uses read-only Hermes commands and performs no Paper mutation.

### 2.3 Redaction boundary

Redaction completes on the Mac mini before serialization. Strict Python and TypeScript schemas
reject unknown fields. The following are prohibited recursively in snapshots, interaction
messages, DOM, browser storage, logs, artifacts, and Railway storage:

- credential, token, cookie, bearer, raw authentication response, or environment value;
- account number, account ID, account fingerprint, or provider user identity;
- raw request/response header, raw provider payload, raw log/stdout/stderr line, or request body;
- absolute path, home-relative path, symlink target, worktree path, credential path, or database
  filename supplied by a source;
- Hermes session ID, local agent/session binding key, claim-store path, or local execution key;
- PID command line, arbitrary process arguments, or arbitrary launchd log text.

Allowed identifiers are opaque public IDs, bounded normalized blocker codes, timestamps, counts,
approved enum states, safe source namespaces, exact content/code SHA-256 where the SHA is itself
public evidence, and redacted terminal receipt IDs.

Final outbound Hermes text passes a denylist plus structural validator after truncation and before
any publisher send. Detection fails closed to a typed `outbound_redaction_failed` terminal; it
never sends a partially redacted response.

### 2.4 Event and cost boundary

- Mac mini owns one persistent publisher WebSocket and `watchfiles` subscriptions on stable parent
  roots. A burst is coalesced into one snapshot rebuild/event.
- Browser owns one initial snapshot GET and one public viewer WebSocket; a paired device may also
  own one authenticated interaction stream.
- Reconnect happens only after disconnect with bounded exponential backoff. Reconnect is not data
  polling.
- No 10-second, 15-second, or other periodic HTTP/DB request, hidden refresh timer, scheduled
  snapshot query, or model heartbeat is permitted.
- Model work begins only after explicit user submit. One interaction UUID can launch at most one
  Hermes process. There are no automatic paid retries.

## 3. Canonical v2 contract

### 3.1 Envelope

`DashboardSnapshotV2` is a strict immutable model:

| Field | Type/constraint | Meaning |
| --- | --- | --- |
| `schema_version` | literal `2` | version discriminator |
| `snapshot_id` | UUID | one projection event; not a local file/session identifier |
| `generated_at` | timezone-aware UTC datetime | projection completion time |
| `source` | literal `local-redacted-projector` | trust boundary declaration |
| `workspaces` | strict object with all nine keys | canonical section-local projections |
| `traces` | max 512 nodes, max 768 edges | all trace IDs referenced by workspace values |
| `projection` | strict metadata | reader versions, redaction policy version, total/projected counts |

Every workspace and independently sourced subsection is a `SourceState<T>`:

| Field | Contract |
| --- | --- |
| `state` | one of `loading`, `empty`, `error`, `blocked`, `unavailable`, `corrupt`, `stale`, `populated` |
| `observed_at` | timezone-aware timestamp or `null` only when authority is absent |
| `freshness` | declared policy ID, age seconds or `null`, and `as_of` |
| `blocker_code` | normalized enum code when blocked/unavailable/error/corrupt; otherwise `null` |
| `summary` | plain-language, redacted, max 160 characters |
| `items/value` | present only where the schema permits it for the state |
| `total_count` | authority count before cap |
| `projected_count` | emitted count |
| `truncated` | `total_count > projected_count` |
| `trace_id` | required for every final state, including valid empty and unavailable |

The parser rejects:

- unknown fields, missing state metadata, naive datetimes, negative counts, count inconsistencies,
  dangling trace IDs, cyclic trace graphs, unknown blocker codes, future observations beyond the
  bounded clock-skew allowance, and payloads over the configured size;
- populated current quote fields without entitlement, allowed redistribution, current capability
  health, and freshness;
- a valid-empty state without a successful source receipt;
- a blocked/corrupt/unavailable state without an allowed terminal;
- mixed v1/v2 objects or a top-level version not equal to the selected parser.

### 3.2 State resolution

State precedence is:

`corrupt > error > blocked > unavailable > stale > populated | empty`.

`loading` exists only in the browser before the first projection/route render; the publisher does
not serialize indefinite loading. Valid empty means the reader succeeded at the declared point in
time and found zero valid rows. A read failure is `error` or `corrupt`, never empty. An absent
entitlement/authority is `unavailable`; a present safety or quality gate is `blocked`. Stale data
may remain visible with observation time and removed actionability, but it cannot be restyled as
current.

### 3.3 Evidence Trace

Node kinds:

`source_receipt`, `observation`, `dataset`, `code_revision`, `hypothesis`, `trial`,
`reviewer_decision`, `lifecycle_decision`, `paper_receipt`, `process_receipt`,
`deployment_receipt`, `blocker_terminal`.

Edge kinds:

`derived_from`, `observed_by`, `bound_to`, `evaluated_in`, `reviewed_by`, `decided_by`,
`executed_as`, `reconciled_by`, `deployed_as`, `blocked_by`.

Each graph is a bounded DAG. Every graph contains at least one source receipt and ends in a
domain-appropriate Reviewer, Paper, typed source/system decision, or blocker terminal. Research
and strategy values never terminate at an unreviewed trial. Paper values never terminate at a
provider response that has not entered the finalized Paper ledger. A missing edge/node makes only
the referencing section `corrupt`; the renderer does not synthesize a replacement.

The browser renders the ordered accessible list as source of truth and an SVG graph as an
enhancement. Drawer focus, inert background, Escape, return-focus, node keyboard navigation, and
route-change behavior are fixed by `dashboard/DESIGN.md`.

## 4. Field-level source-state matrix

Stable watch roots are Mac-mini-only paths expressed relative to the configured output/state roots.
They may guide `watchfiles`, but the resolved absolute path never crosses the projector boundary.
“Cap” is the v2 projection cap; all collections also emit
`total_count/projected_count/truncated`.

### 4.1 Command Center and Overview

| Workspace / fields | Authoritative reader and model | Stable watch root / event | Freshness or point-in-time rule | Cap | State mapping and blocker code | Trace terminal | Prohibited fields | Module ownership |
| --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| Command Center: public agent roster (`agent_id`, label, role, runtime state) | launchd typed receipt adapter + `AgentViewV2` | launchd event inventory; `outputs/system` parent | latest typed receipt at snapshot time; PID alone never current | 12 agents | no typed receipt `unavailable/agent_runtime_receipt_missing`; stale receipt `stale/agent_runtime_receipt_stale`; failed exit `blocked/agent_runtime_failed` | `process_receipt` or `blocker_terminal` | PID command, argv, plist/log path, environment | `trading_agent/dashboard_agents.py`, new `trading_agent/dashboard_projection_agents.py` |
| Command Center: interaction receipt (`id`, agent, state, redacted command/response times) | Railway immutable interaction store and CAS model | authenticated publisher/operator WebSocket event; no filesystem poll | point-in-time CAS state ordered by `updated_at`; no state regression | 50 recent/private | public viewer `unavailable/operator_session_required`; relay absent `blocked/publisher_relay_offline`; uncertain local seam `blocked/execution_uncertain` | `process_receipt` or `blocker_terminal` | Hermes session ID, binding key/path, raw stdout/stderr, operator secret | `dashboard/src/store.ts`, `dashboard/src/realtime.ts`, `trading_agent/dashboard_commands.py`, new `trading_agent/dashboard_hermes_sessions.py` |
| Command Center: binding/claim health (existence only, never IDs) | owner-only binding and claim readers | owner-only dashboard-Hermes state root, watched locally | exact interaction UUID claim and terminal receipt; restart-safe | 12 agent summaries | unsafe mode/link/symlink `corrupt/local_state_permissions_invalid`; missing resume `blocked/hermes_resume_missing`; duplicate `corrupt/duplicate_execution_claim` | `process_receipt` or `blocker_terminal` | session ID, local filename/path, claim payload, account data | new `trading_agent/dashboard_hermes_sessions.py`, new `trading_agent/dashboard_execution_claims.py` |
| Overview: market/session posture | `KisKrSessionCalendarStore` plus new strict `DashboardUsSessionReceiptReader` and `MarketSessionViewV2` | `outputs/live_sessions`, calendar receipt parents | latest completed session/bar for its market; missing authority is unavailable | 2 markets | missing calendar `unavailable/market_calendar_missing`; stale calendar `stale/market_calendar_stale`; closed is populated state, not error | typed `source_receipt` decision or `blocker_terminal` | raw provider payload, credential state, account identity | `trading_agent/kis_kr_session_calendar_store.py`, new `trading_agent/dashboard_market_calendar.py`, new `trading_agent/dashboard_projection_overview.py` |
| Overview: blocker digest | all nine workspace `SourceState` results | snapshot rebuild event | same snapshot ID only; never join across epochs | 12 blockers | zero blockers after successful reads `empty`; read failure stays section-local; mismatched snapshot `corrupt/mixed_snapshot_epoch` | underlying terminal for each blocker | raw exception/log/path | new `trading_agent/dashboard_projection_overview.py` |
| Overview: research/Paper/system summaries | exact workspace projections, not independent readers | same canonical v2 object | same `snapshot_id`; summary cannot be fresher than source workspace | 3 summaries | mirrors section state; never converts unavailable to zero/healthy | referenced workspace terminal | extra values not present in underlying workspace | TypeScript normalizer/render owner in `dashboard/src/schema.ts`, `dashboard/src/workspaces/overview.ts` |

### 4.2 Markets and Data Sources

| Workspace / fields | Authoritative reader and model | Stable watch root | Freshness or point-in-time rule | Cap | State mapping and blocker code | Trace terminal | Prohibited fields | Module ownership |
| --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| Markets: completed bars/session values | append-only session evidence and market-data runtime receipt readers | `outputs/live_sessions` | latest completed bar in current authoritative NY/KR session; historical warmup never becomes current | 24 points/market | no completed current-session bar `blocked/completed_bar_missing`; receipt stale `stale/market_observation_stale`; closed session is populated/closed | accepted `source_receipt` or `blocker_terminal` | in-progress bar, raw trade/quote payload, guessed open state | `trading_agent/us_market_data_runtime_receipt_query.py`, `trading_agent/kis_kr_market_projection.py`, new `trading_agent/dashboard_projection_markets.py` |
| Markets: quote/spread actionability | `us_quote_actionability_evidence.py` and capability/entitlement projection | `outputs/live_sessions` | same-symbol current completed-bar/quote receipt and declared TTL | 50 symbols | missing spread `blocked/spread_missing`; no real-time entitlement `unavailable/realtime_entitlement_missing`; derived-only `blocked/current_quote_not_licensed`; stale `stale/quote_stale` | `reviewer_decision` or `blocker_terminal` | account/subscription identity, raw quote payload/header | `trading_agent/us_quote_actionability_evidence.py`, `trading_agent/alpaca_sip_live_actionability.py`, new `trading_agent/dashboard_projection_markets.py` |
| Data Sources: FRED/ALFRED capability, vintages, revisions | capability reader and ALFRED revision panel model | `outputs/source_evidence` | latest terminal run; vintage selected `<= as_of`, never newest future revision | 20 series / 24 points | missing run `unavailable/source_receipt_missing`; revision mismatch `corrupt/vintage_lineage_invalid`; aged by policy `stale/source_receipt_stale` | typed source acceptance or `blocker_terminal` | API key, request URL query if secret-bearing, raw response | `trading_agent/fred_alfred_capability.py`, `trading_agent/alfred_revision_panel.py`, new `trading_agent/dashboard_projection_sources.py` |
| Data Sources: Treasury curve | `TreasuryYieldStore` receipt/run readers | `outputs/source_evidence` | latest terminal curve with observation date `<= as_of`; report aged by declared daily policy | 24 tenors/points | absent `unavailable/treasury_receipt_missing`; malformed curve `corrupt/treasury_curve_invalid`; aged `stale/treasury_curve_stale` | typed source acceptance or blocker | raw XML, transport headers, local DB path | `trading_agent/treasury_yield_store.py`, `trading_agent/treasury_yield_artifact.py`, new `trading_agent/dashboard_projection_sources.py` |
| Data Sources: CFTC TFF | `CftcTffStore` receipt/run readers | `outputs/source_evidence` | latest published report `<= as_of`; weekly publication age displayed | 20 contracts | absent `unavailable/cftc_receipt_missing`; incompatible report `corrupt/cftc_report_invalid`; late publication `stale/cftc_report_stale` | typed source acceptance or blocker | raw response, request headers/path | `trading_agent/cftc_tff_store.py`, `trading_agent/cftc_tff_artifact.py`, new `trading_agent/dashboard_projection_sources.py` |
| Data Sources: OpenDART | KR source receipt/read model and terminal source run | `outputs/live_sessions` | latest terminal collection for selected KR session | 50 disclosures | valid zero disclosures `empty`; missing run `unavailable/opendart_run_missing`; failed terminal `error/opendart_collection_failed`; mismatched receipt `corrupt/opendart_receipt_invalid` | source acceptance or blocker | API key, corp/account identity, raw page | `trading_agent/opendart_collection.py`, `trading_agent/research_evidence_read_model.py`, new `trading_agent/dashboard_projection_sources.py` |
| Data Sources: KIS market/ranking | KIS receipt store, ranking/market projections, retry audit | `outputs/live_sessions` | selected authoritative KR session; current only with current receipt and calendar | 50 rows | valid zero `empty`; entitlement absent `unavailable/kis_entitlement_missing`; retry/coverage gate `blocked/kis_coverage_incomplete`; hash invalid `corrupt/kis_receipt_invalid` | Reviewer/source decision or blocker | credential, account identity, raw payload/header | `trading_agent/kis_kr_market_receipt_store.py`, `trading_agent/kis_kr_market_projection.py`, `trading_agent/kis_retry_audit.py`, new `trading_agent/dashboard_projection_sources.py` |
| Data Sources: LS news | terminal NWS source run and research evidence read model | `outputs/live_sessions` | selected KR collection date and terminal run | 50 catalysts | valid zero `empty`; no entitlement `unavailable/ls_entitlement_missing`; failed handshake/run `error/ls_collection_failed`; receipt mismatch `corrupt/ls_receipt_invalid` | source acceptance or blocker | OAuth token, raw WebSocket frame, account registration, local path | `trading_agent/ls_nws_collection.py`, `trading_agent/research_evidence_read_model.py`, new `trading_agent/dashboard_projection_sources.py` |
| Data Sources: Alpaca market data/options/Paper capability | capability and runtime receipt projections only | `outputs/live_sessions`, `outputs/derivatives`, `outputs/paper` | per-capability TTL and entitlement; Paper account read is not a market-data entitlement | 30 capabilities | absent `unavailable/alpaca_capability_missing`; current redistribution disallowed `blocked/redistribution_not_allowed`; stale runtime `stale/alpaca_runtime_stale` | typed capability decision or blocker | API key, account ID/fingerprint, raw response/header | `trading_agent/alpaca_option_chain_capability.py`, `trading_agent/alpaca_sip_runtime_evidence.py`, new `trading_agent/dashboard_projection_sources.py` |

### 4.3 Research and Strategies

| Workspace / fields | Authoritative reader and model | Stable watch root | Freshness or point-in-time rule | Cap | State mapping and blocker code | Trace terminal | Prohibited fields | Module ownership |
| --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| Research: source/paper catalog | research evidence read model and registered source evidence | `outputs/experiment_control`, `outputs/live_sessions` | latest immutable registration `<= as_of`; paper publication time retained | 50 sources/papers | valid catalog zero `empty`; authority missing `unavailable/research_catalog_missing`; broken receipt link `corrupt/research_lineage_invalid` | Reviewer acceptance or blocker | local document path, raw copyrighted body, credentials | `trading_agent/research_evidence_read_model.py`, `trading_agent/arxiv_research_collection.py`, new `trading_agent/dashboard_projection_research.py` |
| Research: hypothesis queue | source-driven queue reader/models | `outputs/experiment_control` | queue state at snapshot time; no future registration | 50 hypotheses | zero after successful read `empty`; missing source binding `blocked/hypothesis_source_missing`; invalid ordering `corrupt/hypothesis_queue_invalid` | `reviewer_decision` or `blocker_terminal` | free-form private notes beyond bounded summary, path | `trading_agent/source_driven_hypothesis_queue.py`, `trading_agent/research_hypothesis_registration.py`, new `trading_agent/dashboard_projection_research.py` |
| Research: causal dataset identity | dataset catalog/input binding readers, exact SHA | `outputs/experiment_control` | exact point-in-time manifest selected by trial binding; no “latest” substitution | 24 datasets | missing SHA `blocked/dataset_sha_missing`; mismatched manifest `corrupt/dataset_binding_invalid`; reader missing `unavailable/dataset_catalog_missing` | Reviewer or blocker | dataset path, raw rows, credential-bearing source config | `trading_agent/intraday_research_dataset_catalog.py`, `trading_agent/intraday_research_input_binding.py`, new `trading_agent/dashboard_projection_research.py` |
| Research: evidence gaps/queue status | Reviewer/prerequisite typed decisions | `outputs/experiment_control` | same trial/dataset epoch | 24 gaps | no gaps after review `empty`; pending authority `blocked/reviewer_pending`; contradictory decision `corrupt/reviewer_decision_conflict` | `reviewer_decision` or blocker | raw exception, internal prompt/session ID | `trading_agent/intraday_research_prerequisite.py`, `trading_agent/intraday_research_reviewer.py`, new `trading_agent/dashboard_projection_research.py` |
| Strategies: lane/version/trial | lane registry and experiment ledger read-only snapshots | `outputs/lane_control`, `outputs/experiment_control` | exact lane manifest/version and trial binding at snapshot time | 50 trials | valid no trials `empty`; registry absent `unavailable/lane_registry_missing`; binding mismatch `corrupt/trial_binding_invalid` | Reviewer/lifecycle or blocker | DB path, writable handle, provider payload | `trading_agent/lane_registry_store.py`, `trading_agent/experiment_ledger_store.py`, new `trading_agent/dashboard_projection_strategies.py` |
| Strategies: walk-forward/overfit diagnostics | immutable trial/review artifacts | `outputs/experiment_control` | selected trial version only; no cross-version aggregation | 24 windows/diagnostics | insufficient windows `blocked/walk_forward_insufficient`; diagnostic absent `blocked/overfit_diagnostic_missing`; mixed version `corrupt/trial_version_mismatch` | `reviewer_decision` or blocker | synthetic profitability claim, raw dataset rows/path | `trading_agent/intraday_research_trial.py`, `trading_agent/intraday_research_audit_trials.py`, new `trading_agent/dashboard_projection_strategies.py` |
| Strategies: Reviewer/lifecycle | lane review store and lifecycle controller decision models | `outputs/lane_control` | latest immutable decision for exact lane/version; terminal beats earlier pending | 50 decisions | no review `blocked/reviewer_missing`; lifecycle contradiction `corrupt/lifecycle_conflict`; rejected remains populated/rejected | `reviewer_decision`, `lifecycle_decision`, or blocker | reviewer prompt/session, local artifacts path | `trading_agent/lane_review_store.py`, `trading_agent/lifecycle_controller.py`, new `trading_agent/dashboard_projection_strategies.py` |
| Strategies: champion/Allocation Manager lock | experiment ledger champion authority and lifecycle allocation lock | `outputs/experiment_control`, `outputs/lane_control` | persisted authority for exact version; no inferred champion from score | 12 lanes | no champion `empty`; promotion gate `blocked/champion_authority_missing`; allocation locked `blocked/allocation_manager_locked`; conflicting authority `corrupt/champion_conflict` | `lifecycle_decision` or blocker | mutable allocation action, account/risk secrets | `trading_agent/experiment_ledger_store.py`, `trading_agent/lifecycle_authority_policy.py`, new `trading_agent/dashboard_projection_strategies.py` |

### 4.4 Derivatives and Paper

| Workspace / fields | Authoritative reader and model | Stable watch root | Freshness or point-in-time rule | Cap | State mapping and blocker code | Trace terminal | Prohibited fields | Module ownership |
| --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| Derivatives: option chain/contracts | read-only option chain/contract store projections and capability | `outputs/derivatives` | latest successful snapshot `<= as_of`; “current” requires active real-time entitlement and TTL | 50 contracts | valid zero `empty`; no entitlement `unavailable/options_entitlement_missing`; expired/research-only `blocked/current_quote_not_licensed`; hash invalid `corrupt/options_receipt_invalid` | Reviewer/source decision or blocker | API key, raw option payload/header, account identity | `trading_agent/alpaca_option_chain_store.py`, `trading_agent/alpaca_option_contract_store.py`, `trading_agent/alpaca_option_chain_projection.py`, new `trading_agent/dashboard_projection_derivatives.py` |
| Derivatives: IV/skew/term structure | option surface/skew/term models bound to exact spot/chain receipts | `outputs/derivatives`, `outputs/live_sessions` | all inputs share compatible observation window and exact receipt refs | 24 points each | missing spot `blocked/derivative_spot_missing`; mixed epochs `corrupt/derivative_epoch_mismatch`; stale inputs `stale/derivative_surface_stale` | Reviewer or blocker | estimated “live” quote, raw chain, local path | `trading_agent/alpaca_option_surface.py`, `trading_agent/alpaca_option_skew.py`, `trading_agent/alpaca_option_term_structure.py`, new `trading_agent/dashboard_projection_derivatives.py` |
| Derivatives: futures security master/roll | futures roll security master models and source receipt | `outputs/derivatives` | contract/roll rule effective at `as_of`; never choose by ticker string alone | 50 contracts | master absent `unavailable/futures_master_missing`; roll window absent `blocked/roll_window_missing`; conflicting contract `corrupt/futures_master_invalid` | Reviewer/source decision or blocker | provider account, raw payload/path | `trading_agent/futures_roll_security_master.py`, `trading_agent/futures_roll_security_master_models.py`, new `trading_agent/dashboard_projection_derivatives.py` |
| Derivatives: futures/CFTC positioning | futures positioning context bound to CFTC receipt/master | `outputs/derivatives`, `outputs/source_evidence` | report and contract mapping valid at point in time; weekly age displayed | 24 points | CFTC absent `unavailable/cftc_receipt_missing`; mapping absent `blocked/futures_mapping_missing`; mixed report `corrupt/positioning_context_invalid` | Reviewer or blocker | raw CFTC response/path | `trading_agent/futures_positioning_context.py`, `trading_agent/cftc_tff_store.py`, new `trading_agent/dashboard_projection_derivatives.py` |
| Paper: finalized PnL/exposure counts | finalized lane daily snapshot via read-only lane registry | `outputs/lane_control` | latest finalized session `<= as_of`; never live broker buying power | 1 ledger / 50 positions | no finalized snapshot `unavailable/paper_finalized_ledger_missing`; quality incomplete `blocked/paper_verification_incomplete`; stale session `stale/paper_ledger_stale` | `paper_receipt` or blocker | account ID/fingerprint, unverified buying power, raw broker payload | `trading_agent/lane_registry_store.py`, new `trading_agent/dashboard_projection_paper.py` |
| Paper: positions/open orders | new strict `DashboardPaperLedgerReader` over finalized ledger/order evidence, never provider client | `outputs/paper`, `outputs/lane_control` | same finalized/reconciled session and cutoff epoch | 50 each | valid zero `empty`; reconcile pending `blocked/paper_reconcile_pending`; cross-epoch `corrupt/paper_epoch_mismatch` | `paper_receipt` or blocker | writable broker client, cancel/submit action, account identity | `trading_agent/alpaca_paper_order_reads.py`, new `trading_agent/dashboard_paper_read_model.py`, new `trading_agent/dashboard_projection_paper.py` |
| Paper: entry/OCO/reconcile/cutoff/EOD-flat lifecycle | immutable broker/order/trade-update evidence and protective OCO lifecycle | `outputs/paper` | ordered lifecycle for exact recommendation/order identity; terminal receipts immutable | 50 lifecycles | missing protection `blocked/protective_oco_missing`; cutoff/EOD pending `blocked/eod_flat_pending`; ordering conflict `corrupt/paper_lifecycle_invalid` | `paper_receipt` or blocker | mutation control, raw broker response/header, account | `trading_agent/broker_order_evidence.py`, `trading_agent/paper_protective_oco_lifecycle.py`, `trading_agent/trade_update_receipts.py`, new `trading_agent/dashboard_projection_paper.py` |

### 4.5 System

| Workspace / fields | Authoritative reader and model | Stable watch root / event | Freshness or point-in-time rule | Cap | State mapping and blocker code | Trace terminal | Prohibited fields | Module ownership |
| --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| System: exactly M0–M10 | allowlisted machine-readable milestone receipt model | `outputs/system` | one latest typed receipt per exact milestone; prose/checklist is never authority | exactly 11 | absent entry `unavailable/milestone_authority_missing`; failed receipt `blocked/milestone_failed`; duplicate/conflict `corrupt/milestone_receipt_conflict` | Reviewer/process receipt or blocker | checkpoint prose inference, path, raw log | new `trading_agent/dashboard_system_evidence.py`, new `trading_agent/dashboard_projection_system.py` |
| System: launchd schedule/PID/exit | typed launchd inventory plus current process/exit receipt | launchd change observation + `outputs/system` | PID requires matching fresh typed receipt; stale PID is not healthy | 50 jobs | no receipt `unavailable/launchd_receipt_missing`; stale PID `stale/launchd_pid_stale`; nonzero terminal `blocked/launchd_job_failed` | `process_receipt` or blocker | argv, environment, plist/stdout/stderr paths, raw log line | `trading_agent/dashboard_agents.py`, new `trading_agent/dashboard_system_evidence.py` |
| System: stage outcomes/stdout-stderr summary | allowlisted typed stage result codes | `outputs/system` | exact run ID and terminal receipt; only normalized bounded summary | 50 stages | missing terminal `blocked/stage_terminal_missing`; unknown/raw result `unavailable/stage_code_unknown`; hash/order invalid `corrupt/stage_receipt_invalid` | `process_receipt` or blocker | arbitrary stdout/stderr, stack trace, path, env | new `trading_agent/dashboard_system_evidence.py` |
| System: Railway deploy/health | new strict `DashboardRailwayReceipt` captured by release workflow | deployment event receipt; no periodic health poll | explicit deployment ID/SHA and observation time; live health is point-in-time only | 12 deploys | missing receipt `unavailable/railway_receipt_missing`; SHA mismatch `corrupt/deployment_sha_mismatch`; unhealthy `blocked/railway_health_failed` | `deployment_receipt` or blocker | variable values, auth token, internal DB URL | new `trading_agent/dashboard_system_evidence.py`, new `trading_agent/dashboard_projection_system.py` |
| System: event relay | publisher/viewer connection typed events | WebSocket connect/disconnect event | last connection transition and current socket ownership | 12 transitions | disconnected `blocked/publisher_relay_offline`; aged canary `stale/relay_receipt_stale`; invalid ordering `corrupt/relay_event_order_invalid` | `process_receipt` or blocker | bearer, peer account/session, raw frames | `trading_agent/dashboard_relay.py`, `dashboard/src/realtime.ts`, new `trading_agent/dashboard_projection_system.py` |

## 5. Module and storage ownership

### 5.1 Python

- `trading_agent/dashboard_models.py`: strict v1 models remain compatibility-only; strict v2
  envelope, states, workspace projections, graph, caps, and blocker enums are added in a separate
  `dashboard_models_v2.py` to avoid an oversized mixed module.
- `trading_agent/dashboard_snapshot.py`: v1 characterization/down-projection adapter; canonical v2
  orchestration moves to `dashboard_snapshot_v2.py`.
- `trading_agent/dashboard_projection_*.py`: one read-only projection owner per domain shown in the
  matrix. These modules accept readers and time; they do not open credentials or provider clients.
- `run_dashboard_publisher.py`: stable watch-root inventory, burst coalescing, canonical v2 publish,
  and explicit CLI dry-run. It keeps one WebSocket and no polling.
- `dashboard_hermes_sessions.py` and `dashboard_execution_claims.py`: local-only owner-mode-600
  binding and exactly-once claim/terminal receipts. Their values never enter v2.

### 5.2 Dashboard TypeScript

- `dashboard/src/schema.ts`: strict v1/v2 parsers, discriminated union at ingest, and one canonical
  v2 browser model. No `any`, type assertion, non-null assertion, or ignore directive.
- `dashboard/src/store.ts`: separate v1 rollback and v2 canonical storage; transactional ingest and
  compare-and-set interaction lifecycle.
- `dashboard/src/workspace_tabs.ts`: nine hash routes, fallback to `#command-center`, roving
  navigation, reload/back-forward.
- `dashboard/src/render.ts`: shared source-state and trace primitives only; each workspace owns a
  bounded renderer under `dashboard/src/workspaces/`.
- `dashboard/src/evidence_trace.ts`: graph validation, accessible list/SVG view, focus trap and
  focus return.
- CSS ownership remains split by purpose: tokens/base, shell/layout, primitives/components,
  data/chart/table components, workspace composition, showcase, and responsive/adaptive rules.

## 6. Rolling v1/v2 storage, down-projection, and rollback

V1 and v2 are never written into the same canonical row or key.

1. Characterize current v1 ingest, store, restart, viewer, event, and rollback behavior before
   changing a parser.
2. Deploy a compatibility server that accepts strict v1 or strict v2 envelopes, normalizes either
   exactly once to canonical v2 for the browser, and rejects mixed/unknown payloads.
3. Store an accepted v1 payload in the existing v1 storage and its normalized v2 in separate v2
   storage. Store an accepted v2 payload in canonical v2 storage and generate a bounded redacted
   v1 down-projection in retained v1 rollback storage. A failure in either write leaves the prior
   pair intact.
4. Viewer endpoints and WebSocket emit canonical v2 only after the compatibility server proves
   restart/read/event behavior. Before that switch, existing v1 viewers keep their contract.
5. Deploy the compatibility server before changing the Mac mini publisher.
6. Switch the publisher to strict v2. Prove exact SHA, public snapshot, viewer event, operator
   pairing, one explicit read-only command, redaction, service count, and idle behavior.
7. Roll back the application to the recorded compatibility binary/commit. It must read the
   separately retained v1 down-projection without asking the v2 publisher to republish. Record the
   rollback snapshot ID and health receipt, then return to v2 and prove recovery.
8. Only after that proof, remove v1 ingest acceptance in a later atomic commit. Keep explicit
   v1/unknown rejection tests and retain the bounded v1 down-projection for the documented
   rollback window.

At no point does a v1 parser “best effort” parse v2 or vice versa. An older v1 payload cannot
overwrite a newer canonical v2 snapshot after publisher cutover. Snapshot selection compares
schema rollout epoch and generated time, not arrival order alone.

## 7. Exactly-once Hermes execution

### 7.1 Binding

- Bind only a public `agent_id` to a local Hermes session ID in an owner-owned regular file with
  exact mode `0600`, one hard link, no symlink, atomic replace, directory fsync, and an exclusive
  lock.
- First execution captures the new session ID from a strict machine-readable Hermes result. Later
  execution uses exact argv `hermes --resume <id> ...`; never a shell string.
- Missing, corrupt, unsafe, or unresumable binding fails closed. It does not start a replacement
  paid session. Reset/rebind is an explicit local CLI operation, not a dashboard action.
- Different public agent IDs never share one binding. Railway receives neither the session ID nor
  any binding metadata.

### 7.2 Claim and terminal receipt

- Before process launch, atomically claim the Railway interaction UUID in a durable local store.
- Claim states are `claimed`, `process_started`, and terminal `completed | failed | uncertain`.
  The store records sanitized times and result hashes locally.
- A duplicate delivery returns the existing public terminal/uncertain result and never launches.
- Crash/disconnect at claim, running-send, process-start, process-exit, or terminal-send keeps the
  invocation counter at most one. If launch status cannot be proven, state is `uncertain`; no
  automatic retry follows.
- Railway interaction states use compare-and-set:
  `queued -> running -> completed|failed|uncertain`. Late/duplicate messages cannot regress or
  replace a terminal result.

### 7.3 Outbound redaction and Paper gate

Hermes stdout, stderr-derived messages, timeout text, and exceptions are bounded locally, normalized
to approved text, scanned for secret/account/session/path/header/raw-payload canaries, then
validated by the strict outbound schema. On any hit the publisher sends only
`outbound_redaction_failed`.

Commands default to research/read-only. Any request that could mutate Paper must go through the
existing operator session and exact Paper URL/arm/risk/reconcile/OCO/cutoff/EOD-flat chain. The
dashboard adds no shortcut, generic tool invocation, or automatic approval.

## 8. Railway rollout and rollback contract

Discovered immutable targets:

| Kind | Name | ID |
| --- | --- | --- |
| Project | existing dashboard project | `ee149dc8-82b8-46e7-8ef7-582400fed6f9` |
| Environment | production | `8b37a20f-6b0d-4137-a787-ad90b4b482b9` |
| Service | `observatory` | `a7cae053-9289-4120-b5ac-7a0aefc36778` |
| Service | `Postgres` | `21b11148-2386-47a4-b2dd-2a8dfbce94bd` |

The service count before and after every rollout step is exactly two: `observatory` plus the
existing `Postgres`. No worker, cron service, replica for polling, or new database is created.
Commands must select the exact project/environment/service IDs and may list variable names but
never values.

For each production-affecting commit:

1. record full Git SHA and confirm it is based on current `origin/main`;
2. push non-force;
3. deploy/wait for `observatory` and record deployment ID, status, URL, and commit SHA;
4. verify `/api/health`, strict public snapshot, one public viewer event, public command rejection,
   operator pairing/cookie flags, and private event boundary;
5. confirm exact two-service inventory, no new periodic requests, and no secret/path/session canary;
6. during publisher cutover, prove compatibility server first, then v2 publisher;
7. exercise rollback to the recorded compatibility commit against retained v1 storage, then return
   to v2. A service-count drift or SHA mismatch aborts the rollout.

Extra-service drift is a hard blocker `railway_service_count_drift`, not accepted debt.

## 9. Adversarial invariants

- **Stale v1/v2 rollback:** a delayed v1 ingest cannot overwrite newer v2; rollback reads the
  separately retained down-projection; removal of v1 acceptance occurs only after recorded live
  rollback/recovery.
- **Secret/path/session leakage:** recursive schema, serialized JSON, interaction store, DOM,
  browser storage, logs, and evidence scans inject and reject credential, account, header, raw
  payload, Unix/macOS/Windows path, Hermes session, and binding-key canaries.
- **Read failure versus valid empty:** a successful zero-row receipt maps to empty with trace;
  missing file/DB, SQLite error, schema error, or hash mismatch maps to unavailable/error/corrupt.
- **Paid duplicate execution:** duplicate interaction delivery, reconnect, late result, and every
  crash seam preserve an invocation count of zero or one and never auto retry.
- **Derivatives entitlement:** current quote/IV language requires entitlement, redistribution,
  current capability health, and freshness. Expired, delayed, derived-only, or research artifacts
  are research-only/unavailable.
- **Stale PID/log canaries:** PID without a current typed receipt is stale/unavailable, never
  healthy. Arbitrary log lines, secrets, paths, and misleading “success” text never become system
  state.
- **Railway extra-service drift:** any inventory other than the exact two discovered services
  blocks deploy and rollback.
- **Point-in-time joins:** future-dated source, mixed snapshot epoch, mismatched dataset SHA,
  conflicting Reviewer decision, and cross-session Paper records fail only their affected section
  closed.

## 10. Implementation acceptance

Implementation is accepted only when:

- all nine routes and all eight canonical states are exercised by strict fixtures;
- every displayed value/state has a non-dangling source-to-terminal trace;
- source caps, freshness, point-in-time rules, watch roots, and module ownership match this matrix;
- font binaries and unmodified OFL license artifacts are same-origin and hash-pinned;
- production browser QA passes at 375, 768, and 1280px, 200% zoom, keyboard, screen reader
  structure, reduced motion, CJK/unbroken strings, and axe with zero violations/incomplete;
- dashboard typecheck, Biome, Bun tests/build and changed-Python pytest, Ruff, basedpyright, and
  no-excuse audits pass;
- CLI help, malformed input, redacted happy dry-run, leakage, idle, duplicate execution,
  entitlement, stale receipt, and rollback scenarios produce captured artifacts;
- the exact deployed SHA is healthy, public/private boundaries hold, viewer and explicit private
  read-only command events are observed, and Railway still has exactly two services.
