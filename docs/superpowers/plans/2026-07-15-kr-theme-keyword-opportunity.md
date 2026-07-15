# KR Theme Keyword Opportunity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Follow test-driven development. This milestone is local-only and must not add a network, LLM, quote, trading signal, account, balance, position, or order path.

**Goal:** Classify exact-cycle KR news/DART catalysts with a deterministic keyword baseline, replay theme freshness/dissemination/leader state from stored evidence, and publish one immutable `kr_equities/opportunity_manager/theme_momentum` OpportunitySnapshot per theme.

**Architecture:** A strict local rule set classifies only explicit top-level text fields and appends existing `KrThemeClassification` rows. A pure projector joins one complete collection cycle, one exact classifier cohort, and canonical `volume_surge` BLOB metrics; it rejects missing lineage and ranks related symbols by stored trading value. A local run-manifest CLI exercises ingest-to-opportunity without HTTP, credentials, LLMs, quotes, or broker code.

**Tech Stack:** Python 3.12, Pydantic 2, SQLite, Typer, pytest, Ruff, basedpyright, uv

---

## Safety And Scope Invariants

- Use only `MarketId.KR_EQUITIES`, `AgentFamily.OPPORTUNITY_MANAGER`, strategy `theme_momentum`.
- Do not import KIS/Alpaca clients, auth, HTTP, broker, order, account, balance, position, quote, or TradeSignal modules.
- Raw BLOB and extracted full text must have `repr=False` and never enter exception strings, CLI output, reports, or Opportunity JSONL.
- Classify only `news` and `dart` `application/json` payloads with explicit top-level string fields.
- Never mix classifier kind/version, prompt version, or run ID in one projection.
- Require a final cycle with all four source coverage rows successful before Opportunity publication.
- Read leader metrics only from exact-cycle `volume_surge` catalyst BLOBs after ledger checksum validation.
- Require one metric for every projected related symbol; missing or duplicate metrics fail closed.
- Opportunity candidates are ranked only by `trading_value_krw DESC, symbol ASC`; no LLM or arbitrary composite strength score.
- Theme outputs remain separate snapshots; do not merge themes or deduplicate symbols across themes after the fact.
- Existing KR schema v1 remains unchanged. Existing US Opportunity, Paper execution, and outboxes remain unchanged.

## File Map

- Create `trading_agent/kr_theme_keyword.py`: strict rule-set contracts, text extraction, deterministic classification.
- Create `tests/test_kr_theme_keyword.py`: canonical rules, strict payload, positive/irrelevant/ambiguous classification, privacy tests.
- Create `trading_agent/kr_theme_projection.py`: canonical volume payload, theme state, exact-cohort projector, common OpportunitySnapshot adapter.
- Create `tests/test_kr_theme_projection.py`: cycle/cohort/metric causality and deterministic leader tests.
- Create `trading_agent/kr_theme_projection_manifest.py`: path-safe run manifest and rule loader.
- Create `run_kr_theme_projection.py`: local classification append, projection, immutable Opportunity outbox, Korean aggregate report.
- Create `tests/test_kr_theme_projection_manifest.py`: schema/path containment tests.
- Create `tests/test_kr_theme_projection_cli.py`: ingest-to-opportunity E2E, restart, incomplete cycle, output redaction.
- Create `examples/kr_theme_projection/`: synthetic ingest manifest, three raw payloads, keyword rules, projection run manifest.
- Modify `README.md`: describe the implemented local baseline without claiming live KR collection or signal execution.
- Create `docs/checkpoints/2026-07-15-kr-theme-keyword-opportunity-ko.md`: exact verification and deferred production adapters.

### Task 1: Deterministic Keyword Classification

**Files:**
- Create: `tests/test_kr_theme_keyword.py`
- Create: `trading_agent/kr_theme_keyword.py`

- [x] **Step 1: Write failing rule and classifier tests**

Specify these public contracts:

```text
class KrKeywordRule(BaseModel):
    schema_version: Literal[1] = 1
    theme_name: str
    keywords: tuple[str, ...]
    related_symbols: tuple[KrRelatedSymbol, ...]

class KrKeywordRuleSet(BaseModel):
    schema_version: Literal[1] = 1
    classifier_version: str
    prompt_version: str
    rules: tuple[KrKeywordRule, ...]

classify_kr_keyword_catalyst(
    catalyst: StoredKrCatalyst,
    rules: KrKeywordRuleSet,
    *,
    classification_run_id: str,
    classified_at: datetime,
) -> KrThemeClassification
```

Tests must prove:

