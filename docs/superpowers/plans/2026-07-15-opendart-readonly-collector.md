# OpenDART Read-Only Catalyst Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch one KST day of official OpenDART disclosure-list pages with a guarded read-only client, preserve exact response receipts before parsing, and append receipt-linked DART catalysts plus an immutable source-run result to the KR ledger.

**Architecture:** Extend the existing KR append-only SQLite ledger from schema v1 to v2 with generic source receipts, observation-receipt links, and terminal source runs. Keep OpenDART transport, strict response parsing, disclosure projection, collection orchestration, and fixture CLI in separate modules so later news/KIS adapters can reuse only the ledger contracts.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, httpx2, Typer, pytest, Ruff, basedpyright

---

## File Map

- Create `trading_agent/kr_source_collection_models.py`: immutable receipt, observation lineage and source-run contracts.
- Modify `trading_agent/kr_theme_schema.py`: schema v2 additions and v1-to-v2 migration SQL.
- Modify `trading_agent/kr_theme_store.py`: append/read/validate source evidence and migrate existing v1 DBs.
- Create `tests/test_kr_source_collection_models.py`: contract and redaction tests.
- Modify `tests/test_kr_theme_store.py`: migration, lineage, checksum, trigger and source-run tests.
- Create `trading_agent/opendart_config.py`: exact mode-600 secret loader and production HTTP client factory.
- Create `trading_agent/opendart_client.py`: allowlisted GET transport and strict official response parser.
- Create `tests/test_opendart_config.py`: secret safety tests.
- Create `tests/test_opendart_client.py`: request, parsing, status and redaction tests.
- Create `trading_agent/opendart_collection.py`: raw-first pagination, disclosure projection and source-run orchestration.
- Modify `trading_agent/kr_theme_keyword.py`: explicit official DART text fields.
- Create `tests/test_opendart_collection.py`: success, no-data, pagination drift, partial failure and restart tests.
- Create `trading_agent/opendart_fixture.py`: path-contained deterministic page fixtures.
- Create `run_opendart_collect.py`: production/fixture CLI and redacted report.
- Create `tests/test_opendart_fixture.py` and `tests/test_opendart_collect_cli.py`: fixture and CLI E2E tests.
- Create `examples/opendart_collect/fixture-manifest.json` and two tiny synthetic response pages.
- Modify `AGENTS.md` and `README.md`; create the milestone checkpoint.

### Task 1: Source evidence models

**Files:**
- Create: `tests/test_kr_source_collection_models.py`
- Create: `trading_agent/kr_source_collection_models.py`

- [ ] **Step 1: Write failing model tests**

Cover deterministic receipt identity, hidden raw bytes, aware times, canonical request key, HTTP status range, exact SHA-256, nonnegative item index, source-run status/failure semantics and canonical receipt IDs.

```python
def test_source_receipt_identity_is_redacted_and_deterministic() -> None:
    receipt = KrSourceReceipt(
        source_run_id="kr-20260715-0900:dart",
        source=KrCatalystSource.DART,
        request_key="opendart:list:20260715:page:1",
        received_at=OBSERVED_AT,
        http_status=200,
        content_type="application/json",
        payload_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
    )
    assert receipt.receipt_id == KrSourceReceipt.model_validate(
        receipt.model_dump(mode="python")
    ).receipt_id
    assert "payload" not in repr(StoredKrSourceReceipt(receipt, PAYLOAD))
```

```python
def test_failed_source_run_requires_failure_code() -> None:
    with pytest.raises(ValidationError):
        _ = KrSourceCollectionRun(
            source_run_id="kr-20260715-0900:dart",
            collection_cycle_id="kr-20260715-0900",
            source=KrCatalystSource.DART,
            adapter_version="opendart-list-v1",
            started_at=OBSERVED_AT,
            completed_at=OBSERVED_AT,
            status=KrCoverageStatus.FAILED,
            record_count=0,
            failure_code=None,
            receipt_ids=(),
        )
```

- [ ] **Step 2: Run the model tests and verify RED**

Run: `uv run pytest tests/test_kr_source_collection_models.py -q`

Expected: collection fails because `trading_agent.kr_source_collection_models` does not exist.

- [ ] **Step 3: Implement minimal immutable models**

Implement:

```python
class KrSourceReceipt(BaseModel): ...
class KrCatalystObservationReceipt(BaseModel): ...
class KrSourceCollectionRun(BaseModel): ...

@dataclass(frozen=True, slots=True)
class StoredKrSourceReceipt:
    receipt: KrSourceReceipt
    raw_payload: bytes = field(repr=False)
```

