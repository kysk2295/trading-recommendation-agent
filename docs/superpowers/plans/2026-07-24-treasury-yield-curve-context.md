# Treasury Yield Curve Context Implementation Plan

> **Execution:** Implement inline with `superpowers:executing-plans`; the user
> requested uninterrupted canonical M0-M10 completion.

**Goal:** Collect the official Treasury monthly daily par-yield XML feed
raw-first and publish the latest two causal curves as an immutable M6 macro
context.

**Architecture:** A fixed-origin GET client returns bounded raw bytes. An
append-only private SQLite store commits every response before a strict
namespace-aware parser creates a typed two-curve context. A collection
orchestrator preserves terminal failure/replay semantics, and a Typer CLI
publishes one content-addressed artifact plus a redacted aggregate report.

**Tech Stack:** Python 3.12, Pydantic v2, httpx2, sqlite3, ElementTree, Typer,
pytest, Ruff, basedpyright.

## Task 1: Models and strict parser

- Create `trading_agent/treasury_yield_models.py`.
- Create `trading_agent/treasury_yield_parser.py`.
- Add `tests/fixtures/treasury_yield_curve/2026-07.xml`.
- Add `tests/test_treasury_yield_parser.py`.
- RED: latest-two parse import failure.
- GREEN: frozen request/raw/curve/context/run models and strict XML projection.
- Add RED/GREEN boundaries for missing, duplicate, unknown, future-only and
  out-of-range properties.

## Task 2: Raw-first store and collection

- Create `trading_agent/treasury_yield_schema.py`.
- Create `trading_agent/treasury_yield_store.py`.
- Create `trading_agent/treasury_yield_collection.py`.
- Add store/collection tests.
- RED: malformed XML must remain as one receipt before failed terminal.
- GREEN: mode-600 append-only request/receipt/run rows, canonical hash
  revalidation and terminal replay with fetch count zero.

## Task 3: Fixed-origin client and immutable artifact

- Create `trading_agent/treasury_yield_client.py`.
- Create `trading_agent/treasury_yield_artifact.py`.
- Add client and publisher tests.
- Fix base URL, path, data key and request-derived month; reject redirect,
  wrong final URL, oversized and invalid content-length responses.
- Publish canonical
  `treasury_yield_curve_context_<context-id>.json` with exact replay.

## Task 4: CLI, actual GET and publication

- Create `run_treasury_yield_curve_context.py`.
- Add `tests/test_treasury_yield_curve_context_cli.py`.
- RED then GREEN fixture happy/replay and bad input.
- Run manual `--help`, bad date, fixture happy/replay.
- Run one official current-month actual GET and nonexistent-fixture replay.
- Record request/context/receipt/file SHA, dates, maturity count, modes and
  mutation zero in a checkpoint and README.

## Task 5: Verification and push

- Run focused Treasury and relevant M6 tests.
- Run changed-file Ruff, format, basedpyright, compileall, diff and no-excuse.
- Require every new production file to stay at or below 250 pure LOC.
- Run full pytest, Ruff and basedpyright.
- Commit implementation/tests and documentation separately.
- Push `HEAD:main`, require `HEAD == origin/main`, and leave the integration
  worktree clean.
