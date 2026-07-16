# Research Source Lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add immutable research-source provenance and source-bound hypothesis cards to the global experiment ledger without changing trading authority.

**Architecture:** Keep existing hypothesis/version/trial/lifecycle payloads intact. Add an append-only research catalog and a card that references an existing hypothesis. Migrate exact v1 SQLite ledgers by adding tables only, then provide a local JSON preregistration CLI.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, pytest, Ruff, basedpyright.

---

### Task 1: Research Models And Canonical Keys

**Files:**
- Modify: `trading_agent/experiment_ledger_models.py`
- Modify: `trading_agent/experiment_ledger_keys.py`
- Modify: `tests/test_experiment_ledger_models.py`

- [x] **Step 1: Write the failing contract test**

```python
source = ResearchSource(
    source_id="academic-momentum-1993",
    source_kind=ResearchSourceKind.ACADEMIC_PAPER,
    title="Returns to Buying Winners and Selling Losers",
    source_url="https://doi.org/10.1111/j.1540-6261.1993.tb04702.x",
    published_on=dt.date(1993, 2, 1),
    claim="Intermediate-horizon relative strength motivates a momentum trial.",
    limitations="It is not current-market or net-cost evidence for this project.",
    retrieved_at=LEDGER_RECORDED_AT,
    ledger_recorded_at=LEDGER_RECORDED_AT,
)
card = ResearchHypothesisCard(
    hypothesis=_hypothesis(),
    research_source_keys=(str(research_source_key(source)),),
    economic_mechanism="Underreaction may leave return continuation.",
    counterfactual_baseline="Matched eligible-universe forward return after the same cost model.",
)
assert len(research_hypothesis_card_key(card)) == 64
```

- [x] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_experiment_ledger_models.py -k research_source`

Expected: FAIL because the new imports do not exist.

- [x] **Step 3: Add minimal models and keys**

```python
class ResearchSourceKind(StrEnum):
    ACADEMIC_PAPER = "academic_paper"
    OFFICIAL_MARKET_RULE = "official_market_rule"
    OFFICIAL_PROVIDER_DOCUMENT = "official_provider_document"
    INTERNAL_OBSERVATION = "internal_observation"

class ResearchSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    source_id: str
    source_kind: ResearchSourceKind
    title: str
    source_url: str
    published_on: dt.date
    claim: str
    limitations: str
    retrieved_at: dt.datetime
    ledger_recorded_at: dt.datetime
```

Add `ResearchHypothesisCard`, `ResearchSourceKey`, and `ResearchHypothesisCardKey`. Validate HTTPS URLs without userinfo or fragments, canonical source-key tuples, non-empty mechanism/baseline text, and aware timestamps.

- [x] **Step 4: Verify GREEN**

Run: `uv run pytest -q tests/test_experiment_ledger_models.py -k research_source`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add trading_agent/experiment_ledger_models.py trading_agent/experiment_ledger_keys.py tests/test_experiment_ledger_models.py
git commit -m "feat: add research source contracts"
```

### Task 2: Schema v2 And Append-Only Store

**Files:**
- Modify: `trading_agent/experiment_ledger_schema.py`
- Modify: `trading_agent/experiment_ledger_store.py`
- Modify: `tests/test_experiment_ledger_store.py`

- [x] **Step 1: Write failing store/migration tests**

```python
with store.writer() as writer:
    assert writer.register_research_source(_research_source()) is True
    assert writer.register_research_hypothesis(_research_card()) is True

reader = ExperimentLedgerReader(database)
assert reader.research_sources()[0].source == _research_source()
assert reader.research_hypothesis_cards()[0].card == _research_card()
```

Also initialize a v1 database, retain a copy of its existing hypothesis row, open it through the v2 Writer, and assert the copied row is byte-for-byte unchanged after migration.

