# KIS KR Ranking Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The external Grok worker must run directly with `--no-subagents`; Codex owns review and reconciliation.

**Goal:** Add a bounded, raw-first, KIS read-only collector that persists current-date KRX fluctuation and volume rankings as the KR ledger's terminal `kis_ranking` source run.

**Architecture:** A fixed-origin client exposes only two allow-listed GET contracts and returns raw response metadata without parsing. A collector commits every response BLOB before validating it, appends canonical item-level catalyst lineage, and closes exactly one immutable source run; fixtures implement the same fetch protocol without credentials or network. A thin CLI resolves an existing terminal run or orphan receipt from the local ledger before fixture, date, credential or network dependencies; only a genuinely new production run performs current-KST-date preflight before credentials. Reports contain only aggregate data and use mode 600.

**Tech Stack:** Python 3.12, `httpx2`, Pydantic v2, SQLite through `KrThemeStore`, Typer, pytest, Ruff, basedpyright.

---

## File Map

- Create `trading_agent/kis_kr_ranking.py`: allow-listed KIS request contract, raw response model, strict response parser and canonical item model.
- Create `trading_agent/kis_kr_ranking_fixture.py`: no-network manifest loader implementing the page fetch protocol.
- Create `trading_agent/kis_kr_ranking_collection.py`: raw-first collection, retry, pagination, restart and terminal-run state machine.
- Create `run_kis_kr_ranking_collect.py`: production/fixture CLI and redacted aggregate report.
- Create `tests/test_kis_kr_ranking.py`: endpoint, headers, schema and parser tests.
- Create `tests/test_kis_kr_ranking_fixture.py`: manifest and path safety tests.
- Create `tests/test_kis_kr_ranking_collection.py`: ledger ordering, retries, failures and replay tests.
- Create `tests/test_kis_kr_ranking_collect_cli.py`: CLI contract, reports, mode and redaction tests.
- Create `tests/fixtures/kis_kr_ranking/fixture-manifest.json`, `fluctuation-page-1.json`, `volume-page-1.json`: committed synthetic happy path.
- Modify `pyproject.toml`: include the new CLI in basedpyright.
- Modify `README.md`, `CODEX_START_HERE.md`: record capability and next milestone.
- Create `docs/checkpoints/2026-07-16-kis-kr-ranking-collector-ko.md`: verified checkpoint and production-smoke status.

### Task 1: Fixed KIS GET Contract And Parser

**Files:**
- Create: `trading_agent/kis_kr_ranking.py`
- Create: `tests/test_kis_kr_ranking.py`

- [ ] **Step 1: Write failing client allow-list tests**

Add tests proving that each enum kind sends exactly one GET to the fixed path with the fixed TR ID, `custtype=P`, expected `tr_cont`, and fixed query mapping. Use `httpx2.MockTransport`, dummy credentials and token, and assert no request body. Add constructor tests that reject a non-production origin and `follow_redirects=True` before a request occurs.

Use the exact query mappings recorded in `docs/superpowers/specs/2026-07-16-kis-kr-ranking-collector-design.md`; do not normalize key casing or replace empty strings with omitted parameters.

```python
def test_client_fetches_only_fixed_fluctuation_contract() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8", "tr_cont": ""},
            content=b'{"rt_cd":"0","msg_cd":"0","msg1":"ok","output":[]}',
        )

    client = httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )
    fetcher = KisKrRankingClient(client, _credentials(), "token", _clock=_clock)
    raw = fetcher.fetch_page(KisKrRankingKind.FLUCTUATION, page_no=1, attempt=1, tr_cont="")

    assert raw.content_type == "application/json"
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/uapi/domestic-stock/v1/ranking/fluctuation"
    assert seen[0].headers["tr_id"] == "FHPST01700000"
    assert seen[0].headers["tr_cont"] == ""
    assert seen[0].content == b""
```

