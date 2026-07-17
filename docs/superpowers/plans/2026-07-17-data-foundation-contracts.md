# Data Foundation Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a network-free, execution-free Milestone 2 contract layer that identifies data sources and instruments, records entitlement and quality limits, validates canonical event causality metadata, and deterministically closes strategy lanes as `ready`, `research_only`, or `blocked_by_data`.

**Architecture:** Introduce frozen Pydantic contracts alongside the existing multi-market identity models without moving existing collectors or ledgers. A manifest snapshot binds one strategy lane to explicit primary/fallback sources, current immutable capability health, entitlement windows, point-in-time instrument aliases, corporate actions, and canonical event envelopes. A pure evaluator selects only declared sources in declared order. A local-only CLI validates a manifest and writes a mode-600 aggregate report; it imports no provider, credential, broker, Paper, or order modules.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, Ruff, basedpyright, argparse, existing `write_private_report` helper.

---

### Task 1: Add Point-In-Time Security Master Contracts

**Files:**
- Create: `tests/test_security_master_models.py`
- Create: `trading_agent/security_master_models.py`

- [x] **Step 1: Write failing contract tests**

Cover these observable behaviors:

- `InstrumentId` is frozen, extra-forbid, has a stable opaque `value`, market domain, asset class, venue, ISO currency, IANA timezone, and half-open validity interval.
- A symbol is an `InstrumentAlias`, never the instrument identity. Alias keys are namespace/type/value plus a half-open effective interval.
- `resolve_instrument_alias` resolves exactly one alias at an aware `as_of`; zero or overlapping matches raise a fixed `InstrumentAliasResolutionError`.
- A split requires positive finite numerator/denominator and forbids cash/successor fields.
- A cash dividend requires positive finite cash and matching currency.
- A symbol change has no ratio/cash/successor payload because alias intervals preserve the change.
- A merger or spin-off requires a distinct successor instrument; delisting forbids one.
- All timestamps are timezone-aware, effective times cannot precede announcement, IDs are canonical, tuples are not involved in silent normalization, and unknown fields fail validation.

Run and confirm collection fails because the production module does not exist:

```bash
uv run pytest -q tests/test_security_master_models.py
```

- [x] **Step 2: Implement the minimal security master module**

Create these public contracts:

```text
DataMarketDomain
AssetClass
InstrumentAliasType
CorporateActionType
InstrumentId
InstrumentAlias
CorporateAction
InstrumentAliasResolutionError
resolve_instrument_alias
```

Use point-in-time half-open intervals (`start <= as_of < end`). Do not add persistence, provider adapters, symbol heuristics, current-company backfills, option symbology, or futures roll logic in this task.

- [x] **Step 3: Verify and commit**

```bash
uv run pytest -q tests/test_security_master_models.py
uv run ruff check trading_agent/security_master_models.py tests/test_security_master_models.py
uv run basedpyright trading_agent/security_master_models.py tests/test_security_master_models.py
git diff --check
git add trading_agent/security_master_models.py tests/test_security_master_models.py docs/superpowers/plans/2026-07-17-data-foundation-contracts.md
git commit -m "feat: add point-in-time security master contracts"
```

### Task 2: Add Data Capability, Entitlement, And Strategy Gate Contracts

**Files:**
- Create: `tests/test_data_capability_models.py`
- Create: `tests/test_strategy_data_gate.py`
- Create: `trading_agent/data_capability_models.py`
- Create: `trading_agent/strategy_data_gate.py`

- [ ] **Step 1: Write failing model tests**

Cover:

- `DataSourceId` has canonical provider/feed identity and stable `canonical_id`.
- `DataEntitlement` declares exact market domains, event types, permitted uses, effective window, real-time/historical authority, redistribution, retention, and deletion/correction policy without credentials.
- `DataCapability` declares source class, universe, delivery modes, timestamp semantics, historical depth, expected latency, rate/connection limits, freshness/completeness SLO, assessed health, latest receipt, and observed completeness.
- all sets represented as tuples are sorted and duplicate-free except fallback source order, which is an explicit priority list.
- complete/degraded capability snapshots require current observed evidence; incomplete/failed snapshots cannot masquerade as current.
- `StrategyDataRequirement` binds one exact `StrategyLaneRef` to one primary source, ordered explicit fallbacks, required use/domain/event/timestamp semantics, maximum age, minimum completeness, optional historical depth, degraded-data policy, and failure mode.

Run and confirm expected import/collection failure:

```bash
uv run pytest -q tests/test_data_capability_models.py
```

- [ ] **Step 2: Implement frozen capability contracts**

Create enums and models for the design fields, including:

```text
DataSourceId
DataSourceClass
DataDeliveryMode
TimestampSemantic
DataUse
RedistributionPolicy
DataCorrectionPolicy
DataHealthState
DataRequirementFailureMode
DataEntitlement
DataRateLimits
DataCapability
StrategyDataRequirement
```