- [x] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_experiment_ledger_store.py -k 'research or migration'`

Expected: FAIL because the new Reader/Writer APIs do not exist.

- [x] **Step 3: Implement schema and migration**

```sql
CREATE TABLE research_sources (
  source_key TEXT PRIMARY KEY,
  source_id TEXT NOT NULL UNIQUE,
  source_kind TEXT NOT NULL,
  source_url TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE research_hypothesis_cards (
  card_key TEXT PRIMARY KEY,
  hypothesis_id TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(hypothesis_id) REFERENCES hypotheses(hypothesis_id)
);
```

Create update/delete triggers for both tables. On v1, verify the v1 schema, execute only v2 DDL, then update `user_version` inside the writer transaction. The Writer verifies every referenced source is exact and recorded no later than the embedded hypothesis before inserting the card.

- [x] **Step 4: Verify GREEN**

Run: `uv run pytest -q tests/test_experiment_ledger_store.py -k 'research or migration or append_only or reader_connection'`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add trading_agent/experiment_ledger_schema.py trading_agent/experiment_ledger_store.py tests/test_experiment_ledger_store.py
git commit -m "feat: store research source lineage"
```

### Task 3: Local Preregistration CLI

**Files:**
- Create: `trading_agent/research_hypothesis_registration.py`
- Create: `run_research_hypothesis_register.py`
- Create: `examples/research/us-swing-new-high-rvol-v1.json`
- Create: `tests/test_research_hypothesis_registration.py`
- Create: `tests/test_research_hypothesis_register_cli.py`

- [x] **Step 1: Write failing service and CLI tests**

```python
result = register_research_hypothesis_manifest(
    manifest_path=fixture_manifest,
    ledger=ExperimentLedgerStore(database),
)
assert (result.sources_created, result.cards_created) == (2, 1)
assert register_research_hypothesis_manifest(...).sources_created == 0
```

The CLI test must prove `--help`, malformed input without database creation, fixture creation/replay, mode-600 output, and no provider/credentials/broker/Paper imports.

- [x] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_research_hypothesis_registration.py tests/test_research_hypothesis_register_cli.py`

Expected: FAIL because the service and script are absent.

- [x] **Step 3: Implement bounded manifest registration**

Manifest fields are `research_sources`, `hypothesis`, `research_source_ids`, `economic_mechanism`, and `counterfactual_baseline`. Resolve IDs to source keys, construct a card, register all sources/card in one Writer transaction, and write a report containing only created/reused counts and `external mutation: 0`.

- [x] **Step 4: Verify GREEN and manual CLI QA**

Run: `uv run pytest -q tests/test_research_hypothesis_registration.py tests/test_research_hypothesis_register_cli.py`

Run: `uv run python run_research_hypothesis_register.py --help`

Run: `uv run python run_research_hypothesis_register.py --manifest /tmp/missing.json --database /tmp/research-ledger.sqlite3 --output-dir /tmp/research-report`

Run: `uv run python run_research_hypothesis_register.py --manifest examples/research/us-swing-new-high-rvol-v1.json --database /tmp/research-ledger.sqlite3 --output-dir /tmp/research-report`

Expected: help and fixture succeed; missing manifest fails without a database.

- [x] **Step 5: Commit**

```bash
git add trading_agent/research_hypothesis_registration.py run_research_hypothesis_register.py examples/research/us-swing-new-high-rvol-v1.json tests/test_research_hypothesis_registration.py tests/test_research_hypothesis_register_cli.py
git commit -m "feat: add research hypothesis registration cli"
```

### Task 4: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Create: `docs/checkpoints/2026-07-16-research-source-lineage-ko.md`

- [x] **Step 1: Document authority boundary and fixture command**

State that this registration creates neither strategy version, trial, Paper order, nor automatic promotion.

- [x] **Step 2: Run quality gates**

Run: `uv run pytest -q`

Run: `uv run ruff check .`

Run: `uv run basedpyright`

Expected: all tests pass and static checks report zero findings.

- [x] **Step 3: Commit**

```bash
git add README.md CODEX_START_HERE.md docs/checkpoints/2026-07-16-research-source-lineage-ko.md docs/superpowers/specs/2026-07-16-research-source-lineage-design.md docs/superpowers/plans/2026-07-16-research-source-lineage.md
git commit -m "docs: record research source lineage checkpoint"
```