- [ ] **Step 2: Run client tests and confirm RED**

Run: `uv run pytest -q tests/test_kis_kr_ranking.py -k client`

Expected: collection fails because `trading_agent.kis_kr_ranking` does not exist.

- [ ] **Step 3: Implement fixed client and raw response model**

Define the public contract below. Keep paths, TR IDs and params in a private immutable mapping keyed only by the enum. Normalize response content type by dropping parameters. Convert `httpx2.HTTPError` to `KisKrRankingTransportError` without including provider text or request headers.

```python
class KisKrRankingKind(StrEnum):
    FLUCTUATION = "fluctuation"
    VOLUME = "volume"


@dataclass(frozen=True, slots=True)
class KisKrRankingRawResponse:
    kind: KisKrRankingKind
    page_no: int
    attempt: int
    request_tr_cont: str
    response_tr_cont: str
    request_key: str
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)


class KisKrRankingClient:
    """Exact-origin read-only adapter for the two reviewed ranking contracts."""
```

Give `KisKrRankingClient.fetch_page` the exact signature
`(kind: KisKrRankingKind, *, page_no: int, attempt: int, tr_cont: str) -> KisKrRankingRawResponse`.
Its body selects the immutable contract by enum, copies `quote_headers`, adds only
the validated `tr_cont`, performs `client.get(contract.path, params=contract.params,
headers=headers)`, captures the injected clock after the response, and returns the
raw response object. It must not call `raise_for_status()` or decode JSON.

The request key format is `kis-kr:<kind>:p<page>:a<attempt>:rq-<empty|n>:rs-<empty|m|f>`. Accept request continuation only `""` or `"N"`; accept response continuation only `""`, `"M"` or `"F"`. Bound page and attempt before network.

- [ ] **Step 4: Write failing parser tests**

Cover valid fluctuation and volume payloads, zero-row success, non-200 status, wrong content type, invalid JSON, nonzero `rt_cd`, unknown/missing required fields, six-character uppercase alphanumeric symbol (`[0-9A-Z]{6}`), canonical name, integer rank/volumes, finite Decimal values, duplicate symbol and duplicate rank within a page. Confirm provider `msg1` never appears in raised error text. Include a regression case with letters so official KIS short codes are not silently dropped.

```python
def test_parse_volume_page_projects_reviewed_fields() -> None:
    page = parse_kis_kr_ranking_page(_raw(KisKrRankingKind.VOLUME, _volume_body()))
    assert page.items == (
        KisKrRankingItem(
            market="KRX",
            ranking_kind=KisKrRankingKind.VOLUME,
            symbol="005930",
            name="Synthetic Electronics",
            rank=1,
            price_krw=Decimal("81200"),
            change_pct=Decimal("3.25"),
            accumulated_volume=1_500_000,
            prior_day_volume=500_000,
            average_volume=600_000,
            volume_increase_pct=Decimal("200.00"),
            accumulated_trading_value_krw=Decimal("121800000000"),
        ),
    )
```

- [ ] **Step 5: Implement strict parser and canonical item JSON**

Use `json.loads(raw_payload)` followed by Pydantic models with `extra="forbid"`. The row models must declare every field in the pinned official check examples, while the canonical projection uses only the reviewed subset below. This allows the real official response and still fails closed on undocumented schema additions or omissions.

Declare these fluctuation row fields as strict strings:

```text
stck_shrn_iscd, data_rank, hts_kor_isnm, stck_prpr, prdy_vrss,
prdy_vrss_sign, prdy_ctrt, acml_vol, stck_hgpr, hgpr_hour,
acml_hgpr_date, stck_lwpr, lwpr_hour, acml_lwpr_date,
lwpr_vrss_prpr_rate, dsgt_date_clpr_vrss_prpr_rate, cnnt_ascn_dynu,
hgpr_vrss_prpr_rate, cnnt_down_dynu, oprc_vrss_prpr_sign,
oprc_vrss_prpr, oprc_vrss_prpr_rate, prd_rsfl, prd_rsfl_rate
```

