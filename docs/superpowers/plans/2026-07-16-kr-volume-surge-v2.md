# KR Volume Surge V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. External workers must not spawn subagents; Codex owns reconciliation and final verification.

**Goal:** Derive one canonical schema-v2 `volume_surge` catalyst and terminal source run from immutable same-cycle KIS volume-ranking evidence without opening credentials or network.

**Architecture:** Preserve the existing numeric-only v1 payload parser and add a versioned `[0-9A-Z]{6}` instrument contract plus v2 payload with row-level upstream catalyst lineage. A DB-only derivation state machine validates the exact successful KIS source run, computes fixed-context ratios, appends one deterministic derived catalyst, and closes an idempotent terminal source run. A thin CLI exposes only local cycle/date/path inputs and aggregate private reporting.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite through `KrThemeStore`, Typer, pytest, Ruff, basedpyright.

---

## File Map

- Create `trading_agent/kr_instrument.py`: explicit v1 numeric and v2 uppercase-alphanumeric KR short-code validators.
- Create `trading_agent/kr_volume_surge_models.py`: replay-compatible v1 models, strict v2 lineage models, canonical serializer and version-dispatched parser.
- Modify `trading_agent/kr_theme_projection.py`: import/re-export volume models and accept verified v2 metrics without changing v1 related-symbol semantics.
- Create `trading_agent/kr_volume_surge.py`: upstream ledger validation, deterministic derivation, append-only restart and terminal state machine.
- Create `run_kr_volume_surge_derive.py`: DB-only CLI and aggregate mode-600 report.
- Create `tests/test_kr_instrument.py`: instrument-version contract tests.
- Create `tests/test_kr_volume_surge_models.py`: v1/v2 model, serialization and parser tests.
- Modify `tests/test_kr_theme_projection.py`: v1 replay and v2 upstream-lineage projection tests.
- Create `tests/test_kr_volume_surge.py`: derivation, failure and restart tests.
- Create `tests/test_kr_volume_surge_cli.py`: CLI contract, E2E, redaction and mode tests.
- Modify `pyproject.toml`: include the new CLI in basedpyright.
- Modify `README.md`, `CODEX_START_HERE.md`: record the source capability and orchestrator next milestone.
- Create `docs/checkpoints/2026-07-16-kr-volume-surge-v2-ko.md`: verified local and production-ledger checkpoint.

### Task 1: Versioned KR Instrument And Volume Models

**Files:**
- Create: `trading_agent/kr_instrument.py`
- Create: `trading_agent/kr_volume_surge_models.py`
- Create: `tests/test_kr_instrument.py`
- Create: `tests/test_kr_volume_surge_models.py`
- Modify: `trading_agent/kr_theme_projection.py:42-88`
- Modify: `tests/test_kr_theme_projection.py:24-77`

- [ ] **Step 1: Write failing instrument-version tests**

Test that v1 accepts only six digits, v2 accepts six uppercase alphanumeric characters including the observed KIS shape, and both reject lowercase, whitespace, punctuation, controls and wrong lengths.

```python
def test_kr_instrument_symbol_versions_are_explicit() -> None:
    assert is_kr_instrument_symbol_v1("005930")
    assert not is_kr_instrument_symbol_v1("1234A0")
    assert is_kr_instrument_symbol_v2("005930")
    assert is_kr_instrument_symbol_v2("1234A0")
    assert not is_kr_instrument_symbol_v2("1234a0")
```

- [ ] **Step 2: Run the instrument test and confirm RED**

Run: `uv run pytest -q tests/test_kr_instrument.py`

Expected: import failure because `trading_agent.kr_instrument` does not exist.

- [ ] **Step 3: Implement the two immutable validators**

Expose only:

```python
KR_INSTRUMENT_SCHEMA_V1: Final = 1
KR_INSTRUMENT_SCHEMA_V2: Final = 2
_KR_SYMBOL_V1 = re.compile(r"^[0-9]{6}$")
_KR_SYMBOL_V2 = re.compile(r"^[0-9A-Z]{6}$")

def is_kr_instrument_symbol_v1(value: str) -> bool:
    return isinstance(value, str) and _KR_SYMBOL_V1.fullmatch(value) is not None

def is_kr_instrument_symbol_v2(value: str) -> bool:
    return isinstance(value, str) and _KR_SYMBOL_V2.fullmatch(value) is not None
```

Require `isinstance(value, str)`, exact length/pattern and no normalization. Do not uppercase or strip caller input.

- [ ] **Step 4: Write failing v1/v2 payload tests**