- rules, keywords, and related symbols require sorted unique canonical order;
- only `news`/`dart` + `application/json` is accepted;
- supported fields are exactly `title`, `body`, `summary`, `report_name`, `company_name` in fixed extraction order;
- invalid JSON, no supported text, non-string supported fields, empty/control-character text fail with one safe error string;
- a single matching theme creates positive `KrThemeClassification` with rule symbols and a maximum-200-character evidence quote;
- no match creates irrelevant classification with no theme/symbols;
- two matching themes raise ambiguity rather than choosing one;
- raw bytes and full extracted text do not appear in result wrapper repr or any exception string;
- fixed run ID/time produces an identical classification ID and payload.

- [x] **Step 2: Verify RED**

Run: `uv run pytest tests/test_kr_theme_keyword.py -q`

Expected: import failure because `trading_agent.kr_theme_keyword` does not exist.

- [x] **Step 3: Implement the minimal strict keyword engine**

Implementation rules:

```text
SUPPORTED_TEXT_FIELDS = ("title", "body", "summary", "report_name", "company_name")
ELIGIBLE_SOURCES = frozenset({KrCatalystSource.NEWS, KrCatalystSource.DART})
```

- Parse with `json.loads()` and require a JSON object.
- Inspect only supported top-level fields; ignore unrelated metadata but never recurse.
- Normalize only for matching with `casefold()`; preserve the bounded source field for evidence.
- Match literal substrings. Build the matching-theme set before selecting.
- Use `Decimal(1)` for deterministic rule execution confidence, not predictive probability.
- Raise `InvalidKrKeywordClassificationError` whose `__str__` contains no payload, text, symbol, source record ID, or path.
- Keep any internal extracted-field dataclass fields `repr=False`.

- [x] **Step 4: Verify GREEN and focused static checks**

Run:

```bash
uv run pytest tests/test_kr_theme_keyword.py -q
uv run ruff check trading_agent/kr_theme_keyword.py tests/test_kr_theme_keyword.py
uv run basedpyright trading_agent/kr_theme_keyword.py tests/test_kr_theme_keyword.py
```

- [x] **Step 5: Commit Task 1**

```bash
git add trading_agent/kr_theme_keyword.py tests/test_kr_theme_keyword.py
git commit -m "feat: add KR keyword theme baseline"
```

### Task 2: Stored-Evidence Theme And Opportunity Projection

**Files:**
- Create: `tests/test_kr_theme_projection.py`
- Create: `trading_agent/kr_theme_projection.py`

- [x] **Step 1: Write failing projection contract tests**

Specify:

```python
class KrVolumeSurgeSymbol(BaseModel):
    schema_version: Literal[1] = 1
    symbol: str
    trading_value_krw: Decimal
    volume_ratio: Decimal

class KrVolumeSurgePayload(BaseModel):
    schema_version: Literal[1] = 1
    observed_at: datetime
    symbols: tuple[KrVolumeSurgeSymbol, ...]

class KrProjectedThemeSymbol(BaseModel):
    symbol: str
    trading_value_krw: Decimal
    volume_ratio: Decimal

class KrThemeState(BaseModel):
    schema_version: Literal[1] = 1
    state_id: str
    collection_cycle_id: str
    theme_name: str
    classifier_version: str
    prompt_version: str
    classification_run_id: str
    first_observed_at: datetime
    latest_observed_at: datetime
    projected_at: datetime
    freshness_seconds: int
    catalyst_count: int
    publisher_count: int
    related_symbols: tuple[KrProjectedThemeSymbol, ...]
    total_trading_value_krw: Decimal
    leader_symbol: str
    classification_ids: tuple[str, ...]
    market_catalyst_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class KrThemeOpportunityProjection:
    state: KrThemeState
    opportunity: OpportunitySnapshot

project_kr_theme_opportunities(
    cycle: KrCatalystCollectionCycle,
    catalysts: tuple[StoredKrCatalyst, ...],
    observations: tuple[KrCatalystObservation, ...],
    classifications: tuple[KrThemeClassification, ...],
    *,
    classifier_version: str,
    prompt_version: str,
    classification_run_id: str,
    projected_at: datetime,
    validity: timedelta,
    producer_strategy_version: str,
) -> tuple[KrThemeOpportunityProjection, ...]
```

Tests must prove:

