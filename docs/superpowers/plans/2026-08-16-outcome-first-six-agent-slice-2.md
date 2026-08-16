# Outcome-First 6-Agent Slice 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development task-by-task.

**Goal:** Make Opportunity and Market Context decisions terminate only through resolved existing artifacts, and render their real rows in Hermes without adding a scheduler, database, or provider.

**Architecture:** Keep the Slice 1 action context and cycle journal. Add two bounded primary-family action clients: Opportunity resolves the selected snapshot/candidate and, when an already-registered ledger card matches snapshot provenance, its existing hypothesis card key; Context resolves the current or research-archive context payload and compares its artifact identity with prior terminal results. Results reference the source payload hash (and optional existing card key). Hermes resolves those references through evidence already stored in the cycle journal and renders family-specific rows. No model prose becomes an artifact.

**Tech Stack:** Python 3.12, Pydantic v2, existing Opportunity/MarketContext models, existing ExperimentLedgerReader, SQLite JSON cycle journal, existing Hermes delivery store, pytest, Ruff, basedpyright

---

## File map

- `trading_agent/research_agent_source_common.py`: deterministic compact candidate subject identity.
- `trading_agent/research_agent_source_adapters_primary.py`, `trading_agent/research_agent_source_archives.py`: expose snapshot and candidate subjects.
- `trading_agent/research_agent_primary_actions.py`: parse authority payloads, resolve existing hypothesis cards, produce completed/no-action results.
- `trading_agent/research_agent_actions.py`: dispatch only the two now-implemented primary family actions.
- `trading_agent/research_agent_cycle_store.py`: read all stored evidence for projection; no schema change.
- `trading_agent/research_agent_hermes.py`: resolve Opportunity/Context artifact hashes and render typed rows.
- `trading_agent/research_agent_service_runtime.py`: wire existing source paths, experiment reader, cycle store, and Hermes projection.
- `tests/test_research_agent_primary_actions.py`: primary action behavior and artifact resolution.
- `tests/test_research_agent_sources.py`: candidate subject projection.
- `tests/test_research_agent_actions.py`: dispatch and unimplemented-action rejection.
- `tests/test_research_agent_hermes.py`: enriched family rendering and replay.
- `tests/test_research_agent_service_runtime.py`: production wiring and zero broker mutation.

## Task 1: Expose resolvable Opportunity subjects

**Files:**
- Modify: `tests/test_research_agent_sources.py`
- Modify: `trading_agent/research_agent_source_common.py`
- Modify: `trading_agent/research_agent_source_adapters_primary.py`
- Modify: `trading_agent/research_agent_source_archives.py`

- [ ] **Step 1: Write failing current/archive candidate subject tests**

Extend the seeded Opportunity assertions so `subject_refs` contain the source key plus one deterministic candidate ref. Add an archive assertion using the prior-date test. The ref must not contain a raw symbol and must remain within the existing safe-reference contract.

```python
candidate_ref = opportunity_candidate_subject_ref(opportunity.source_key, 1)
assert opportunity.subject_refs == tuple(sorted((opportunity.source_key, candidate_ref)))
```

- [ ] **Step 2: Confirm RED**

```bash
uv run pytest -q tests/test_research_agent_sources.py -k 'projection_routes or prior_date'
```

Expected: FAIL because sources currently expose only the envelope source key.

- [ ] **Step 3: Add the compact identity helper and use it in both projections**

```python
def opportunity_candidate_subject_ref(source_key: str, rank: int) -> str:
    digest = hashlib.sha256(source_key.encode()).hexdigest()[:16]
    return f"opportunity_candidate.{digest}.{rank}"
```

