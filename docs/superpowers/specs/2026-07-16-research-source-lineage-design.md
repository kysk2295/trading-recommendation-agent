# Research Source Lineage Design

- Status: approved implementation checkpoint
- Date: 2026-07-16
- Scope: global experiment ledger research evidence lineage
- Out of scope: provider calls, broker mutation, automatic lifecycle promotion, allocation

## Goal

Make every newly registered research hypothesis traceable to immutable, bounded evidence without rewriting the existing global experiment ledger or changing the authority of any execution lane.

The existing ledger already records hypothesis, strategy version, trial, trial events, and lifecycle. This checkpoint adds the missing upstream lineage:

```text
ResearchSource -> ResearchHypothesisCard -> existing StrategyVersion/Trial -> existing Reviewer
```

`ResearchSource` stores source metadata, a compact claim, and explicit limitations only. It never stores credentials, market payloads, copyrighted paper contents, recommendations, or any instruction to trade.

## Compatibility And Migration

The SQLite ledger changes from schema v1 to v2. A writer migrates an exact v1 database transactionally by adding append-only tables and then setting `PRAGMA user_version = 2`. Existing v1 tables and rows are never updated, deleted, re-keyed, or re-serialized. Readers accept v2; opening v1 for writing performs the local migration first.

Existing `HypothesisRegistration` remains unchanged for the legacy intraday import path. New research work uses `ResearchHypothesisCard`, which binds an existing hypothesis to stored research-source keys. This protects the current intraday hypothesis IDs and running trial contracts.

## Contracts

`ResearchSource` has a stable identifier, source kind, HTTPS URL, title, publication date, compact claim, compact limitation, retrieval time, and ledger recording time. URLs forbid embedded credentials and fragments. Its canonical key is SHA-256 over canonical ASCII JSON.

`ResearchHypothesisCard` contains a `HypothesisRegistration`, a sorted non-empty tuple of source keys, an economic mechanism, and a counterfactual baseline. The Writer admits it only if every source resolves exactly and was recorded no later than the hypothesis. It atomically registers the underlying hypothesis and card. Replays are no-ops; any same-ID payload change is a conflict.

The card is not a strategy version, trial, Reviewer decision, lifecycle transition, order, or allocation decision. Existing downstream contracts remain the only owners of those actions.

## Storage And CLI

Schema v2 adds append-only `research_sources` and `research_hypothesis_cards` tables. Reader connections stay query-only; the current single Writer lock and mode-600 database rules remain unchanged.

`run_research_hypothesis_register.py` accepts a JSON preregistration manifest, a ledger path, and a private output directory. It validates all input before opening the writer, registers sources and one card, then writes a mode-600 Korean report with only created/reused counts and `external mutation: 0`. It accepts no credentials and imports no provider, Paper, broker, or execution module.

The example manifest records provenance for the US swing new-high/RVOL idea. It is an IDEA-stage record only: no account binding, quote, order, shadow fill, performance claim, or trial is created. Connecting the lane to strategy version, forward trial, and Reviewer is the next separate vertical.

## Verification

Tests cover model validation, deterministic keys, v1-to-v2 migration without row rewrites, append-only behavior, missing-source rejection, conflict/replay, query-only reads, and CLI help/invalid/fixture paths. Full pytest, Ruff, basedpyright, and manual CLI QA must pass before merge.
