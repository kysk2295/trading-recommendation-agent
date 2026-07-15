# US Fresh Quote Actionability Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-query a causal KIS US level-one quote for each newly publishable day signal and emit an immutable current-quote-validated signal only when strict market-time and feasibility gates pass.

**Architecture:** Add a sanitized read-only KIS quote adapter, a pure quote evidence/actionability kernel, append-only evidence writers, and a narrow orchestration hook inside the existing scan client lifetime. Preserve the original conditional signal and create a bounded SHA-256-derived signal identity for each independently observed quote.

**Tech Stack:** Python 3.12, httpx2, Pydantic v2, Typer, append-only JSONL, pytest, Ruff, basedpyright

---

### Task 1: KIS US Level-One Quote Adapter

**Files:**
- Create: `trading_agent/kis_us_quote.py`
- Create: `tests/test_kis_us_quote.py`

- [ ] **Step 1: Write failing exact-request and parse tests**

Use `httpx2.MockTransport` to assert the official contract and a stable normalized result:

```python
def test_fetch_kis_us_quote_uses_exact_read_only_contract() -> None:
    received_at = dt.datetime(2026, 7, 15, 13, 20, 1, tzinfo=NEW_YORK)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "MCA00000",
                "msg1": "provider success text",
                "output1": {"dymd": "20260715", "dhms": "132000"},
                "output2": {
                    "pbid1": "10.08",
                    "pask1": "10.10",
                    "vbid1": "1200",
                    "vask1": "900",
                },
                "output3": {},
            },
        )

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as client:
        quote = fetch_kis_us_level_one_quote(
            client,
            KisSession(KisCredentials("key", "secret"), "token"),
            exchange="NAS",
            symbol="ABCD",
            clock=lambda: received_at,
        )

    assert requests[0].url.path == "/uapi/overseas-price/v1/quotations/inquire-asking-price"
    assert dict(requests[0].url.params) == {"AUTH": "", "EXCD": "NAS", "SYMB": "ABCD"}
    assert requests[0].headers["tr_id"] == "HHDFS76200100"
    assert quote.provider_observed_at == received_at - dt.timedelta(seconds=1)
    assert quote.bid == Decimal("10.08")
    assert quote.ask == Decimal("10.10")
```

Add parameterized tests for missing output blocks, malformed timestamps, zero/crossed/non-finite prices, negative/fractional sizes, non-zero provider status, HTTP errors, and a response exception whose request contains credential headers. Assert exceptions contain only a stable code and have no chained credential-bearing cause.

- [ ] **Step 2: Run adapter tests and verify RED**

```bash
uv run pytest tests/test_kis_us_quote.py -q
```

Expected: import failure because `trading_agent.kis_us_quote` does not exist.

- [ ] **Step 3: Implement the adapter and normalized model**

Create constants and a frozen result:

```python
KIS_US_LEVEL_ONE_PATH: Final = "/uapi/overseas-price/v1/quotations/inquire-asking-price"
KIS_US_LEVEL_ONE_TR_ID: Final = "HHDFS76200100"
NEW_YORK: Final = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class KisUsLevelOneQuote:
    exchange: str
    symbol: str
    provider_observed_at: dt.datetime
    received_at: dt.datetime
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int


class KisUsQuoteUnavailableError(RuntimeError):
    __slots__ = ("failure_code",)

    def __init__(self, failure_code: str) -> None:
        super().__init__()
        self.failure_code = failure_code

    def __str__(self) -> str:
        return f"KIS 미국주식 현재 호가를 검증할 수 없습니다: {self.failure_code}"
```

Use strict Pydantic response blocks for `dymd`, `dhms`, `pbid1`, `pask1`, `vbid1`, and `vask1`; ignore unrelated documented provider fields but require exactly one object for each required block. Parse provider time with `America/New_York`. Catch HTTP, provider-status, JSON and validation failures and raise `KisUsQuoteUnavailableError` from `None` so request headers and provider bodies are not retained.

- [ ] **Step 4: Run adapter tests and verify GREEN**

```bash
uv run pytest tests/test_kis_us_quote.py -q
```

Expected: all adapter tests pass.

- [ ] **Step 5: Commit the adapter**

```bash
git add trading_agent/kis_us_quote.py tests/test_kis_us_quote.py
git commit -m "feat: fetch KIS US level-one quotes"
```

### Task 2: Quote Evidence And Actionability Kernel

**Files:**
- Create: `trading_agent/us_quote_actionability.py`
- Create: `tests/test_us_quote_actionability.py`

- [ ] **Step 1: Write failing immutable-contract tests**

Cover snapshot identity, terminal assessment geometry and derived signal lineage:

