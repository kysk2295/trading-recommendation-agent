# LS NWS Subscription Acknowledgement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate the observed LS NWS success acknowledgement before accepting news data while preserving raw-first receipts and sanitized terminal outcomes.

**Architecture:** Extend the strict frame classifier with a separate acknowledgement result, then make the collector enforce `expect_ack -> accept_news` without turning control frames into catalysts. Update the committed fixture and aggregate report so replay and live operation exercise the same protocol order.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite append-only KR ledger, Typer, pytest, Ruff, basedpyright

---

### Task 1: Strict Subscription Acknowledgement Parser

**Files:**
- Modify: `trading_agent/ls_nws.py`
- Modify: `tests/test_ls_nws.py`

- [ ] **Step 1: Write failing acknowledgement parser tests**

Add tests that build a raw frame containing the observed control shape and assert a dedicated result without exposing the message:

```python
def test_parse_subscription_acknowledgement() -> None:
    frame = _frame(
        b'{"header":{"rsp_cd":"00000","rsp_msg":"success ack",'
        b'"tr_cd":"NWS","tr_type":"3"},"body":null}'
    )
    parsed = parse_ls_nws_packet(frame, collection_date=COLLECTION_DATE)
    assert isinstance(parsed, ParsedLsNwsSubscriptionAck)
    assert "success ack" not in repr(parsed)


@pytest.mark.parametrize(
    ("payload", "failure_code"),
    (
        (_ack_payload(rsp_cd="99999"), "subscription_rejected"),
        (_ack_payload(body={}), "invalid_control_packet"),
        (_ack_payload(extra={"unknown": "x"}), "invalid_control_packet"),
        (_ack_payload(rsp_msg=" bad "), "invalid_control_packet"),
    ),
)
def test_reject_invalid_subscription_acknowledgement(
    payload: bytes,
    failure_code: str,
) -> None:
    with pytest.raises(LsNwsParseError, match=failure_code):
        parse_ls_nws_packet(_frame(payload), collection_date=COLLECTION_DATE)
```

- [ ] **Step 2: Run the parser tests and verify RED**

Run:

```bash
uv run pytest tests/test_ls_nws.py -q
```

Expected: collection fails because `parse_ls_nws_packet` and `ParsedLsNwsSubscriptionAck` do not exist.

- [ ] **Step 3: Implement the strict packet classifier**

In `trading_agent/ls_nws.py`, keep `parse_ls_nws_frame()` as the strict news-only compatibility entry point and add:

```python
@dataclass(frozen=True, slots=True)
class ParsedLsNwsSubscriptionAck:
    received_at: dt.datetime


class _LsNwsAckHeader(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    rsp_cd: StrictStr
    rsp_msg: StrictStr
    tr_cd: Literal["NWS"]
    tr_type: Literal["3"]


class _LsNwsAckPacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    header: _LsNwsAckHeader
    body: None


type ParsedLsNwsPacket = ParsedLsNwsSubscriptionAck | ParsedLsNwsNews


def parse_ls_nws_packet(
    frame: LsNwsRawFrame,
    *,
    collection_date: dt.date,
) -> ParsedLsNwsPacket:
    document = _decode_unique_document(frame.raw_payload)
    if _looks_like_control(document):
        packet = _validate_ack(document)
        if packet.header.rsp_cd != "00000":
            raise LsNwsParseError("subscription_rejected")
        return ParsedLsNwsSubscriptionAck(received_at=frame.received_at)
    return _parse_news_document(frame, document, collection_date)
```

Validate `rsp_msg` as trimmed, non-empty, at most 200 characters, with no controls or surrogates. Do not retain it in the returned model or exception. Reuse one duplicate-key-safe JSON decode helper for control and data packets.

- [ ] **Step 4: Run parser tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_ls_nws.py -q
```

Expected: all parser tests pass.

- [ ] **Step 5: Commit the parser**

```bash
git add trading_agent/ls_nws.py tests/test_ls_nws.py
git commit -m "feat: parse LS NWS subscription acknowledgements"
```

### Task 2: Collector Acknowledgement State Machine

**Files:**
- Modify: `trading_agent/ls_nws_collection.py`
- Modify: `tests/test_ls_nws_collection.py`

- [ ] **Step 1: Write failing collection-state tests**

Add focused tests for protocol ordering:

```python
def test_collection_requires_ack_before_news(tmp_path: Path) -> None:
    result = _collect(tmp_path, frames=(_news_frame(sequence=1),))
    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "subscription_ack_missing"
    assert result.receipt_count == 1