Cover existing v1 numeric parsing, v1 alphanumeric rejection, v2 alphanumeric acceptance, numeric v2 compatibility, sorted unique symbols, unique source catalyst IDs, finite nonnegative values, aware causal times, exact safe source run ID, empty v2 symbols, unknown schema and extra fields. Assert raw payload is not in exception text or repr.

```python
def test_volume_surge_v2_preserves_alphanumeric_lineage() -> None:
    payload = KrVolumeSurgePayloadV2(
        observed_at=DERIVED_AT,
        source_observed_at=SOURCE_AT,
        source_run_id="cycle-001:kis_ranking",
        symbols=(
            KrVolumeSurgeSymbolV2(
                symbol="1234A0",
                trading_value_krw=Decimal("100"),
                volume_ratio=Decimal("2.5"),
                source_catalyst_id="a" * 64,
            ),
        ),
    )
    assert parse_kr_volume_surge_payload(canonical_kr_volume_surge_payload(payload)) == payload
```

- [ ] **Step 5: Implement replay-compatible models and parser**

Move the current v1 model definitions without semantic changes into `kr_volume_surge_models.py`. Add `KrVolumeSurgeSymbolV2` and `KrVolumeSurgePayloadV2` from the design. Use explicit top-level `schema_version` dispatch after `json.loads`; support only integer `1` and `2`, then Pydantic `extra="forbid"` validation. Expose:

```python
KrVolumeSurgePayloadAny: TypeAlias = KrVolumeSurgePayload | KrVolumeSurgePayloadV2

def canonical_kr_volume_surge_payload(payload: KrVolumeSurgePayloadAny) -> bytes:
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

def parse_kr_volume_surge_payload(raw_payload: bytes) -> KrVolumeSurgePayloadAny:
    document = json.loads(raw_payload)
    if not isinstance(document, dict):
        raise InvalidKrVolumeSurgePayloadError
    model = {1: KrVolumeSurgePayload, 2: KrVolumeSurgePayloadV2}.get(
        document.get("schema_version")
    )
    if model is None:
        raise InvalidKrVolumeSurgePayloadError
    return model.model_validate(document)
```

Import the classes into `kr_theme_projection.py` so existing `from trading_agent.kr_theme_projection import KrVolumeSurgePayload` callers continue to work.

- [ ] **Step 6: Verify and commit Task 1**

Run:

```bash
uv run pytest -q tests/test_kr_instrument.py tests/test_kr_volume_surge_models.py tests/test_kr_theme_projection.py
uv run ruff check trading_agent/kr_instrument.py trading_agent/kr_volume_surge_models.py trading_agent/kr_theme_projection.py tests/test_kr_instrument.py tests/test_kr_volume_surge_models.py tests/test_kr_theme_projection.py
uv run basedpyright trading_agent/kr_instrument.py trading_agent/kr_volume_surge_models.py trading_agent/kr_theme_projection.py
```

Expected: all exit 0.

```bash
git add trading_agent/kr_instrument.py trading_agent/kr_volume_surge_models.py trading_agent/kr_theme_projection.py tests/test_kr_instrument.py tests/test_kr_volume_surge_models.py tests/test_kr_theme_projection.py
git commit -m "feat: version KR volume surge symbols"
```

### Task 2: Downstream V2 Projection Lineage

**Files:**
- Modify: `trading_agent/kr_theme_projection.py:364-394`
- Modify: `tests/test_kr_theme_projection.py`

- [ ] **Step 1: Write failing v2 projection lineage tests**

Build one complete synthetic cycle containing a KIS volume-ranking catalyst and a v2 volume-surge catalyst that points to it. Prove numeric v1 related symbols still project. Add failures for missing source catalyst, non-KIS source, source symbol mismatch, non-volume KIS row, future source observation and `source_observed_at > observed_at`.

```python
def test_projection_accepts_v2_metric_with_exact_kis_lineage() -> None:
    cycle, catalysts, observations, classifications = _complete_v2_lineage_fixture()
    projections = project_kr_theme_opportunities(
        cycle,
        catalysts,
        observations,
        classifications,
        classifier_version="keyword-v1",
        prompt_version="no-prompt-v1",
        classification_run_id="classification-run-001",
        projected_at=PROJECTED_AT,
        validity=dt.timedelta(minutes=10),
        producer_strategy_version="theme-projection-v1",
    )
    assert projections[0].state.leader_symbol == "005930"
```

Define `_complete_v2_lineage_fixture` in the same test file. It must return the exact four objects shown above and construct both the KIS row catalyst and its v2 metric catalyst explicitly; it must not call production derivation code, so projection validation is tested independently.

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `uv run pytest -q tests/test_kr_theme_projection.py -k v2`

Expected: fail because `_volume_metrics` still validates only the v1 payload shape.