```python
def test_fresh_quote_below_trigger_creates_waiting_signal() -> None:
    base = _conditional_publication(entry="10.10", stop="9.90")
    quote = _quote(provider_at=AT - dt.timedelta(seconds=1), bid="10.07", ask="10.08")
    decision = assess_us_quote(
        base,
        quote,
        scan_started_at=AT - dt.timedelta(seconds=20),
        evaluated_at=AT,
    )
    assert decision.assessment.status is QuoteAssessmentStatus.VALIDATED_WAITING
    assert decision.derived_publication is not None
    assert decision.derived_publication.signal.actionability is SignalActionability.CURRENT_QUOTE_VALIDATED
    assert decision.derived_publication.signal.signal_id.startswith("us-quote-signal:")


def test_quote_exactly_five_seconds_old_is_stale() -> None:
    decision = assess_us_quote(
        _conditional_publication(),
        _quote(provider_at=AT - dt.timedelta(seconds=5)),
        scan_started_at=AT - dt.timedelta(seconds=20),
        evaluated_at=AT,
    )
    assert decision.assessment.status is QuoteAssessmentStatus.STALE_QUOTE
    assert decision.derived_publication is None
```

Add cases for 4.999-second freshness, future quote, market closed, date mismatch, 25 bp spread pass and above-bound fail, bid at stop, ask below/at trigger, ask exactly 20 bp above entry and above-bound fail, base expiry, and deterministic replay.

- [ ] **Step 2: Run kernel tests and verify RED**

```bash
uv run pytest tests/test_us_quote_actionability.py -q
```

Expected: import failure because the actionability module does not exist.

- [ ] **Step 3: Implement evidence and assessment models**

Add strict Pydantic models and bounded identities:

```python
class QuoteAssessmentStatus(StrEnum):
    VALIDATED_WAITING = "validated_waiting"
    VALIDATED_TRIGGER_REACHED = "validated_trigger_reached"
    MARKET_CLOSED = "market_closed"
    PROVIDER_FAILED = "provider_failed"
    INVALID_QUOTE = "invalid_quote"
    FUTURE_QUOTE = "future_quote"
    STALE_QUOTE = "stale_quote"
    SPREAD_TOO_WIDE = "spread_too_wide"
    SETUP_INVALIDATED = "setup_invalidated"
    ENTRY_SLIPPAGE_EXCEEDED = "entry_slippage_exceeded"


class UsQuoteSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    quote_id: str
    provider: Literal["kis"] = "kis"
    market_id: Literal[MarketId.US_EQUITIES] = MarketId.US_EQUITIES
    exchange: str
    symbol: str
    provider_observed_at: dt.datetime
    received_at: dt.datetime
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int
    spread_bps: Decimal


class QuoteActionabilityAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    assessment_id: str
    base_signal_id: str
    scan_started_at: dt.datetime
    evaluated_at: dt.datetime
    status: QuoteAssessmentStatus
    quote_id: str | None = None
    derived_signal_id: str | None = None
```

Canonicalize identity inputs with sorted compact JSON and SHA-256. Use `us-quote:<hex>`, `us-quote-assessment:<hex>`, and `us-quote-signal:<hex>` prefixes so identifiers remain bounded even when a recommendation ID is long.

- [ ] **Step 4: Implement the pure gate and derived projection**

Use fixed v1 constants:

```python
QUOTE_FRESHNESS = dt.timedelta(seconds=5)
MAX_QUOTE_SPREAD_BPS = Decimal("25")
MAX_ENTRY_SLIPPAGE_BPS = Decimal("20")
BASIS_POINTS = Decimal("10000")
```

Evaluate gates in this order: base validity and current regular session, provider future/date/session, strict freshness, spread, stop invalidation, entry slippage. On success, create `QuoteValidation` with expiry `provider_observed_at + QUOTE_FRESHNESS`, add sorted `signal/conditional` and `quote/snapshot` evidence to the base evidence, and emit a `TradeSignalPublication` observed and published at `evaluated_at`.

For adapter failures, provide a separate constructor:

```python
def provider_failed_assessment(
    base: TradeSignalPublication,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> QuoteActionabilityAssessment:
    return _assessment(
        base,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
        status=QuoteAssessmentStatus.PROVIDER_FAILED,
    )
```

- [ ] **Step 5: Run kernel tests and verify GREEN**

```bash
uv run pytest tests/test_us_quote_actionability.py -q
```

Expected: all evidence, gate and projection tests pass.

- [ ] **Step 6: Commit the kernel**

```bash
git add trading_agent/us_quote_actionability.py tests/test_us_quote_actionability.py
git commit -m "feat: assess US quote actionability"
```

### Task 3: Append-Only Quote Evidence And Cards