def test_ack_only_timeout_is_zero_news_success(tmp_path: Path) -> None:
    result = _collect(tmp_path, frames=(_ack_frame(sequence=1),))
    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.subscription_acknowledged is True
    assert result.receipt_count == 1
    assert result.catalyst_count == 0


def test_duplicate_ack_fails_closed(tmp_path: Path) -> None:
    result = _collect(
        tmp_path,
        frames=(_ack_frame(sequence=1), _ack_frame(sequence=2)),
    )
    assert result.run.failure_code == "duplicate_subscription_ack"
    assert result.receipt_count == 2
```

- [ ] **Step 2: Run collection tests and verify RED**

Run:

```bash
uv run pytest tests/test_ls_nws_collection.py -q
```

Expected: tests fail because the collector treats every frame as news and has no acknowledgement state.

- [ ] **Step 3: Implement the state transition**

Change the parser protocol and result model:

```python
type LsNwsParser = Callable[
    [LsNwsRawFrame, dt.date],
    ParsedLsNwsSubscriptionAck | ParsedLsNwsNews,
]


@dataclass(frozen=True, slots=True)
class LsNwsCollectionResult:
    run: KrSourceCollectionRun
    receipt_count: int
    new_receipt_count: int
    catalyst_count: int
    new_catalyst_count: int
    new_observation_count: int
    restarted: bool
    subscription_acknowledged: bool
```

Bump `LS_NWS_ADAPTER_VERSION` from `ls-nws-v1` to `ls-nws-v2`; the protocol evidence contract changed and old terminal runs must not be presented as acknowledgement-aware runs.

Initialize `subscription_acknowledged = False`. After raw receipt append and sequence validation:

```python
parsed = _parser(effective_frame, collection_date)
expected_sequence += 1
if isinstance(parsed, ParsedLsNwsSubscriptionAck):
    if subscription_acknowledged:
        failure_code = "duplicate_subscription_ack"
        break
    subscription_acknowledged = True
    continue
if not subscription_acknowledged:
    failure_code = "subscription_ack_missing"
    break
```

Only the news branch creates catalysts. A bounded timeout is success only after a successful ack. If the stream closes before any ack, terminate with `subscription_ack_missing`.

For exact terminal and orphan restart results, read only the first stored receipt from SQLite and run the strict packet classifier locally:

```python
def _stored_subscription_acknowledged(
    store: KrThemeStore,
    *,
    source_run_id: str,
    collection_date: dt.date,
) -> bool:
    receipts = store.source_receipts(source_run_id)
    if not receipts:
        return False
    first = receipts[0]
    wire_kind = (
        LsNwsWireKind.BINARY
        if first.receipt.request_key.endswith(":binary")
        else LsNwsWireKind.TEXT
    )
    frame = LsNwsRawFrame(
        sequence=1,
        received_at=first.receipt.received_at,
        wire_kind=wire_kind,
        raw_payload=first.raw_payload,
    )
    try:
        parsed = parse_ls_nws_packet(frame, collection_date=collection_date)
    except LsNwsParseError:
        return False
    return isinstance(parsed, ParsedLsNwsSubscriptionAck)
```

This path never opens credentials, token, network or a fixture manifest and never renders the raw payload or provider message.

- [ ] **Step 4: Run collection tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_ls_nws_collection.py -q
```

Expected: all collection tests pass and raw receipt counts include controls.

- [ ] **Step 5: Commit the state machine**

```bash
git add trading_agent/ls_nws_collection.py tests/test_ls_nws_collection.py
git commit -m "feat: require LS NWS subscription acknowledgement"
```

### Task 3: Fixture, CLI And Aggregate Report

**Files:**
- Create: `tests/fixtures/ls_nws/frame-000000.json`
- Modify: `tests/fixtures/ls_nws/fixture-manifest.json`
- Modify: `trading_agent/ls_nws_fixture.py`
- Modify: `run_ls_nws_collect.py`
- Modify: `tests/test_ls_nws_fixture.py`
- Modify: `tests/test_ls_nws_collect_cli.py`

- [ ] **Step 1: Write failing fixture and CLI expectations**