Current and archived Opportunity evidence set sorted refs for the source key and every candidate rank. Do not expose raw symbols in identities; values remain in the bounded payload.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest -q tests/test_research_agent_sources.py
git add trading_agent/research_agent_source_common.py trading_agent/research_agent_source_adapters_primary.py trading_agent/research_agent_source_archives.py tests/test_research_agent_sources.py
git commit -m "feat(agent): expose opportunity candidate subjects"
```

## Task 2: Resolve Opportunity and Context authority artifacts

**Files:**
- Create: `trading_agent/research_agent_primary_actions.py`
- Create: `tests/test_research_agent_primary_actions.py`
- Modify: `trading_agent/research_agent_actions.py`
- Modify: `tests/test_research_agent_actions.py`

- [ ] **Step 1: Write failing action-client tests**

Cover these boundaries with real model payloads:

1. `INVESTIGATE_CANDIDATE` selects one candidate subject and returns `COMPLETED`, the evidence payload SHA as artifact, and a deterministic summary containing symbol, rank, features, coverage source, and `investigation_reason=ranked_candidate`.
2. `PROPOSE_HYPOTHESIS` with a fake resolver returning an existing 64-hex card key returns both the payload SHA and card key; with no matching card it raises `required_evidence_unavailable` and never turns decision text into a card.
3. `PUBLISH_CONTEXT` returns a current `MarketContextSnapshot` artifact with regime/features.
4. A repeated context payload whose SHA exists in prior completed Context results returns `NO_ACTION`, reason `context_unchanged`, continuation, and no artifact.
5. The archived-day Context envelope is accepted only with its exact typed keys and renders session/count/hash facts; malformed or blocked capability payloads raise `authority_artifact_unresolved`.

Use `ResearchAgentActionContext` and decisions whose subjects came from the evidence. Assert all authority flags remain false.

- [ ] **Step 2: Confirm RED**

```bash
uv run pytest -q tests/test_research_agent_primary_actions.py tests/test_research_agent_actions.py
```

Expected: FAIL because the clients and dispatch do not exist.

- [ ] **Step 3: Implement parsers and primary clients**

`research_agent_primary_actions.py` defines:

```python
class OpportunityHypothesisResolver(Protocol):
    def matching_card_key(self, snapshot: OpportunitySnapshot) -> str | None: ...

@dataclass(frozen=True, slots=True)
class OpportunityResearchActionExecutor:
    hypothesis_resolver: OpportunityHypothesisResolver
    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1: ...

@dataclass(frozen=True, slots=True)
class MarketContextResearchActionExecutor:
    prior_results: Callable[[], tuple[ResearchAgentResultV1, ...]]
    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1: ...
```

Unwrap research archives only when `research_only is True` and `trading_authority is False`. Parse Opportunity with `OpportunitySnapshot`. Parse Context as `MarketContextSnapshot` or the exact archived-day context model. Artifact refs are sorted existing identities only. Result summaries are derived from parsed fields, never from `decision.summary`.

- [ ] **Step 4: Implement existing-ledger hypothesis resolution**

Add `ExperimentLedgerOpportunityHypothesisResolver(ExperimentLedgerReader)`:

- map snapshot `EvidenceRef.canonical_id` and coverage source IDs to existing `ResearchSource.source_id`;
- map those stored source keys to existing cards;
- return exactly one matching card key;
- return `None` for zero matches;
- raise `authority_artifact_unresolved` for ambiguity or invalid ledger contents.

It is read-only and never registers a source/card.

- [ ] **Step 5: Dispatch only implemented family actions**

Extend `ResearchAgentActionConfig` with optional `opportunity` and `market_context` clients. Dispatch:

```python
case INVESTIGATE_CANDIDATE | PROPOSE_HYPOTHESIS if cycle.agent_family_id == "opportunity_manager":
    return configured_opportunity.execute(context)
case PUBLISH_CONTEXT if cycle.agent_family_id == "market_context":
    return configured_context.execute(context)
```

Missing clients fail `action_not_configured`; wrong-family or all other prose actions remain `prose_only_result`.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest -q tests/test_research_agent_primary_actions.py tests/test_research_agent_actions.py tests/test_research_agent_cycle_models.py
git add trading_agent/research_agent_primary_actions.py trading_agent/research_agent_actions.py tests/test_research_agent_primary_actions.py tests/test_research_agent_actions.py
git commit -m "feat(agent): execute opportunity and context artifacts"
```

## Task 3: Render resolved family artifacts in Hermes

**Files:**
- Modify: `trading_agent/research_agent_cycle_store.py`
- Modify: `trading_agent/research_agent_hermes.py`
- Modify: `trading_agent/research_agent_service_runtime.py`
- Modify: `tests/test_research_agent_cycle_store.py`
- Modify: `tests/test_research_agent_hermes.py`
- Modify: `tests/test_research_agent_service_runtime.py`

- [ ] **Step 1: Write failing evidence-reader and renderer tests**

Add a cycle-store test proving `all_evidence()` returns canonical insertion order. Add Hermes tests with completed Opportunity and Context results whose artifact ref equals a real evidence payload SHA.

Assert Opportunity text contains candidate symbol/rank, feature values, coverage source, investigation reason, and safe artifact identity. Assert Context text contains regime/features or archived session counts. Assert replay remains exactly once and `order authority: false` is present.