- volume payload requires aware time, six-digit sorted unique symbols, finite nonnegative trading value and ratio;
- volume payload time equals the exact cycle observation for its `volume_surge` catalyst;
- incomplete source coverage blocks all Opportunity output;
- every cycle news/DART catalyst has exactly one classification in the selected keyword cohort;
- different classifier versions/runs are ignored, never mixed; a missing selected-cohort row blocks;
- classification and market observation after `projected_at` blocks;
- positive classifications for the same theme aggregate catalyst and publisher counts;
- irrelevant classifications remain coverage evidence but create no theme;
- every related symbol requires one volume metric and conflicting duplicate metrics block;
- state freshness is `projected_at - min(first_observed_at)` in whole seconds;
- related symbols rank by trading value descending then symbol ascending; rank 1 is `leader_symbol`;
- candidate score equals trading value and feature names are canonical sorted;
- one separate KR `OpportunitySnapshot` is emitted per theme with complete four-source coverage and only canonical evidence IDs;
- repeated pure projection returns byte-equivalent model dumps and deterministic IDs.

- [x] **Step 2: Verify RED**

Run: `uv run pytest tests/test_kr_theme_projection.py -q`

Expected: import failure because `trading_agent.kr_theme_projection` does not exist.

- [x] **Step 3: Implement strict volume parsing and pure projection**

Use constants:

```python
KR_THEME_OPPORTUNITY_LANE = StrategyLaneRef(
    market_id=MarketId.KR_EQUITIES,
    agent_family=AgentFamily.OPPORTUNITY_MANAGER,
    strategy_id="theme_momentum",
)
```

Projection requirements:

- Build exact cycle catalyst IDs from observations and reject duplicate/missing catalyst references.
- Reparse every `StoredKrCatalyst` through existing checksum-validating reader output; never trust an external metric manifest.
- Parse only `KrCatalystSource.VOLUME_SURGE` BLOBs as `KrVolumeSurgePayload`.
- Filter classifications by all four cohort keys: keyword kind, classifier version, prompt version, run ID.
- Group only positive rows by `theme_name`; validate repeated symbol relation/rationale is identical before metric join.
- Preserve raw strength components; do not calculate a weighted theme score.
- Build evidence namespaces `kr/collection_cycle`, `kr/theme_classification`, and `kr/catalyst/volume_surge`.
- Convert each exact final source coverage entry to common `SourceCoverage` with `observed_at=cycle.completed_at`.
- Generate safe deterministic SHA-256-based state/opportunity IDs from cycle, theme, cohort, evidence IDs, and projected time.

- [x] **Step 4: Verify GREEN and focused static checks**

Run:

```bash
uv run pytest tests/test_kr_theme_projection.py -q
uv run ruff check trading_agent/kr_theme_projection.py tests/test_kr_theme_projection.py
uv run basedpyright trading_agent/kr_theme_projection.py tests/test_kr_theme_projection.py
```

- [x] **Step 5: Commit Task 2**

```bash
git add trading_agent/kr_theme_projection.py tests/test_kr_theme_projection.py
git commit -m "feat: project stored KR themes to opportunities"
```

### Task 3: Local Projection Run Manifest And CLI E2E

**Files:**
- Create: `tests/test_kr_theme_projection_manifest.py`
- Create: `tests/test_kr_theme_projection_cli.py`
- Create: `trading_agent/kr_theme_projection_manifest.py`
- Create: `run_kr_theme_projection.py`
- Create: `examples/kr_theme_projection/ingest-manifest.json`
- Create: `examples/kr_theme_projection/news-synthetic.json`
- Create: `examples/kr_theme_projection/kis-ranking-synthetic.json`
- Create: `examples/kr_theme_projection/volume-surge-synthetic.json`
- Create: `examples/kr_theme_projection/keyword-rules.json`
- Create: `examples/kr_theme_projection/projection-run.json`

- [x] **Step 1: Write failing manifest and CLI tests**

Run manifest fields:

```text
schema_version=1
collection_cycle_id
rules_path
classification_run_id
classified_at
projected_at
validity_seconds (1..3600)
producer_strategy_version
```

Test:

- run manifest and rules parse structurally with `extra=forbid`;
- rules path is a relative regular file under run-manifest directory;
- traversal, symlink escape, missing/invalid rule file, naive/future-inverted time, unsafe IDs and invalid validity fail before writer/outbox creation;
- CLI reads one exact complete cycle and classifies only its news/DART catalysts;
- happy path appends one classification, projects one theme, writes one `opportunities.v1.jsonl` row and one aggregate Korean report;
- Opportunity lane is `kr_equities/opportunity_manager/theme_momentum`, symbol is `005930`, rank is 1, and action remains an Opportunity only;
- report contains theme name, leader symbol, freshness and component counts but no raw title/body, evidence quote, source record ID, payload hash, DB path, or credential-like values;
- exact restart appends no duplicate classification or Opportunity row;
- incomplete cycle, ambiguous rules, missing volume metric and corrupt existing outbox fail closed with safe Typer errors;
- invalid manifest fails before database or output creation.