**Files:**
- Modify: `trading_agent/contract_outbox.py`
- Modify: `tests/test_contract_outbox.py`

- [ ] **Step 1: Write failing outbox and card tests**

Add exact replay, conflict and card assertions:

```python
assert append_us_quote_snapshot(path, quote) is True
assert append_us_quote_snapshot(path, quote) is False
with pytest.raises(ContractOutboxConflictError):
    append_us_quote_snapshot(path, quote.model_copy(update={"ask": Decimal("10.11")}))

assert append_quote_actionability_assessment(assessment_path, assessment) is True
card = next(cards_dir.iterdir()).read_text(encoding="utf-8")
assert "현재 bid/ask: 10.08 / 10.10" in card
assert "트리거 상태: 도달" in card
assert "자동주문" in card
```

Keep the existing conditional card byte-for-byte unchanged.

- [ ] **Step 2: Run outbox tests and verify RED**

```bash
uv run pytest tests/test_contract_outbox.py -q
```

Expected: quote append functions are missing and validated cards omit quote detail.

- [ ] **Step 3: Add typed append-only writers**

Reuse `_append_model()`:

```python
def append_us_quote_snapshot(path: Path, snapshot: UsQuoteSnapshot) -> bool:
    return _append_model(
        path,
        snapshot,
        model_type=UsQuoteSnapshot,
        identity=lambda item: item.quote_id,
    )


def append_quote_actionability_assessment(
    path: Path,
    assessment: QuoteActionabilityAssessment,
) -> bool:
    return _append_model(
        path,
        assessment,
        model_type=QuoteActionabilityAssessment,
        identity=lambda item: item.assessment_id,
    )
```

- [ ] **Step 4: Render quote-validated card detail**

When `quote_validation` exists, append current quote lines before entry:

```python
quote_lines = (
    f"- 호가 관측 시각: {quote.observed_at.isoformat()}",
    f"- 현재 bid/ask: {_decimal_text(quote.bid)} / {_decimal_text(quote.ask)}",
    f"- spread: {_decimal_text(quote.spread_bps)} bp",
    f"- 트리거 상태: {'도달' if quote.ask >= signal.entry_price else '대기'}",
)
```

Do not add these lines to conditional cards and do not call an external sender.

- [ ] **Step 5: Run outbox tests and verify GREEN**

```bash
uv run pytest tests/test_contract_outbox.py -q
```

Expected: all existing and new outbox tests pass.

- [ ] **Step 6: Commit evidence writers**

```bash
git add trading_agent/contract_outbox.py tests/test_contract_outbox.py
git commit -m "feat: persist US quote actionability evidence"
```

### Task 4: Scan Orchestration

**Files:**
- Create: `trading_agent/us_quote_publication.py`
- Create: `tests/test_us_quote_publication.py`
- Modify: `run_kis_paper_scan.py`
- Modify: `tests/test_run_kis_paper_scan_contracts.py`

- [ ] **Step 1: Write failing orchestration tests**

Use an injected fetcher and clock:

```python
def test_quote_batch_fetches_each_signal_symbol_once() -> None:
    calls: list[tuple[str, str]] = []
    batch = evaluate_quote_publications(
        (_publication("ABCD"), _publication("ABCD"), _publication("EFGH")),
        exchange_by_symbol={"ABCD": "NAS", "EFGH": "NYS"},
        fetch_quote=lambda exchange, symbol: calls.append((exchange, symbol)) or _quote(symbol),
        scan_started_at=STARTED_AT,
        clock=lambda: EVALUATED_AT,
    )
    assert calls == [("NAS", "ABCD"), ("NYS", "EFGH")]
    assert len(batch.assessments) == 3


def test_closed_market_makes_zero_provider_calls() -> None:
    calls = 0
    batch = evaluate_quote_publications(
        (_publication("ABCD"),),
        exchange_by_symbol={"ABCD": "NAS"},
        fetch_quote=lambda *_: pytest.fail("provider called"),
        scan_started_at=WEEKEND,
        clock=lambda: WEEKEND,
    )
    assert batch.assessments[0].status is QuoteAssessmentStatus.MARKET_CLOSED
```

Add provider failure isolation, missing exchange fail-closed, expired signal no-call, stable ordering, and no quote fetch when no conditional publication exists.

- [ ] **Step 2: Run orchestration tests and verify RED**

```bash
uv run pytest tests/test_us_quote_publication.py tests/test_run_kis_paper_scan_contracts.py -q
```

Expected: publication batch module and scan hook are absent.

- [ ] **Step 3: Implement the injected batch evaluator**

Define:

```python
@dataclass(frozen=True, slots=True)
class UsQuotePublicationBatch:
    snapshots: tuple[UsQuoteSnapshot, ...]
    assessments: tuple[QuoteActionabilityAssessment, ...]
    derived_publications: tuple[TradeSignalPublication, ...]
```