Declare these volume row fields as strict strings:

```text
hts_kor_isnm, mksc_shrn_iscd, data_rank, stck_prpr,
prdy_vrss_sign, prdy_vrss, prdy_ctrt, acml_vol, prdy_vol,
lstn_stcn, avrg_vol, n_befr_clpr_vrss_prpr_rate, vol_inrt,
vol_tnrt, nday_vol_tnrt, avrg_tr_pbmn, tr_pbmn_tnrt,
nday_tr_pbmn_tnrt, acml_tr_pbmn
```

Map canonical fields exactly:

```text
fluctuation: stck_shrn_iscd, data_rank, hts_kor_isnm, stck_prpr,
             prdy_ctrt, acml_vol
volume:      mksc_shrn_iscd, data_rank, hts_kor_isnm, stck_prpr,
             prdy_ctrt, acml_vol, prdy_vol, avrg_vol, vol_inrt,
             acml_tr_pbmn
```

Expose `canonical_kis_kr_ranking_item(item) -> bytes` using sorted keys, compact separators and Pydantic JSON-mode serialization. Raise `KisKrRankingResponseError(failure_code)` with only these stable codes: `http_<status>`, `content_type`, `invalid_json`, `invalid_response`, `kis_api_error`, `duplicate_symbol`, `duplicate_rank`.

- [ ] **Step 6: Run parser tests and quality checks**

Run:

```bash
uv run pytest -q tests/test_kis_kr_ranking.py
uv run ruff check trading_agent/kis_kr_ranking.py tests/test_kis_kr_ranking.py
uv run basedpyright trading_agent/kis_kr_ranking.py
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit Task 1**

```bash
git add trading_agent/kis_kr_ranking.py tests/test_kis_kr_ranking.py
git commit -m "feat: define KIS KR ranking read contract"
```

### Task 2: Safe Synthetic Fixture Fetcher

**Files:**
- Create: `trading_agent/kis_kr_ranking_fixture.py`
- Create: `tests/test_kis_kr_ranking_fixture.py`
- Create: `tests/fixtures/kis_kr_ranking/fixture-manifest.json`
- Create: `tests/fixtures/kis_kr_ranking/fluctuation-page-1.json`
- Create: `tests/fixtures/kis_kr_ranking/volume-page-1.json`

- [ ] **Step 1: Write failing manifest safety tests**

Test exact schema v1 parsing, deterministic call order, response metadata, duplicate request identity, missing kind, page gaps, attempt outside 1..2, unsupported continuation, payload path traversal, absolute paths, symlink escape, non-regular file, empty payload and collection-date mismatch.

```python
def test_fixture_rejects_payload_path_escape(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, payload_path="../outside.json")
    with pytest.raises(KisKrRankingFixtureError):
        load_kis_kr_ranking_fixture(manifest, collection_date=dt.date(2026, 7, 16))