- [ ] **Step 3: Implement explicit payload dispatch and lineage checks**

Replace direct `KrVolumeSurgePayload.model_validate_json` with `parse_kr_volume_surge_payload`. Keep v1 behavior unchanged. For each v2 metric, require the referenced catalyst and observation in the exact projection cycle, source `KIS_RANKING`, strict `KisKrRankingItem` with kind `VOLUME`, identical symbol, and source observation no later than payload `source_observed_at`. Require the payload's `source_observed_at <= observed_at <= projected_at` and exact source run suffix `:kis_ranking`.

Convert v1/v2 items to the existing internal metric dataclass so candidate ordering and evidence IDs do not change for v1.

- [ ] **Step 4: Verify and commit Task 2**

Run:

```bash
uv run pytest -q tests/test_kr_theme_projection.py tests/test_kr_volume_surge_models.py
uv run ruff check trading_agent/kr_theme_projection.py tests/test_kr_theme_projection.py
uv run basedpyright trading_agent/kr_theme_projection.py
```

Expected: all exit 0.

```bash
git add trading_agent/kr_theme_projection.py tests/test_kr_theme_projection.py
git commit -m "feat: verify volume surge v2 lineage"
```

### Task 3: DB-Only Derivation State Machine

**Files:**
- Create: `trading_agent/kr_volume_surge.py`
- Create: `tests/test_kr_volume_surge.py`

- [ ] **Step 1: Write failing happy-path derivation tests**

Seed `KrThemeStore` through `collect_kis_kr_rankings` using deterministic fluctuation and volume responses. Assert one v2 catalyst, same-cycle observation, source run, sorted metrics, exact row catalyst IDs, `trading_value_krw`, `volume_ratio`, derivation/source times, adapter version and mode 600. Include an alphanumeric KIS symbol and assert it remains in the stored payload.

```python
result = derive_kr_volume_surge(
    store,
    collection_cycle_id="cycle-001",
    collection_date=COLLECTION_DATE,
    _clock=lambda: DERIVED_AT,
)
assert result.run.status is KrCoverageStatus.SUCCESS
assert result.symbol_count == 2
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest -q tests/test_kr_volume_surge.py -k happy`

Expected: import failure because `trading_agent.kr_volume_surge` does not exist.

- [ ] **Step 3: Define public result and stable errors**

```python
KR_VOLUME_SURGE_ADAPTER_VERSION: Final = "kis-ranking-volume-surge-v2"
KIS_RANKING_INPUT_ADAPTER_VERSION: Final = "kis-kr-ranking-v1"

@dataclass(frozen=True, slots=True)
class KrVolumeSurgeDerivationResult:
    run: KrSourceCollectionRun
    symbol_count: int
    new_catalyst_count: int
    new_observation_count: int
    restarted: bool

class KrVolumeSurgeSourceNotReadyError(ValueError):
    @override
    def __str__(self) -> str:
        return "volume surge upstream KIS source가 아직 terminal이 아닙니다"
```

Expose `derive_kr_volume_surge(store, *, collection_cycle_id, collection_date, _clock, _after_catalyst)`. Stable failed-run codes are `upstream_kis_failed`, `invalid_upstream_evidence`, `zero_average_volume`, and `invalid_derivation_time`.

- [ ] **Step 4: Implement exact upstream selection and lineage validation**

Before clock or writer use, replay one exact existing volume terminal run. Otherwise require one exact KIS run and select same-cycle KIS observations. Cross-check run record/receipt counts, one observation receipt link per catalyst, payload/checksum/source identities and strict `KisKrRankingItem` parsing. Select only `VOLUME` rows but require at least one volume receipt even when rows are empty.

Build ratios under:

```python
with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
    volume_ratio = Decimal(item.accumulated_volume) / Decimal(item.average_volume)
```

Reject `average_volume is None or <= 0` and missing trading value without dropping rows.

- [ ] **Step 5: Implement append and terminal restart**

Create canonical v2 payload, deterministic catalyst record and observation, append them in one writer call, invoke `_after_catalyst`, then append terminal run in a second writer call. This deliberately exposes the crash window for the orphan restart test. Exact existing catalyst append is no-op; content conflict bubbles as `KrThemeConflictError`.

For terminal failed KIS input, append only a failed volume run. For missing KIS run, raise `KrVolumeSurgeSourceNotReadyError` and write nothing.

- [ ] **Step 6: Add failure and replay tests**

Cover terminal replay with rejecting clock/read hooks, missing upstream no write, failed upstream terminal volume failure, wrong adapter/date, malformed KIS payload/checksum/lineage, duplicate link/symbol/source ID, zero average, missing trading value, zero rows, derivation clock before upstream, crash after catalyst and deterministic restart, incompatible existing volume run, writer conflict and process-global Decimal context independence.