Use `extra="forbid"`, frozen models, aware timestamps, safe IDs, exact lowercase SHA-256 and canonical tuples. Derive `receipt_id` from source run, source and request key without including payload or credentials.

- [ ] **Step 4: Verify focused quality**

```bash
uv run pytest tests/test_kr_source_collection_models.py -q
uv run ruff check trading_agent/kr_source_collection_models.py tests/test_kr_source_collection_models.py
uv run basedpyright trading_agent/kr_source_collection_models.py tests/test_kr_source_collection_models.py
```

Expected: all pass with zero type warnings.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/kr_source_collection_models.py tests/test_kr_source_collection_models.py
git commit -m "feat: add KR source evidence contracts"
```

### Task 2: KR ledger schema v2 and source evidence storage

**Files:**
- Modify: `trading_agent/kr_theme_schema.py`
- Modify: `trading_agent/kr_theme_store.py`
- Modify: `tests/test_kr_theme_store.py`

- [ ] **Step 1: Write failing schema/store tests**

Add tests proving:

```python
with store.writer() as writer:
    stored = writer.append_source_receipt(receipt, RAW_PAGE)
    result = writer.append_catalyst_from_receipt(
        record,
        observation,
        ITEM_PAYLOAD,
        receipt_id=stored.receipt.receipt_id,
        item_index=0,
    )
    assert writer.append_source_run(source_run) is True
```

Also assert:

- a manually created v1 DB migrates to `PRAGMA user_version = 2` without changing existing rows;
- same receipt bytes are idempotent while changed bytes conflict;
- a link to the wrong source, future receipt or wrong item SHA fails closed;
- source-run counts and receipt sets must match exact stored lineage;
- reader revalidates receipt BLOB checksum and source-run JSON;
- UPDATE/DELETE triggers reject all three new tables.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest tests/test_kr_theme_store.py -q`

Expected: failures for missing v2 schema and Writer APIs.

- [ ] **Step 3: Add schema v2 migration**

Keep `CREATE_KR_THEME_SCHEMA_V1` unchanged and add `CREATE_KR_THEME_SCHEMA_V2_ADDITIONS` for:

```sql
CREATE TABLE kr_source_receipts (...);
CREATE TABLE kr_catalyst_observation_receipts (...);
CREATE TABLE kr_source_collection_runs (...);
```

Add append-only triggers and set `KR_THEME_SCHEMA_VERSION = 2`. `_prepare_writer_connection` must create v2 from an empty DB or add only the v2 objects when `user_version=1`; every other noncurrent version remains unsupported.

- [ ] **Step 4: Add reader and Writer APIs**

Implement these public methods:

```python
KrThemeReader.source_receipts(source_run_id: str | None = None)
KrThemeReader.observation_receipts(collection_cycle_id: str | None = None)
KrThemeReader.source_runs(collection_cycle_id: str | None = None)
KrThemeWriter.append_source_receipt(receipt, raw_payload)
KrThemeWriter.append_catalyst_from_receipt(
    record, observation, raw_payload, *, receipt_id, item_index
)
KrThemeWriter.append_source_run(run)
```

Refactor catalyst append internally so catalyst, observation and receipt link commit atomically, while the existing local-manifest `append_catalyst` behavior remains unchanged.

- [ ] **Step 5: Verify focused quality and commit**

```bash
uv run pytest tests/test_kr_theme_models.py tests/test_kr_theme_store.py tests/test_kr_theme_ingest_manifest.py tests/test_kr_theme_ingest_cli.py -q
uv run ruff check trading_agent/kr_theme_schema.py trading_agent/kr_theme_store.py tests/test_kr_theme_store.py
uv run basedpyright trading_agent/kr_theme_schema.py trading_agent/kr_theme_store.py tests/test_kr_theme_store.py
git add trading_agent/kr_theme_schema.py trading_agent/kr_theme_store.py tests/test_kr_theme_store.py
git commit -m "feat: preserve KR source response lineage"
```

Expected: existing local ingest remains compatible and v1 migration tests pass.

### Task 3: Guarded OpenDART config and client

**Files:**
- Create: `tests/test_opendart_config.py`
- Create: `tests/test_opendart_client.py`
- Create: `trading_agent/opendart_config.py`
- Create: `trading_agent/opendart_client.py`

- [ ] **Step 1: Write failing config and client tests**

Test exact mode `600`, symlink rejection, one exact setting, 40-character secret validation and secret-free repr/errors. With `httpx2.MockTransport`, assert the only request is:

