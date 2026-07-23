# CFTC TFF Positioning Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect the latest two official CFTC TFF futures-only observations for one contract market, preserve raw evidence first, and publish an immutable shadow-only positioning context.

**Architecture:** A fixed-origin HTTP adapter returns one bounded raw response. An append-only private SQLite store persists the response before a strict parser projects two weekly reports into a typed context. A content-addressed artifact and aggregate CLI report expose replayable evidence without raw positions or mutation authority.

**Tech Stack:** Python 3.12, Pydantic v2, httpx2, SQLite, Typer, pytest, Ruff, basedpyright.

---

### Task 1: Typed request, report, and context projection

**Files:**
- Create: `trading_agent/cftc_tff_models.py`
- Create: `trading_agent/cftc_tff_parser.py`
- Create: `tests/test_cftc_tff_parser.py`
- Fixture: `tests/fixtures/cftc_tff/es_latest_two.json`

- [ ] **Step 1: Write the failing parser test**

```python
def test_latest_two_reports_project_category_net_changes() -> None:
    request = CftcTffRequest(
        collection_id="es-tff-20260724",
        contract_market_code="13874A",
        through_date=dt.date(2026, 7, 24),
    )
    response = CftcTffRawResponse(
        request_id=request.request_id,
        received_at=RECEIVED,
        status_code=200,
        content_type="application/json",
        raw_payload=FIXTURE.read_bytes(),
    )

    context = parse_cftc_tff_context(request, response)

    assert context.latest_report_date == dt.date(2026, 7, 14)
    assert context.previous_report_date == dt.date(2026, 7, 7)
    assert len(context.categories) == 5
    assert context.categories[2].name is CftcTffCategory.LEVERAGED_MONEY
    assert context.observed_at == RECEIVED
```

- [ ] **Step 2: Run the test and verify RED**

Run:
`uv run pytest -q tests/test_cftc_tff_parser.py`

Expected: collection error because `trading_agent.cftc_tff_models` does not exist.

- [ ] **Step 3: Implement strict boundary models and parser**

Define frozen `CftcTffRequest`, `CftcTffRawResponse`, `CftcTffReport`,
`CftcTffCategoryPosition`, `CftcTffPositioningContext`, status/failure enums and
one sanitized `CftcTffError`. Parse exactly two descending `FutOnly` rows, require
matching market metadata and reconcile category long/short totals to open
interest. Derive net, weekly change and current net bps from integer positions.

- [ ] **Step 4: Add one failure per invariant and verify GREEN**

Add independent tests for wrong market code, future/duplicate report date,
combined report, negative position and broken open-interest reconciliation.

Run:
`uv run pytest -q tests/test_cftc_tff_parser.py`

Expected: all parser tests pass.

### Task 2: Raw-first append-only store and collection terminal

**Files:**
- Create: `trading_agent/cftc_tff_schema.py`
- Create: `trading_agent/cftc_tff_store.py`
- Create: `trading_agent/cftc_tff_collection.py`
- Create: `tests/test_cftc_tff_collection.py`

- [ ] **Step 1: Write the failing raw-before-parse test**

```python
def test_malformed_response_is_preserved_before_failed_terminal(tmp_path: Path) -> None:
    store = CftcTffStore(tmp_path / "cftc-tff.sqlite3")
    request = request_fixture()

    result = collect_cftc_tff(_Fetcher(b"{"), store, request, _clock=fixed_clock)

    assert result.run.status is CftcTffStatus.FAILED
    assert result.run.failure is CftcTffFailure.RESPONSE_STRUCTURE
    assert store.counts() == (1, 1)
    assert store.receipt(request.request_id).raw_payload == b"{"
```

- [ ] **Step 2: Run the test and verify RED**

Run:
`uv run pytest -q tests/test_cftc_tff_collection.py`

Expected: import failure because the store and collector do not exist.

- [ ] **Step 3: Implement schema, store, and collector**

Use one raw receipt row and one terminal run row per request ID. Add
`BEFORE UPDATE` and `BEFORE DELETE` abort triggers. Store canonical request/run
bytes and SHA-256, verify them on every read, use mode 600 SQLite under mode 700
parents, and open replay readers with `mode=ro` plus `PRAGMA query_only=ON`.
The collector must append the raw response before calling the parser and return
an existing exact terminal without invoking the fetcher.

- [ ] **Step 4: Verify failed and successful exact replay**

Run:
`uv run pytest -q tests/test_cftc_tff_collection.py`

Expected: malformed response is retained, successful first run creates one
receipt/run, and replay keeps counts unchanged with fetch count zero.

