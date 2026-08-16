# Outcome-First 6-Agent Slice 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development task-by-task.

**Goal:** Connect existing Day recommendations/terminal events and Swing shadow signals/events to research actions, then carry terminal outcomes into the next bounded evidence without calculating prices from model prose or placing orders.

**Architecture:** Enrich the existing Day source projection from its read-only recommendation database, checkpoints, risk screen, and existing trade-signal outbox. The Day action only verifies and references an already-produced recommendation/signal/event; it does not create entry/stop/targets. The Swing action only verifies and advances/reviews existing shadow state through the current shadow engine/store. Both return existing artifact identities. Their immutable events are already collected on later ticks and become feedback payloads. No Alpaca call occurs in this slice.

**Critical discovery:** The current Day journal stores checkpoint time/last close, recommendations and terminal events, but not full OHLC bars. Therefore this slice must not claim to recompute a recommendation from a new bar. A completed checkpoint can be shown as bar provenance; publication requires an existing `TradeSignalEnvelope`/recommendation pair. Missing existing output is typed `NO_ACTION(no_setup)` rather than a fabricated signal.

---

## Task 1: Put Day outputs and outcomes in bounded evidence

**Files:**
- Modify: `trading_agent/research_agent_primary_admission.py`
- Modify: `trading_agent/research_agent_source_adapters_primary.py`
- Modify: `tests/test_research_agent_primary_admission.py`
- Modify: `tests/test_research_agent_sources.py`

- [ ] Write failing tests proving the Day payload contains bounded latest checkpoints (`symbol`, `processed_at`, `last_close`), recommendations with timestamp/entry/stop/targets/rationale/state, and each recommendation's immutable event history.
- [ ] Prove current-session admission still rejects session closed, stale, missing spread, and completed checkpoint unavailable.
- [ ] Read SQLite with `mode=ro` and `PRAGMA query_only=ON`; sort deterministically and cap recommendations/events/checkpoints at 32 each so the 48 KiB evidence limit remains enforced.
- [ ] Add Day subject refs for session, recommendation IDs and terminal event identities using compact hashes where needed.
- [ ] Preserve archive wrapping so the same real outputs are research-only after the session.
- [ ] Run `uv run pytest -q tests/test_research_agent_primary_admission.py tests/test_research_agent_sources.py` and commit `feat(agent): include day outcomes in evidence`.

## Task 2: Verify existing Day recommendation artifacts

**Files:**
- Create: `trading_agent/research_agent_day_actions.py`
- Create: `tests/test_research_agent_day_actions.py`
- Modify: `trading_agent/research_agent_actions.py`
- Modify: `trading_agent/research_agent_service_runtime.py`
- Modify: `tests/test_research_agent_actions.py`

- [ ] Write RED tests for `PUBLISH_RECOMMENDATION` resolving an existing recommendation and matching existing `TradeSignalEnvelope`; assert timestamp, entry, stop, 1R/2R targets, rationale and event/outcome refs.
- [ ] Reject signal/recommendation disagreement as `authority_artifact_unresolved`.
- [ ] Return `NO_ACTION(no_setup)` when the admitted payload has no existing recommendation/signal. Keep session/stale/spread/bar blockers in deterministic Primary admission.
- [ ] Support `REVIEW_OPEN_STATE` by resolving the latest existing recommendation event; terminal outcomes produce a completed event artifact, open states reference the recommendation and latest event.
- [ ] Dispatch only Day `PUBLISH_RECOMMENDATION`/`REVIEW_OPEN_STATE` to the configured client. LLM never supplies or changes a price.
- [ ] Wire the client with existing `day_session_root`; every file/database read is private, current-user and read-only. Do not call any broker API.
- [ ] Run focused tests and commit `feat(agent): resolve day recommendation outcomes`.

## Task 3: Resolve and advance existing Swing shadow state

**Files:**
- Create: `trading_agent/research_agent_swing_actions.py`
- Create: `tests/test_research_agent_swing_actions.py`
- Modify: `trading_agent/research_agent_source_adapters_research.py`
- Modify: `trading_agent/research_agent_actions.py`
- Modify: `trading_agent/research_agent_service_runtime.py`
- Modify: `tests/test_research_agent_sources.py`
- Modify: `tests/test_research_agent_actions.py`

- [ ] Expose Swing signal ID and each event ID as safe subjects in the existing Swing evidence.
- [ ] Write RED tests for `REVIEW_OPEN_STATE` resolving a signal/event payload and returning the latest event artifact.
- [ ] For a completed `SwingDailySource`, use existing `advance_swing_shadow_session()` and `SwingShadowStore.writer()`; preserve same-bar stop-first behavior already enforced by the engine.
- [ ] Do not create a new signal from prose. `PROPOSE_HYPOTHESIS` without an existing Swing signal/daily-source artifact remains `required_evidence_unavailable`.
- [ ] Terminal shadow event is `COMPLETED`; unchanged open state is `NO_ACTION(shadow_state_unchanged)` with next wake.
- [ ] Runtime restart/replay must not duplicate a signal/event because the existing store is append-only/idempotent.
- [ ] Run focused source/action/engine tests and commit `feat(agent): connect swing shadow outcomes`.

## Task 4: Feed outcomes forward and render Day/Swing rows

**Files:**
- Modify: `trading_agent/research_agent_hermes.py`
- Modify: `tests/test_research_agent_hermes.py`
- Modify: `tests/test_research_agent_runtime.py`
- Modify: `tests/test_research_agent_service_runtime.py`

- [ ] Add RED Hermes tests showing Day recommendation/outcome and Swing thesis/open-state/outcome from resolved evidence/artifact refs.
- [ ] Render Day timestamp/entry/stop/targets/rationale/state and Swing signal/event state; show next wake and `order authority: false`.
- [ ] Add runtime regression: a Day terminal event persisted after one cycle appears in the next Day evidence payload, and a Swing terminal event wakes the Swing family as `OPEN_WORK`/feedback without duplicating the event.
- [ ] Keep exactly-once Hermes delivery identity and existing `RESEARCH` kind.
- [ ] Run regression tests and commit `feat(agent): render day and swing feedback`.

## Task 5: Slice 3 acceptance

- [ ] Run all Slice 1-3 focused tests, Ruff and basedpyright for changed files.
- [ ] Run CLI help (exit 0), missing config (exit 2), and service status read-only.
- [ ] On a current NY session, acceptance requires either an existing real recommendation artifact or typed Primary no-action; backdated recommendations must be zero.
- [ ] Outside the session (the current workspace state), inspect the real archived 20260730 Day database/outbox and show an existing recommendation/outcome or `no_setup`, with model calls 0 and broker mutation 0.
- [ ] Inspect the real Swing shadow/review store or its explicit archive/blocker and show a typed artifact/no-action/blocker.
- [ ] Confirm every recommendation carries timestamp, entry, stop, targets, rationale and immutable outcome history; confirm no Alpaca/KIS/LS mutation call occurred.
- [ ] Confirm clean worktree, atomic commits and `git diff --check` before opening Slice 4.