Reuse `DataMarketDomain` from the security master module and `StrategyLaneRef` from the existing research identity module. Keep source metadata free of secrets and endpoint URLs.

- [ ] **Step 3: Write failing pure-gate tests**

Prove:

- a healthy, fresh, complete primary source with active matching entitlement produces `ready`;
- undeclared providers are never selected;
- fallback is selected only when the primary fails and the fallback appears in the requirement, with `fallback_used=true` evidence;
- missing/expired entitlement, stale latest receipt, insufficient completeness, unsupported timestamp semantics, insufficient historical depth, or failed health all produce fixed reason codes;
- degraded data is rejected unless the requirement explicitly permits it;
- an unresolved hard requirement makes the lane `blocked_by_data`;
- only soft unresolved requirements make the lane `research_only`;
- hard blocking dominates soft degradation;
- requirements must be canonical, unique, and for one lane.

```bash
uv run pytest -q tests/test_strategy_data_gate.py
```

- [ ] **Step 4: Implement deterministic evaluation and commit**

Create:

```text
DataRequirementStatus
StrategyDataStatus
DataRequirementEvaluation
StrategyDataDecision
evaluate_strategy_data
```

The evaluator is pure and receives `evaluated_at`; it performs no clock, file, credential, network, or broker access. It tries primary then declared fallbacks in order and returns sanitized fixed reason codes.

```bash
uv run pytest -q tests/test_data_capability_models.py tests/test_strategy_data_gate.py
uv run ruff check trading_agent/data_capability_models.py trading_agent/strategy_data_gate.py tests/test_data_capability_models.py tests/test_strategy_data_gate.py
uv run basedpyright trading_agent/data_capability_models.py trading_agent/strategy_data_gate.py tests/test_data_capability_models.py tests/test_strategy_data_gate.py
git diff --check
git add trading_agent/data_capability_models.py trading_agent/strategy_data_gate.py tests/test_data_capability_models.py tests/test_strategy_data_gate.py docs/superpowers/plans/2026-07-17-data-foundation-contracts.md
git commit -m "feat: gate strategies on declared data capability"
```

### Task 3: Add Canonical Event And Cross-Contract Manifest Validation

**Files:**
- Create: `tests/test_canonical_event_models.py`
- Create: `tests/test_data_foundation_manifest.py`
- Create: `trading_agent/canonical_event_models.py`
- Create: `trading_agent/data_foundation_manifest.py`
- Create: `examples/data/us-orb-data-foundation-v1.json`

- [ ] **Step 1: Write failing canonical event tests**

Cover:

- `CanonicalEventEnvelope` contains every timestamp field from the design, keeps inapplicable times as explicit `null`, and requires aware values when present.
- `received_at` and `normalized_at` are mandatory and `normalized_at >= received_at`.
- original events forbid `correction_of`; correction/tombstone events require it and cannot refer to themselves.
- entity refs, quality flags, and IDs are canonical sorted unique tuples.
- raw receipt references are opaque canonical IDs, not local paths; content hashes are lowercase SHA-256.
- effective intervals are half-open and valid; schema is version 1 and unknown fields fail.

```bash
uv run pytest -q tests/test_canonical_event_models.py
```

- [ ] **Step 2: Implement canonical envelopes**

Create:

```text
CanonicalEntityType
CanonicalEntityRef
CanonicalEventOperation
CanonicalEventEnvelope
```

Do not include raw payload content, strategy features, recommendation logic, or inferred replacement timestamps.

- [ ] **Step 3: Write failing manifest cross-reference tests**

`DataFoundationManifest` must enforce:

- one canonical manifest ID, registered/evaluated times, and one strategy lane;
- exactly one capability snapshot and one entitlement per declared source;
- requirements reference only declared source IDs and exactly the manifest lane;
- instruments, aliases, corporate actions, and events are sorted/unique;
- aliases/actions reference declared instruments;
- event source IDs reference declared capabilities and instrument entity refs reference declared instruments;
- event types are covered by the event source capability;
- no silent source, symbol, or entity substitution;
- the example fixture validates and evaluates as `ready` without provider or broker imports.

```bash
uv run pytest -q tests/test_data_foundation_manifest.py
```

- [ ] **Step 4: Implement manifest loading and commit**

Provide `InvalidDataFoundationManifestError`, `DataFoundationManifest`, `load_data_foundation_manifest`, and a method or pure helper that invokes `evaluate_strategy_data`. Resolve the manifest path strictly as a regular file and parse it with Pydantic. The example must use fixture identities and must not claim a live entitlement.