### Task 3: Fixed official CFTC HTTP adapter

**Files:**
- Create: `trading_agent/cftc_tff_client.py`
- Create: `tests/test_cftc_tff_client.py`

- [ ] **Step 1: Write the failing wire-contract test**

```python
def test_client_uses_fixed_tff_futures_only_query() -> None:
    with fixture_client(handler) as client:
        response = CftcTffClient(client, _clock=lambda: RECEIVED).fetch(request_fixture())

    assert captured.method == "GET"
    assert captured.url.path == "/resource/gpe5-46if.json"
    assert captured.url.params["$limit"] == "2"
    assert "futonly_or_combined='FutOnly'" in captured.url.params["$where"]
    assert response.raw_payload == FIXTURE.read_bytes()
```

- [ ] **Step 2: Run the test and verify RED**

Run:
`uv run pytest -q tests/test_cftc_tff_client.py`

Expected: import failure because the client does not exist.

- [ ] **Step 3: Implement fixed-origin bounded streaming GET**

Validate exact HTTPS origin, no redirects, fixed path, fixed selected fields,
descending report date and limit two. Stream at most 1 MiB with identity encoding.
Create the production client with HTTP/2, split timeout, retrying transport,
bounded pool and TCP_NODELAY. Convert only named httpx2/shape failures to
`CftcTffTransportError`.

- [ ] **Step 4: Verify transport boundaries**

Add tests for wrong origin, redirect, wrong final path, oversized
content-length and streamed overflow.

Run:
`uv run pytest -q tests/test_cftc_tff_client.py`

Expected: all client tests pass without external network.

### Task 4: Content-addressed artifact and CLI

**Files:**
- Create: `trading_agent/cftc_tff_artifact.py`
- Create: `run_cftc_tff_positioning_context.py`
- Create: `tests/test_cftc_tff_positioning_context_cli.py`

- [ ] **Step 1: Write the failing CLI E2E**

```python
def test_fixture_cli_publishes_context_and_replays_without_network(tmp_path: Path) -> None:
    first = run_cli(tmp_path, fixture=FIXTURE)
    replay = run_cli(tmp_path, fixture=Path("/nonexistent"))

    artifacts = tuple((tmp_path / "output").glob("cftc_tff_context_*.json"))
    assert first.returncode == 0
    assert replay.returncode == 0
    assert len(artifacts) == 1
    assert "artifact_created=no" in replay.stdout
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
```

- [ ] **Step 2: Run the test and verify RED**

Run:
`uv run pytest -q tests/test_cftc_tff_positioning_context_cli.py`

Expected: failure because the CLI script does not exist.

- [ ] **Step 3: Implement artifact and CLI**

Publish `cftc_tff_context_<context-id>.json` through the existing private
immutable writer and a stable aggregate report. CLI options are collection ID,
contract market code, through date, database, output directory and optional
fixture response. Existing terminal replay must bypass fixture reads and HTTP.
Exit 2 for a persisted failed terminal and Typer bad-parameter exit for invalid
boundary input.

- [ ] **Step 4: Verify help, bad input, happy path, and replay**

Run:
`uv run pytest -q tests/test_cftc_tff_positioning_context_cli.py`

Expected: help exposes all inputs, invalid code creates no database/output,
fixture first/replay create one artifact with `yes/no`, and reports contain no
raw positions or local paths.

### Task 5: Actual evidence, documentation, and final gates

**Files:**
- Create: `docs/checkpoints/2026-07-24-m6-cftc-tff-positioning-context-ko.md`
- Modify: `README.md`

- [ ] **Step 1: Run official bounded actual GET and exact replay**

Run the CLI for ES contract market code `13874A` and through date
`2026-07-24` into a private output under `outputs/derivatives/m6_live/`.
Immediately replay the same request and verify receipt/run/artifact counts and
file SHA are unchanged.

- [ ] **Step 2: Run all required verification**

Run focused CFTC tests, related futures/security-master tests, changed-file
Ruff format/check, basedpyright, compileall, Python no-excuse, CLI help/bad/happy
manual QA, then full `uv run pytest -q`, `uv run ruff check .`, and
`uv run basedpyright`.

- [ ] **Step 3: Record exact evidence and commit**

Document exact commit/runtime, report dates, context/artifact identity, raw
receipt count and SHA, replay counts, mode 600, GET-only and mutation zero.
State explicitly that CFTC data is weekly market-level aggregate and not a
current futures curve, strategy performance, recommendation, champion or order
authority. Commit implementation and checkpoint separately, then push
`HEAD:main` and verify local HEAD equals `origin/main`.