```

- [ ] **Step 2: Run fixture tests and confirm RED**

Run: `uv run pytest -q tests/test_kis_kr_ranking_fixture.py`

Expected: import failure for the missing fixture module.

- [ ] **Step 3: Implement fixture protocol**

Use frozen Pydantic manifest models. Resolve each payload under the manifest directory, reject symlinks and path escape before reading, and keep payload bytes out of `repr`. The returned fetcher must consume the exact expected `(kind, page_no, attempt, tr_cont)` sequence and return `KisKrRankingRawResponse`; unexpected or exhausted calls raise the fixed Korean `KisKrRankingFixtureError`.

- [ ] **Step 4: Add committed two-response happy fixture**

Use synthetic company names and symbols only. Include one page for `fluctuation` and one for `volume`, both with `received_at` on `2026-07-16` KST and terminal empty continuation. Do not include real account, token, header or provider output copied from a user session.

- [ ] **Step 5: Verify and commit Task 2**

Run:

```bash
uv run pytest -q tests/test_kis_kr_ranking_fixture.py tests/test_kis_kr_ranking.py
uv run ruff check trading_agent/kis_kr_ranking_fixture.py tests/test_kis_kr_ranking_fixture.py
uv run basedpyright trading_agent/kis_kr_ranking_fixture.py
```

Expected: all commands exit 0.

```bash
git add trading_agent/kis_kr_ranking_fixture.py tests/test_kis_kr_ranking_fixture.py tests/fixtures/kis_kr_ranking
git commit -m "test: add safe KIS KR ranking fixtures"
```

### Task 3: Raw-First Collection State Machine

**Files:**
- Create: `trading_agent/kis_kr_ranking_collection.py`
- Create: `tests/test_kis_kr_ranking_collection.py`

- [ ] **Step 1: Write failing happy-path and ordering tests**

Use an instrumented parser and `KrThemeStore`. Assert the receipt is queryable before the parser callback runs, both kinds are collected in enum order, canonical items have receipt item lineage, `collection_date` is stored, exact counts are used, database/lock mode is 600, and a terminal success replay invokes the fetcher zero times.

```python
def test_collection_commits_receipt_before_parser(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr.sqlite3")

    def parser(raw: KisKrRankingRawResponse) -> KisKrRankingPage:
        assert len(store.source_receipts("cycle-001:kis_ranking")) == 1
        return parse_kis_kr_ranking_page(raw)

    result = collect_kis_kr_rankings(
        _fetcher(_two_success_responses()),
        store,
        collection_cycle_id="cycle-001",
        collection_date=dt.date(2026, 7, 16),
        _parser=parser,
        _sleeper=lambda _: None,
    )
    assert result.run.status is KrCoverageStatus.SUCCESS
```

- [ ] **Step 2: Write failing retry, pagination and failure tests**

Cover:

- 500/502/503/504 followed by success: two raw receipts and one 80ms sleep.
- repeated transient response: failed run, no third call.
- 429: failed run, no sleep/retry.
- `tr_cont=M`: next page uses request `N`; terminal empty or `F` completes.
- more than 10 pages: `page_limit_exceeded`.
- response KST date mismatch: receipt preserved, no catalyst for that response.
- malformed/duplicate page: earlier observations preserved and terminal run failed.
- transport error before any receipt: zero-receipt failed run with aware clock timestamps.
- orphan receipt on restart: `incomplete_restart` failed run, network zero.
- incompatible existing terminal adapter version or multiple source runs: fail closed.

- [ ] **Step 3: Run collection tests and confirm RED**

Run: `uv run pytest -q tests/test_kis_kr_ranking_collection.py`

Expected: import failure for the missing collection module.

- [ ] **Step 4: Implement collection result and source-run helpers**

```python
KIS_KR_RANKING_ADAPTER_VERSION: Final = "kis-kr-ranking-v1"
TRANSIENT_STATUS_CODES: Final = frozenset({500, 502, 503, 504})
REQUEST_DELAY_SECONDS: Final = 0.08
MAX_PAGES_PER_KIND: Final = 10


@dataclass(frozen=True, slots=True)
class KisKrRankingCollectionResult:
    run: KrSourceCollectionRun
    receipt_count: int
    new_receipt_count: int
    catalyst_count: int
    new_catalyst_count: int
    new_observation_count: int
    restarted: bool
```

At entry, initialize/migrate the store under the writer lease. Query exact source runs first. If none exist but source-run receipts exist, append `incomplete_restart` from existing evidence and return without calling the fetcher. Otherwise process each kind/page/attempt. Append `KrSourceReceipt` in its own writer context before status/parser logic. Use `source_record_id=f"kis-ranking://{collection_cycle_id}/{kind.value}/{item.symbol}"`, `publisher_id="kis_domestic_market_data"`, `published_at=None`, and receipt time for first observation.

Finalize in a `finally`-safe path so expected transport/response failures become an immutable failed run. Do not catch `KeyboardInterrupt`, `SystemExit`, writer conflicts or programmer errors as provider failures. Build source-run `started_at`/`completed_at` from receipt times when receipts exist, otherwise one injected aware clock value.

- [ ] **Step 5: Run focused verification and commit Task 3**

Run:

```bash
uv run pytest -q tests/test_kis_kr_ranking.py tests/test_kis_kr_ranking_fixture.py tests/test_kis_kr_ranking_collection.py
uv run ruff check trading_agent/kis_kr_ranking.py trading_agent/kis_kr_ranking_fixture.py trading_agent/kis_kr_ranking_collection.py tests/test_kis_kr_ranking.py tests/test_kis_kr_ranking_fixture.py tests/test_kis_kr_ranking_collection.py
uv run basedpyright trading_agent/kis_kr_ranking.py trading_agent/kis_kr_ranking_fixture.py trading_agent/kis_kr_ranking_collection.py
```

Expected: all commands exit 0.

```bash
git add trading_agent/kis_kr_ranking_collection.py tests/test_kis_kr_ranking_collection.py
git commit -m "feat: collect raw-first KIS KR rankings"
```

### Task 4: Bounded CLI And Redacted Report

**Files:**
- Create: `run_kis_kr_ranking_collect.py`
- Create: `tests/test_kis_kr_ranking_collect_cli.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing CLI contract tests**

Test direct `main()` calls and subprocess help. Required cases:

- committed fixture happy path and exact replay.
- invalid cycle ID/date before DB creation.
- historical date accepted only with fixture.
- production historical date rejected before credential loader, token call, DB or report.
- existing terminal production replay returns before current-date, fixture, credential, token and HTTP dependencies.
- orphan receipt restart closes `incomplete_restart` before current-date, fixture, credential, token and HTTP dependencies.
- fixture mode never calls credential/token/client functions.
- failed source run writes report then raises nonzero `typer.BadParameter`.
- report and terminal omit synthetic company, payload SHA-256, raw provider message, token/header markers, DB/output paths and receipt IDs.
- `--help` lists only five approved options and no URL, token, account, order, mode, secret or force option.

- [ ] **Step 2: Run CLI tests and confirm RED**

Run: `uv run pytest -q tests/test_kis_kr_ranking_collect_cli.py`

Expected: import failure for `run_kis_kr_ranking_collect`.

- [ ] **Step 3: Implement preflight, production wiring and report**

New production path order must be:

```text
parse safe cycle/date
-> create KrThemeStore value (no file yet)
-> resolve exact terminal run or orphan receipt locally
-> if resolved, report without fixture/date/credential/network dependencies
-> otherwise compare collection_date with Asia/Seoul current date
-> load fixed KIS live credentials
-> create fixed live client
-> get/reuse access token
-> construct KisKrRankingClient
-> collect
```

Fixture mode loads only the fixture and store. Catch known contract/config/store errors and replace unexpected `ValueError` causes with one fixed Korean message using `from None`. Write `kis_kr_ranking_collection_summary_ko.md` with aggregate counts only and chmod 600 before converting a failed run to nonzero.

- [ ] **Step 4: Add CLI to basedpyright and verify**

Add `run_kis_kr_ranking_collect.py` to `[tool.basedpyright].include`.

Run:

```bash
uv run pytest -q tests/test_kis_kr_ranking_collect_cli.py tests/test_kis_kr_ranking_collection.py
uv run ruff check run_kis_kr_ranking_collect.py tests/test_kis_kr_ranking_collect_cli.py pyproject.toml
uv run basedpyright run_kis_kr_ranking_collect.py
uv run python run_kis_kr_ranking_collect.py --help
```

Expected: tests/checks exit 0 and help contains only the approved collector options.

- [ ] **Step 5: Run manual fixture QA**

Use a temporary directory and the committed fixture. Confirm help exits 0, invalid `../escape` exits 2 before DB creation, happy path exits 0, exact replay exits 0, and a copied fixture with malformed JSON exits 2 after preserving one failed terminal source run. Confirm mode-600 DB/report and no private markers in stdout/stderr/report.

- [ ] **Step 6: Commit Task 4**

```bash
git add run_kis_kr_ranking_collect.py tests/test_kis_kr_ranking_collect_cli.py pyproject.toml
git commit -m "feat: add KIS KR ranking collector CLI"
```

### Task 5: Documentation And Checkpoint

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Create: `docs/checkpoints/2026-07-16-kis-kr-ranking-collector-ko.md`

- [ ] **Step 1: Update README capability and command**

Describe exact two GETs, current-date/raw-first behavior, terminal source-run replay, and that the output is discovery evidence without quote validation, TradeSignal or orders. Add fixture and production command examples without credentials. Change the KR status sentence so only canonical `volume_surge`, scheduler, quote/risk and shadow signal remain unimplemented.

- [ ] **Step 2: Update next-start priorities**

Record `canonical volume_surge source adapter` as the next KR milestone. Preserve Alpaca Paper regular-session smoke as a separate market-time-gated priority and do not claim it completed.

- [ ] **Step 3: Write checkpoint from actual evidence**

Include scope, endpoint/TR allow-list, raw-first/restart contracts, test counts, Ruff/type/manual QA, commit IDs and production smoke status. If no production GET was run, state exactly `실제 KIS 국내 랭킹 GET 0건`. If run, report only aggregate request/receipt/row counts and never payload, symbol, account or credentials.

- [ ] **Step 4: Commit Task 5**

```bash
git add README.md CODEX_START_HERE.md docs/checkpoints/2026-07-16-kis-kr-ranking-collector-ko.md
git commit -m "docs: record KIS KR ranking checkpoint"
```

### Task 6: Independent Reconciliation And Full Verification

**Files:**
- Review all files changed since `4558a5e`.

- [ ] **Step 1: Inspect worker history and diff**

Run:

```bash
git status --short --branch
git log --oneline --decorate 4558a5e..HEAD
git diff --stat 4558a5e..HEAD
git diff --check 4558a5e..HEAD
```

Expected: only planned files, small task commits, no whitespace errors, no secret/config/output files.

- [ ] **Step 2: Search safety boundaries**

Run targeted searches for `/stock/order`, `/stock/accno`, account/balance/order methods, non-official KIS origins, `follow_redirects=True`, POST/PUT/PATCH/DELETE in new ranking modules, credential/token logging, `force` and user-supplied URL/TR ID. Any match must be explained by a test assertion or removed.

- [ ] **Step 3: Run full verification**

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
uv run python run_kis_kr_ranking_collect.py --help
```

Expected: all commands exit 0. Record the actual pytest count and durations in the checkpoint.

- [ ] **Step 4: Codex code review**

Review before integration for behavioral regressions, raw-first ordering, restart ambiguity, partial evidence accounting, pagination/retry bounds, report redaction, secret handling and missing tests. Fix findings in the worker branch and rerun focused/full verification.

- [ ] **Step 5: Optional bounded production read-only smoke**

Only when the fixed credential file is current-user-owned mode 600, the collection date is current KST, the exact live origin guard passes, and no pasted/compromised credential is involved, execute one new-cycle production command. Do not use any KIS account, balance, position or order endpoint. If any condition is uncertain, leave production GET at zero and retain fixture E2E evidence.

- [ ] **Step 6: Integrate and push**

Fast-forward or cherry-pick reviewed worker commits onto `main`, rerun `git status --short --branch` and the required verification, then push `main` to `origin`. Confirm local `HEAD` equals `origin/main` and remove the completed worker worktree/branch only after integration.