```bash
uv run pytest -q tests/test_canonical_event_models.py tests/test_data_foundation_manifest.py
uv run ruff check trading_agent/canonical_event_models.py trading_agent/data_foundation_manifest.py tests/test_canonical_event_models.py tests/test_data_foundation_manifest.py
uv run basedpyright trading_agent/canonical_event_models.py trading_agent/data_foundation_manifest.py tests/test_canonical_event_models.py tests/test_data_foundation_manifest.py
git diff --check
git add trading_agent/canonical_event_models.py trading_agent/data_foundation_manifest.py tests/test_canonical_event_models.py tests/test_data_foundation_manifest.py examples/data/us-orb-data-foundation-v1.json docs/superpowers/plans/2026-07-17-data-foundation-contracts.md
git commit -m "feat: add canonical data foundation manifest"
```

### Task 4: Add A Local-Only Contract Check CLI

**Files:**
- Create: `tests/test_data_foundation_check_cli.py`
- Create: `run_data_foundation_check.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing CLI tests**

Cover:

- `--help` exits 0 and exposes only `--manifest` and `--output-dir`;
- missing or invalid input exits 1, writes a sanitized mode-600 `blocked` report, and creates no database;
- valid fixture exits 0 with `ready`, aggregate counts, no raw event content or local paths, `network access: 0`, and `broker mutation: 0`;
- a valid stale fixture exits 2 with `blocked_by_data` rather than being treated as invalid;
- source files contain no Alpaca/KIS/LS/provider HTTP, credential, Paper, order, or broker imports.

```bash
uv run pytest -q tests/test_data_foundation_check_cli.py
```

- [ ] **Step 2: Implement the CLI and type-check entry point**

Use `write_private_report`. Catch only validation, manifest, OS, and value errors. Do not print exception text. Add `run_data_foundation_check.py` to basedpyright includes.

- [ ] **Step 3: Focused verification and commit**

```bash
uv run pytest -q tests/test_security_master_models.py tests/test_data_capability_models.py tests/test_strategy_data_gate.py tests/test_canonical_event_models.py tests/test_data_foundation_manifest.py tests/test_data_foundation_check_cli.py
uv run ruff check run_data_foundation_check.py trading_agent/security_master_models.py trading_agent/data_capability_models.py trading_agent/strategy_data_gate.py trading_agent/canonical_event_models.py trading_agent/data_foundation_manifest.py tests/test_security_master_models.py tests/test_data_capability_models.py tests/test_strategy_data_gate.py tests/test_canonical_event_models.py tests/test_data_foundation_manifest.py tests/test_data_foundation_check_cli.py
uv run basedpyright run_data_foundation_check.py trading_agent/security_master_models.py trading_agent/data_capability_models.py trading_agent/strategy_data_gate.py trading_agent/canonical_event_models.py trading_agent/data_foundation_manifest.py
git diff --check
git add run_data_foundation_check.py pyproject.toml tests/test_data_foundation_check_cli.py docs/superpowers/plans/2026-07-17-data-foundation-contracts.md
git commit -m "feat: add local data foundation check"
```

### Task 5: Document, Fully Verify, And Publish Milestone 2

**Files:**
- Create: `docs/checkpoints/2026-07-17-data-foundation-contracts-ko.md`
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/superpowers/plans/2026-07-17-data-foundation-contracts.md`

- [ ] **Step 1: Run manual CLI QA**

```bash
./run_data_foundation_check.py --help
./run_data_foundation_check.py --manifest /tmp/missing-data-foundation.json --output-dir /tmp/data-foundation-invalid
./run_data_foundation_check.py --manifest examples/data/us-orb-data-foundation-v1.json --output-dir /tmp/data-foundation-ready
```

Expected exits are 0, 1, and 0. Reports are mode 600 and contain no credentials, endpoint URLs, raw payloads, account IDs, order IDs, or private input/output paths.

- [ ] **Step 2: Run complete verification one heavy command at a time**

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
git diff --check
```

- [ ] **Step 3: Update durable documentation with exact evidence**

Record models, fail-closed states, explicit fallback behavior, local-only CLI, test count, static-check results, manual exit codes, and external mutation 0. Update README current implementation/limitations without claiming a live source registry, raw lake, provider entitlement, strategy profitability, or Paper POST. Add the completed checkpoint and next Milestone 3 boundary to `CODEX_START_HERE.md` without reordering existing live-session priorities.

- [ ] **Step 4: Commit, check remote freshness, and push main**

```bash
git add README.md CODEX_START_HERE.md docs/checkpoints/2026-07-17-data-foundation-contracts-ko.md docs/superpowers/plans/2026-07-17-data-foundation-contracts.md
git commit -m "docs: record data foundation contracts"
git fetch origin main
git rev-list --left-right --count main...origin/main
git push origin main
git status --short --branch
git rev-parse HEAD origin/main
```

Expected: no unexpected remote divergence, clean worktree, and matching local/remote SHA.