```python
assert request.method == "GET"
assert request.url.scheme == "https"
assert request.url.host == "opendart.fss.or.kr"
assert request.url.path == "/api/list.json"
assert request.url.params["bgn_de"] == "20260715"
assert request.url.params["end_de"] == "20260715"
assert request.url.params["sort"] == "date"
assert request.url.params["sort_mth"] == "asc"
assert request.url.params["page_count"] == "100"
```

Assert a wrong base URL or redirect-following client is rejected before the transport runs. Test status `000`, no-data `013`, malformed JSON, invalid disclosure fields, HTTP failure and API error without exposing key, URL, raw body or API message.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_opendart_config.py tests/test_opendart_client.py -q`

Expected: import failures for the new modules.

- [ ] **Step 3: Implement config and raw transport**

Implement:

```python
DEFAULT_OPENDART_SECRET_PATH = Path.home() / ".config/trading-agent/opendart.env"
OPENDART_BASE_URL = "https://opendart.fss.or.kr"

@dataclass(frozen=True, slots=True)
class OpenDartCredentials:
    api_key: str = field(repr=False)

def load_opendart_credentials(path: Path = DEFAULT_OPENDART_SECRET_PATH) -> OpenDartCredentials: ...
def create_opendart_http_client() -> httpx2.Client: ...
```

`OpenDartClient.fetch_page()` returns a raw-response object with bytes hidden from repr. It must not parse the API body; the collector needs to append the receipt first.

- [ ] **Step 4: Implement strict parser**

Add official response/disclosure models and:

```python
def parse_opendart_disclosure_page(raw_response: OpenDartRawResponse) -> OpenDartDisclosurePage: ...
```

Return a distinct no-data result for `013`. Map every other error to a stable nonsecret failure code.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_opendart_config.py tests/test_opendart_client.py -q
uv run ruff check trading_agent/opendart_config.py trading_agent/opendart_client.py tests/test_opendart_config.py tests/test_opendart_client.py
uv run basedpyright trading_agent/opendart_config.py trading_agent/opendart_client.py tests/test_opendart_config.py tests/test_opendart_client.py
git add trading_agent/opendart_config.py trading_agent/opendart_client.py tests/test_opendart_config.py tests/test_opendart_client.py
git commit -m "feat: add guarded OpenDART read client"
```

### Task 4: Raw-first OpenDART collection orchestration

**Files:**
- Create: `tests/test_opendart_collection.py`
- Create: `trading_agent/opendart_collection.py`
- Modify: `trading_agent/kr_theme_keyword.py`
- Modify: `tests/test_kr_theme_keyword.py`

- [ ] **Step 1: Write failing collector tests**

Use a deterministic page fetcher and real temporary `KrThemeStore` to prove:

- two pages produce two receipts, receipt-linked catalysts and one successful source run;
- raw receipt rows exist before the parser is invoked;
- `013` produces one receipt, zero catalysts and a successful run;
- API/schema/pagination/duplicate failure preserves receipts and partial observations in a failed run;
- a terminal run causes restart to return without calling the fetcher;
- source record IDs, publisher IDs and official canonical payloads are deterministic;
- `report_nm` and `corp_name` are eligible explicit keyword fields.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_opendart_collection.py tests/test_kr_theme_keyword.py -q`

Expected: missing collector APIs and DART text-field assertions fail.

- [ ] **Step 3: Implement projection and collector**

Define an `OpenDartPageFetcher` protocol and a collection function:

```python
def collect_opendart_disclosures(
    fetcher: OpenDartPageFetcher,
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    adapter_version: str = "opendart-list-v1",
) -> OpenDartCollectionResult: ...
```

For each page, append `KrSourceReceipt` before `parse_opendart_disclosure_page()`. Canonicalize each exact disclosure object, append the catalyst/observation/link, validate stable pagination and exact unique count, then append a terminal `KrSourceCollectionRun`.

- [ ] **Step 4: Add explicit official DART keyword fields**

Append `report_nm` and `corp_name` to `SUPPORTED_TEXT_FIELDS`. Keep top-level-only extraction, strict string validation and deterministic field order.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_opendart_collection.py tests/test_kr_theme_keyword.py tests/test_kr_theme_projection.py -q
uv run ruff check trading_agent/opendart_collection.py trading_agent/kr_theme_keyword.py tests/test_opendart_collection.py tests/test_kr_theme_keyword.py
uv run basedpyright trading_agent/opendart_collection.py trading_agent/kr_theme_keyword.py tests/test_opendart_collection.py tests/test_kr_theme_keyword.py
git add trading_agent/opendart_collection.py trading_agent/kr_theme_keyword.py tests/test_opendart_collection.py tests/test_kr_theme_keyword.py
git commit -m "feat: collect OpenDART catalysts raw first"
```