Update tests to expect three receipts, two catalysts and an acknowledged aggregate:

```python
assert result.receipt_count == 3
assert result.catalyst_count == 2
assert result.subscription_acknowledged is True
assert "구독 확인: 예" in report
assert "rsp_msg" not in report
```

Add a restart assertion that an existing terminal v2 run derives acknowledgement from the stored first receipt without opening or rereading a missing fixture manifest.

- [ ] **Step 2: Run fixture and CLI tests and verify RED**

Run:

```bash
uv run pytest tests/test_ls_nws_fixture.py tests/test_ls_nws_collect_cli.py -q
```

Expected: receipt count and report assertions fail.

- [ ] **Step 3: Add the committed acknowledgement fixture**

Add an exact synthetic success packet:

```json
{"header":{"rsp_cd":"00000","rsp_msg":"subscription accepted","tr_cd":"NWS","tr_type":"3"},"body":null}
```

Place it first in the fixture manifest, renumber sequence assignment through the existing manifest loader, and keep the two existing news payloads unchanged.

- [ ] **Step 4: Update CLI reporting**

Add only the aggregate line:

```python
f"- 구독 확인: {'예' if result.subscription_acknowledged else '아니오'}"
```

Do not include response code, message, endpoint, quote, receipt identity, checksum or raw frame.

- [ ] **Step 5: Run fixture and CLI tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_ls_nws_fixture.py tests/test_ls_nws_collect_cli.py -q
```

Expected: all fixture and CLI tests pass.

- [ ] **Step 6: Commit fixture and reporting changes**

```bash
git add tests/fixtures/ls_nws trading_agent/ls_nws_fixture.py run_ls_nws_collect.py tests/test_ls_nws_fixture.py tests/test_ls_nws_collect_cli.py
git commit -m "test: cover LS NWS subscription handshake"
```

### Task 4: Verification, Live Smoke And Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture_ko.md`
- Create: `docs/checkpoints/2026-07-16-ls-nws-subscription-ack-ko.md`

- [ ] **Step 1: Run focused and full verification**

```bash
uv run pytest tests/test_ls_nws.py tests/test_ls_nws_stream.py tests/test_ls_nws_collection.py tests/test_ls_nws_fixture.py tests/test_ls_nws_collect_cli.py -q
uv run pytest -q
uv run ruff check .
uv run basedpyright
git diff --check
```

Expected: all tests pass, Ruff passes, and basedpyright reports zero errors and warnings.

- [ ] **Step 2: Run manual CLI QA**

```bash
uv run python run_ls_nws_collect.py --help
uv run python run_ls_nws_collect.py --collection-cycle-id ../bad --collection-date 2026-07-16
uv run python run_ls_nws_collect.py --collection-cycle-id kr-ls-nws-ack-fixture-001 --collection-date 2026-07-15 --duration-seconds 1 --max-frames 3 --database /tmp/ls-nws-ack.sqlite3 --output-dir /tmp/ls-nws-ack-report --fixture-manifest tests/fixtures/ls_nws/fixture-manifest.json
```

Expected: help exits 0, bad input exits 2 before DB creation, fixture exits 0 with three receipts and two catalysts.

- [ ] **Step 3: Run a new bounded production smoke**

Use a new cycle ID and private output path with the rotated mode-600 local credentials:

```bash
uv run python run_ls_nws_collect.py --collection-cycle-id kr-ls-nws-ack-smoke-20260716 --collection-date 2026-07-16 --duration-seconds 10 --max-frames 20 --database outputs/kr_theme/ls_nws/ack-smoke-20260716/kr_theme.sqlite3 --output-dir outputs/kr_theme/ls_nws/ack-smoke-20260716/report
```

Expected: acknowledgement is recorded, no `invalid_packet`, no account/order call, and either zero or more causally valid news records.

- [ ] **Step 4: Update durable documentation**

Record the first failed smoke as immutable evidence, the redacted root cause, the new parser/state contract, test counts, live smoke aggregate and external mutation count. Do not include credentials, tokens, account data, raw frame, provider message or hashes.

- [ ] **Step 5: Commit and push the checkpoint**

```bash
git add README.md docs/architecture_ko.md docs/checkpoints/2026-07-16-ls-nws-subscription-ack-ko.md
git commit -m "docs: record LS NWS handshake checkpoint"
git push origin main
```