Sort base publications by `(signal.symbol, signal.signal_id)`, cache one provider result per symbol, and create one assessment per base publication. Catch only `KisUsQuoteUnavailableError` and convert it to `provider_failed`; unexpected programming errors must propagate.

- [ ] **Step 4: Refactor conditional projection without changing behavior**

In `run_kis_paper_scan.py`, split construction from append:

```python
def build_trade_signal_contracts(...) -> tuple[TradeSignalPublication, ...]:
    if opportunity is None:
        return ()
    return project_trade_signal_publications(...)


def append_trade_signal_contracts(
    output: Path,
    publications: tuple[TradeSignalPublication, ...],
) -> int:
    return sum(
        append_trade_signal_publication(
            output / "trade-signals.v1.jsonl",
            output / "trade-signal-cards-ko",
            publication,
        )
        for publication in publications
    )
```

Keep `publish_trade_signal_contracts()` as a compatibility wrapper for existing tests.

- [ ] **Step 5: Connect quote requests inside the KIS client lifetime**

After all candidate observations are complete but before leaving `with create_kis_client(...)`, build conditional publications, map selected symbols to exchanges, and call `evaluate_quote_publications()` with `fetch_kis_us_level_one_quote`. Outside the client context append in order:

```text
conditional signals
quote snapshots
derived validated signals/cards
terminal assessments
```

Use these exact output names:

```text
us-quote-snapshots.v1.jsonl
quote-actionability-assessments.v1.jsonl
trade-signals.v1.jsonl
trade-signal-cards-ko/
```

Add aggregate terminal counts only: quote attempts, waiting validations, trigger-reached validations and blocked assessments. Do not print symbols, prices, quote IDs or provider messages.

- [ ] **Step 6: Run orchestration tests and verify GREEN**

```bash
uv run pytest tests/test_us_quote_publication.py tests/test_run_kis_paper_scan_contracts.py -q
```

Expected: all batch and scanner contract tests pass with existing conditional behavior unchanged.

- [ ] **Step 7: Commit scan integration**

```bash
git add trading_agent/us_quote_publication.py tests/test_us_quote_publication.py run_kis_paper_scan.py tests/test_run_kis_paper_scan_contracts.py
git commit -m "feat: publish fresh US quote signals"
```

### Task 5: Verification, Read-Only Smoke And Checkpoint

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture_ko.md`
- Create: `docs/checkpoints/2026-07-16-us-fresh-quote-signal-ko.md`

- [ ] **Step 1: Run focused verification**

```bash
uv run pytest tests/test_kis_us_quote.py tests/test_us_quote_actionability.py tests/test_us_quote_publication.py tests/test_contract_outbox.py tests/test_run_kis_paper_scan_contracts.py -q
```

Expected: all fresh-quote and existing outbox contract tests pass.

- [ ] **Step 2: Run the full quality gate**

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
git diff --check
```

Expected: full suite passes, Ruff passes, and basedpyright reports zero errors and warnings.

- [ ] **Step 3: Run manual CLI QA**

```bash
uv run python run_kis_paper_scan.py --help
uv run python run_kis_paper_scan.py --top 0
uv run pytest tests/test_run_kis_paper_scan_contracts.py -q
```

Expected: help exits 0, invalid top exits 2 before credential loading or output creation, and the deterministic fake-provider happy path emits one conditional and one quote-validated signal.

- [ ] **Step 4: Run one current-session read-only KIS smoke when configured**

Require a mode-600 `~/.config/trading-agent/kis.env`. Use a unique output directory and `--top 1`; do not start Alpaca Paper, send an external message or count a mid-session start as an eligible forward day.

```bash
uv run python run_kis_paper_scan.py --output-dir outputs/live_runs/20260715_us_quote_readonly_smoke --top 1 --mode live --range-minutes 5 --max-pages 10 --strategy orb
```

Expected: read-only KIS requests only; aggregate output records zero or more validated signals without credential or provider-message output. If `kis.env` is absent, document the pre-network blocker and keep fixture verification authoritative.

- [ ] **Step 5: Update durable documentation**

Document the official endpoint, immutable quote/assessment lineage, strict `<5s` freshness, 25 bp spread, 20 bp entry slippage, manual QA, actual read-only smoke status, and zero broker mutations. Describe outputs as Paper forward-validation candidates, not profitable recommendations.

- [ ] **Step 6: Commit and push the checkpoint**

```bash
git add README.md docs/architecture_ko.md docs/checkpoints/2026-07-16-us-fresh-quote-signal-ko.md
git commit -m "docs: record US fresh quote checkpoint"
git push origin main
```