- [ ] **Step 7: Verify and commit Task 3**

Run:

```bash
uv run pytest -q tests/test_kr_volume_surge.py tests/test_kis_kr_ranking_collection.py tests/test_kr_volume_surge_models.py
uv run ruff check trading_agent/kr_volume_surge.py tests/test_kr_volume_surge.py
uv run basedpyright trading_agent/kr_volume_surge.py
```

Expected: all exit 0.

```bash
git add trading_agent/kr_volume_surge.py tests/test_kr_volume_surge.py
git commit -m "feat: derive KR volume surge evidence"
```

### Task 4: Bounded Local CLI

**Files:**
- Create: `run_kr_volume_surge_derive.py`
- Create: `tests/test_kr_volume_surge_cli.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing CLI contract tests**

Test only four approved options plus help, invalid ID/date before DB/report, missing DB/source-not-ready without output, fixture-seeded happy path, terminal replay with rejecting derivation dependencies, failed source report/nonzero, mode 600 and redaction of symbol, IDs, hashes, raw payload and paths.

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest -q tests/test_kr_volume_surge_cli.py`

Expected: import failure for `run_kr_volume_surge_derive`.

- [ ] **Step 3: Implement local-only CLI and report**

The CLI order is:

```text
parse safe cycle/date
-> require existing regular DB
-> construct KrThemeStore
-> derive/replay
-> write aggregate private report
-> failed terminal run becomes nonzero
```

Catch only reviewed input/store/source errors and replace unexpected `ValueError` text with one fixed Korean message. Do not import KIS auth, HTTP, credential, provider or broker modules. Write `kr_volume_surge_derivation_summary_ko.md` through `write_private_report`.

- [ ] **Step 4: Add CLI to basedpyright and manual QA**

Run:

```bash
uv run pytest -q tests/test_kr_volume_surge_cli.py
uv run ruff check run_kr_volume_surge_derive.py tests/test_kr_volume_surge_cli.py pyproject.toml
uv run basedpyright run_kr_volume_surge_derive.py
uv run python run_kr_volume_surge_derive.py --help
```

Use a temporary DB seeded by the committed KIS fixture. Confirm bad `../escape` exits 2 before writes, first derivation exits 0, replay exits 0 with new counts 0, reports/DB are mode 600 and private-marker scan is empty.

- [ ] **Step 5: Commit Task 4**

```bash
git add run_kr_volume_surge_derive.py tests/test_kr_volume_surge_cli.py pyproject.toml
git commit -m "feat: add KR volume surge derive CLI"
```

### Task 5: Documentation, Local Production-Ledger QA And Integration

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Create: `docs/checkpoints/2026-07-16-kr-volume-surge-v2-ko.md`

- [ ] **Step 1: Update operating docs**

Document the DB-only command, v1/v2 compatibility, causal timestamps, no-network boundary and that alphanumeric metrics remain source evidence until a related-symbol/classification v2 milestone. Set the next KR priority to the four-adapter orchestrator, while preserving the market-time-gated US Paper lifecycle smoke.

- [ ] **Step 2: Run bounded production-ledger derivation**

Only if the previously verified temporary KIS production ledger still exists and its terminal `kis_ranking` run is success, run the new derivation against that local DB. Do not open KIS credentials or network. Report only aggregate upstream receipt/row, derived symbol/catalyst and replay-new counts. If the temp ledger is absent, state that production-ledger QA was not run and rely on fixture E2E.

- [ ] **Step 3: Run independent safety review**

Inspect all changes since `b03a291`. Search production files for HTTP/client/auth imports, URL, token, account/balance/position/order, POST/PUT/PATCH/DELETE, external messaging, raw/symbol logging and user-supplied provider controls. Any match must be removed or explained as a negative test.

- [ ] **Step 4: Run full verification**

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
uv run python run_kr_volume_surge_derive.py --help
```

Record exact counts and durations in the checkpoint. Confirm `git diff --check b03a291..HEAD`, planned file scope and a clean worker branch.

- [ ] **Step 5: Commit docs, integrate and push**

```bash
git add README.md CODEX_START_HERE.md docs/checkpoints/2026-07-16-kr-volume-surge-v2-ko.md
git commit -m "docs: record KR volume surge v2 checkpoint"
```

Fast-forward reviewed commits onto clean `main`, rerun the full verification on merged main, push `origin main`, confirm local `HEAD == origin/main`, then remove this owned `.worktrees/kr-volume-surge-v2` worktree and feature branch.