- [ ] **Step 2: Confirm RED**

```bash
uv run pytest -q tests/test_research_agent_cycle_store.py tests/test_research_agent_hermes.py
```

- [ ] **Step 3: Add read-only evidence resolution and family renderer**

`ResearchAgentCycleStore.all_evidence()` reads every evidence row and uses the existing codec. Change `project_research_agent_results` to accept evidence and build a payload-SHA index. For Opportunity/Context completed results, resolve an artifact ref to evidence of the same family, re-validate the typed payload, and render bounded rows. Legacy/non-primary results retain the existing generic renderer. Redaction and 4096-character bound remain mandatory.

- [ ] **Step 4: Wire the existing service surfaces**

Create the cycle store before action clients. Wire:

```python
store = ResearchAgentCycleStore(config.cycle_database)
opportunity = OpportunityResearchActionExecutor(
    ExperimentLedgerOpportunityHypothesisResolver(ExperimentLedgerReader(config.source_paths.experiment_ledger))
)
context = MarketContextResearchActionExecutor(store.results)
```

Pass `runtime.store.all_evidence()` to Hermes projection. Do not add a store, scheduler, provider, order path, or delivery kind.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/test_research_agent_cycle_store.py tests/test_research_agent_hermes.py tests/test_research_agent_service_runtime.py
git add trading_agent/research_agent_cycle_store.py trading_agent/research_agent_hermes.py trading_agent/research_agent_service_runtime.py tests/test_research_agent_cycle_store.py tests/test_research_agent_hermes.py tests/test_research_agent_service_runtime.py
git commit -m "feat(agent): render primary artifacts in Hermes"
```

## Task 4: Slice 2 acceptance on real read-only artifacts

- [ ] **Step 1: Run all Slice 1 and Slice 2 focused tests**

```bash
uv run pytest -q tests/test_research_agent_cycle_models.py tests/test_research_agent_cycle_store.py tests/test_research_agent_sources.py tests/test_research_agent_decision.py tests/test_research_agent_actions.py tests/test_research_agent_primary_actions.py tests/test_research_agent_runtime.py tests/test_research_agent_service_runtime.py tests/test_research_agent_hermes.py
```

- [ ] **Step 2: Run Ruff and basedpyright for every changed Python file**

```bash
uv run ruff check trading_agent/research_agent_source_common.py trading_agent/research_agent_source_adapters_primary.py trading_agent/research_agent_source_archives.py trading_agent/research_agent_primary_actions.py trading_agent/research_agent_actions.py trading_agent/research_agent_cycle_store.py trading_agent/research_agent_hermes.py trading_agent/research_agent_service_runtime.py tests/test_research_agent_sources.py tests/test_research_agent_primary_actions.py tests/test_research_agent_actions.py tests/test_research_agent_cycle_store.py tests/test_research_agent_hermes.py tests/test_research_agent_service_runtime.py
uv run basedpyright trading_agent/research_agent_source_common.py trading_agent/research_agent_source_adapters_primary.py trading_agent/research_agent_source_archives.py trading_agent/research_agent_primary_actions.py trading_agent/research_agent_actions.py trading_agent/research_agent_cycle_store.py trading_agent/research_agent_hermes.py trading_agent/research_agent_service_runtime.py
```

- [ ] **Step 3: Manual CLI help and bad input**

```bash
uv run python run_research_agent_runtime.py --help
uv run python run_research_agent_runtime.py tick --config /tmp/nonexistent-outcome-first-slice2.json
```

Expected: 0 then 2, with no secret/header/account output.

- [ ] **Step 4: Observe real archived Opportunity and Context artifacts read-only**

Use the current private config only to collect the real production source batch. Construct deterministic `INVESTIGATE_CANDIDATE` and `PUBLISH_CONTEXT` decisions in memory, run the two primary action clients without persisting, and print only safe summary text, artifact identities, statuses, and `broker_mutation=0`. Then project those in-memory results/evidence into a temporary Hermes store and read the rendered text. Never print raw payloads or credentials.

Expected: actual Opportunity candidate rows and source/reason; actual Context or archived-context fields; artifact refs resolve; model calls 0; broker mutation 0.

- [ ] **Step 5: Final acceptance evidence**

```bash
git status --short
git log --oneline 019acc3..HEAD
git diff --check 019acc3..HEAD
```

Expected: clean worktree, atomic Slice 2 commits, no new database/provider/scheduler/coordinator, and all acceptance evidence recorded before opening Slice 3.