- [x] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_kr_theme_projection_manifest.py tests/test_kr_theme_projection_cli.py -q
```

Expected: imports fail because the manifest module and CLI do not exist.

- [x] **Step 3: Implement path-safe manifest loading and local CLI**

Execution order:

1. Load and validate run manifest and contained rule file.
2. Open the existing KR ledger read-only and require schema v1.
3. Select one exact cycle, its observations/catalysts, and existing classifications.
4. Generate all keyword classifications in memory.
5. Merge exact existing/generated rows by classification ID; changed payload conflicts.
6. Project theme states and Opportunities entirely in memory.
7. Open one writer lease and append generated classifications.
8. Append Opportunities through `append_opportunity_snapshot`.
9. Write the aggregate report and print counts only.

Catch only known safe domain/Pydantic/outbox errors and translate them to one redacted `typer.BadParameter`. Do not catch programmer errors broadly. Import no HTTP, credentials, broker, quote, or signal modules.

Synthetic fixture:

- one news catalyst whose title contains a synthetic semiconductor keyword;
- one KIS-ranking catalyst for source coverage only;
- one canonical volume-surge catalyst for `005930` with finite string Decimals;
- one `반도체` rule related to `005930`;
- all dates fixed and explicitly synthetic;
- zero DART rows with successful DART coverage.

- [x] **Step 4: Verify GREEN and manual CLI QA**

Run:

```bash
uv run pytest tests/test_kr_theme_projection_manifest.py tests/test_kr_theme_projection_cli.py -q
uv run ruff check trading_agent/kr_theme_projection_manifest.py run_kr_theme_projection.py tests/test_kr_theme_projection_manifest.py tests/test_kr_theme_projection_cli.py
uv run basedpyright trading_agent/kr_theme_projection_manifest.py run_kr_theme_projection.py tests/test_kr_theme_projection_manifest.py tests/test_kr_theme_projection_cli.py
./run_kr_theme_projection.py --help
./run_kr_theme_projection.py --run-manifest examples/kr_theme_projection/missing.json --database /tmp/kr-projection-invalid.sqlite3 --output-dir /tmp/kr-projection-invalid
```

For happy-path QA, create one explicit temporary directory, run `run_kr_theme_ingest.py` with the new fixture, run projection twice, verify DB mode `600`, classification count `1`, Opportunity JSONL line count `1`, and remove only that temporary directory.

- [x] **Step 5: Commit Task 3**

```bash
git add trading_agent/kr_theme_projection_manifest.py run_kr_theme_projection.py tests/test_kr_theme_projection_manifest.py tests/test_kr_theme_projection_cli.py examples/kr_theme_projection
git commit -m "feat: publish local KR theme opportunities"
```

### Task 4: Document, Verify, Review, And Publish

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-15-kr-theme-keyword-opportunity.md`
- Create: `docs/checkpoints/2026-07-15-kr-theme-keyword-opportunity-ko.md`

- [x] **Step 1: Update documentation honestly**

Document implemented local keyword classification, stored-evidence theme/leader projection and immutable KR Opportunity output. Explicitly state that production collectors/extractors, LLM comparison, live market freshness, KR risk gates, TradeSignal, shadow fills, domestic account APIs and orders remain absent.

- [x] **Step 2: Run complete verification**

Run from the final feature branch:

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
git diff --check
```

Repeat CLI help, invalid input, ingest + projection happy path, and restart from the final merged `main`. Record exact results and confirm external network, LLM and trading mutations were zero.

- [x] **Step 3: Review privacy and lineage**

Review `main...HEAD` specifically for raw payload/text repr, exception/report leakage, exact cycle/cohort joins, source coverage, deterministic IDs, append-only conflicts, and theme mixing. Fix every blocking or important finding with a failing regression test first.

- [ ] **Step 4: Commit, fast-forward main, and push**

```bash
git add README.md docs/superpowers/plans/2026-07-15-kr-theme-keyword-opportunity.md docs/checkpoints/2026-07-15-kr-theme-keyword-opportunity-ko.md
git commit -m "docs: record KR theme opportunity milestone"
git -C /Users/goyunseo/work/trading-recommendation-agent pull --ff-only origin main
git -C /Users/goyunseo/work/trading-recommendation-agent merge --ff-only codex/kr-theme-keyword-opportunity
git -C /Users/goyunseo/work/trading-recommendation-agent push origin main
```

## Deferred To The Next KR Units

- Production read-only news, DART and KIS domestic collectors plus source-specific text extractors.
- Configured LLM classification, repeated-run stability, keyword comparison, and human audit sample.
- Current KR quotes, VI, price-limit, warning, halt, auction and freshness gates.
- KR day shadow TradeSignal, conservative fill lifecycle and outcome evaluation.
- Any domestic account, balance, position, order, or execution endpoint.