### Task 5: Fixture manifest and operator CLI

**Files:**
- Create: `tests/test_opendart_fixture.py`
- Create: `tests/test_opendart_collect_cli.py`
- Create: `trading_agent/opendart_fixture.py`
- Create: `run_opendart_collect.py`
- Create: `examples/opendart_collect/fixture-manifest.json`
- Create: `examples/opendart_collect/page-1.json`
- Create: `examples/opendart_collect/page-2.json`

- [ ] **Step 1: Write failing fixture/CLI tests**

Test path traversal, absolute path, symlink escape, duplicate/missing pages and invalid received times. CLI E2E must run a two-page fixture twice and assert:

```python
assert len(store.source_receipts()) == 2
assert len(store.catalysts()) == 2
assert len(store.observation_receipts()) == 2
assert len(store.source_runs()) == 1
assert stat.S_IMODE(database.stat().st_mode) == 0o600
```

Assert the report and captured terminal output contain none of the API-key setting name, company/report names, receipt numbers, payload hashes or fixture raw text.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_opendart_fixture.py tests/test_opendart_collect_cli.py -q`

Expected: fixture and CLI imports fail.

- [ ] **Step 3: Implement path-contained fixture loading**

Use a strict Pydantic manifest with canonical page numbers, aware `received_at`, status, content type and relative regular payload paths. Read all fixture bytes before opening the Writer.

- [ ] **Step 4: Implement CLI and aggregate report**

Support explicit production or `--fixture-manifest` mode, never both. Production loads the mode-600 key and official client; fixture mode loads no credentials and performs no network. Convert known safe errors to `typer.BadParameter` without exception chaining or raw provider text.

- [ ] **Step 5: Verify CLI manually and commit**

```bash
./run_opendart_collect.py --help
./run_opendart_collect.py --collection-cycle-id bad --collection-date invalid --fixture-manifest examples/opendart_collect/fixture-manifest.json
./run_opendart_collect.py --collection-cycle-id kr-dart-fixture-001 --collection-date 2026-07-15 --fixture-manifest examples/opendart_collect/fixture-manifest.json --database /tmp/opendart-fixture.sqlite3 --output-dir /tmp/opendart-fixture-report
```

Run the happy path twice, inspect only aggregate DB counts/mode and remove only those explicit temporary paths.

```bash
uv run pytest tests/test_opendart_fixture.py tests/test_opendart_collect_cli.py -q
uv run ruff check trading_agent/opendart_fixture.py run_opendart_collect.py tests/test_opendart_fixture.py tests/test_opendart_collect_cli.py
uv run basedpyright trading_agent/opendart_fixture.py run_opendart_collect.py tests/test_opendart_fixture.py tests/test_opendart_collect_cli.py
git add trading_agent/opendart_fixture.py run_opendart_collect.py tests/test_opendart_fixture.py tests/test_opendart_collect_cli.py examples/opendart_collect
git commit -m "feat: add OpenDART collection CLI"
```

### Task 6: Documentation, full verification and checkpoint

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Create: `docs/checkpoints/2026-07-15-opendart-readonly-collector-ko.md`
- Modify: `docs/superpowers/plans/2026-07-15-opendart-readonly-collector.md`

- [ ] **Step 1: Document exact current capability**

Add the OpenDART secret path rule, production/fixture CLI usage, schema v2 source evidence and limitations. State that no real API request was made, DART alone does not finalize a four-source cycle, and news/KIS/LLM/quote/risk/shadow/order paths remain absent.

- [ ] **Step 2: Run complete verification**

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
```

Expected: all tests pass, Ruff passes, basedpyright reports zero errors and warnings.

- [ ] **Step 3: Re-run manual CLI QA on the merged candidate**

Run help, invalid input, fixture happy path twice, inspect aggregate counts, report redaction and mode `600`. Confirm external network, LLM and broker mutation counts are all zero by construction of fixture mode.

- [ ] **Step 4: Record checkpoint and complete this plan**

Write exact test counts and commit hashes in the Korean checkpoint, mark every plan checkbox complete, and run `git diff --check` plus `git status --short`.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md README.md docs/checkpoints/2026-07-15-opendart-readonly-collector-ko.md docs/superpowers/plans/2026-07-15-opendart-readonly-collector.md
git commit -m "docs: record OpenDART collector milestone"
```

After review, merge the isolated branch into `main`, rerun the full gate on merged `main`, push `origin/main`, and verify local `main`, `origin/main` and the pushed commit are identical.
