# Autonomous Generated Python Strategy Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Researcher persist its unrestricted Python strategy source, execute that source under the real macOS sandbox, evaluate it through the existing conservative walk-forward/Reviewer core, and feed terminal evidence into the next bounded autonomous cycle.

**Architecture:** Preserve the existing `StrategyMode` manifest and trial path unchanged. Add a generated-strategy vertical with immutable source/runtime identity, a framed one-bar-at-a-time sandbox protocol, a generated historical manifest/trial path, and one orchestration CLI; join the existing experiment ledger and Reviewer only after host-side signal validation.

**Tech Stack:** Python 3.12, Pydantic v2, macOS `/usr/bin/sandbox-exec`, stdlib `subprocess`/`selectors`/`resource`, SQLite experiment ledger, Typer-free existing `argparse` CLI style, pytest, Ruff, basedpyright.

---

## File structure

New focused modules:

- `trading_agent/generated_strategy_artifact.py`: runtime-bound immutable generated source model/store.
- `trading_agent/generated_strategy_runtime.py`: Python executable/package fingerprint and sandbox profile construction.
- `trading_agent/generated_strategy_protocol.py`: typed framed JSON request/response boundary.
- `trading_agent/generated_strategy_runner.py`: child-side generated module loader and single-bar dispatcher.
- `trading_agent/generated_strategy_sandbox.py`: host process lifecycle, resource limits, frame exchange and signal adapter.
- `trading_agent/generated_intraday_research_models.py`: generated manifest, selection and walk-forward result contracts.
- `trading_agent/generated_intraday_registration.py`: source/card/artifact-bound strategy version registration.
- `trading_agent/generated_intraday_trial.py`: generated historical trial ledger chain and artifact publication.
- `trading_agent/generated_intraday_loop.py`: data gate, heavy lease, generated trial and existing Reviewer orchestration.
- `trading_agent/autonomous_research_cycle.py`: one bounded propose/artifactize/test/review/feedback application service.
- `run_autonomous_research_cycle.py`: public one-shot CLI.

Existing modules changed only at shared seams:

- `trading_agent/researcher_pipeline.py`: publish the generated artifact before hypothesis registration.
- `trading_agent/researcher_llm.py`: describe the machine-consumed entrypoint contract.
- `trading_agent/critic_agent.py`: remove AST lookahead rejection because future bars are unavailable by protocol.
- `trading_agent/intraday_research_artifacts.py`: accept the generated result schema alongside existing v1/v2 results.
- `trading_agent/intraday_research_reviewer.py`: consume the shared metric surface without changing decisions.
- `trading_agent/intraday_research_loop.py`: re-export the existing heavy empirical lease from a focused module.
- `run_researcher_propose.py`: require strategy artifact root and bound Python executable.

### Task 1: Runtime identity and immutable generated strategy artifact

**Files:**
- Create: `trading_agent/generated_strategy_runtime.py`
- Create: `trading_agent/generated_strategy_artifact.py`
- Create: `tests/test_generated_strategy_artifact.py`
- Create: `tests/test_generated_strategy_runtime.py`

- [ ] **Step 1: Write failing runtime identity tests**

Test a real `sys.executable`, a substituted executable hash, non-canonical package inventory ordering, and `/usr/bin/sandbox-exec` identity. The public API is:

```python
identity = resolve_generated_strategy_runtime(Path(sys.executable))
assert identity.python_executable == Path(sys.executable).resolve(strict=True)
assert len(identity.python_executable_sha256) == 64
assert len(identity.runtime_fingerprint) == 64
assert identity.sandbox_profile_version == "generated_strategy_sandbox_v1"
```

Run: `uv run pytest tests/test_generated_strategy_runtime.py -q`

Expected: FAIL because `generated_strategy_runtime` does not exist.

- [ ] **Step 2: Implement typed runtime identity**

Use a frozen Pydantic boundary for serialized identity and a frozen dataclass for the resolved executable:

```python
class GeneratedStrategyRuntimeIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    python_executable: Path
    python_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    sandbox_profile_version: Literal["generated_strategy_sandbox_v1"]
```

Resolve and descriptor-check the interpreter as a current-user or root-owned executable regular file with no symlink components. Produce the package inventory using the bound interpreter in isolated mode and `importlib.metadata`, serialize sorted `name==version` rows, and hash executable bytes plus inventory hash. Never include environment values in the inventory receipt.

- [ ] **Step 3: Run runtime tests green**

Run: `uv run pytest tests/test_generated_strategy_runtime.py -q`

Expected: PASS.

- [ ] **Step 4: Write failing artifact publication tests**

Assert canonical SHA identity, private mode `600` files, idempotent replay and rejection of a same-path/different-content substitution. Build the artifact only from `ProposedHypothesis`, `GeneratedStrategyRuntimeIdentity`, source bytes and receipt hashes.

Run: `uv run pytest tests/test_generated_strategy_artifact.py -q`

Expected: FAIL because the artifact store does not exist.

- [ ] **Step 5: Implement the artifact boundary and store**

The artifact payload includes `source_sha256`, hypothesis/card/source keys, prompt/response/model identity, sorted free parameters, runtime identity and `created_at`. Use canonical experiment-ledger JSON for identity and existing `publish_private_immutable_text` for:

```text
{strategy_root}/{artifact_id}/strategy.py
{strategy_root}/{artifact_id}/manifest.json
```

Expose `publish(proposal) -> PublishedGeneratedStrategy` and `load(artifact_id) -> GeneratedStrategyArtifact`; load must rehash both files and reject name/content/runtime inconsistencies.

- [ ] **Step 6: Run artifact tests green and commit**

Run: `uv run pytest tests/test_generated_strategy_runtime.py tests/test_generated_strategy_artifact.py -q`

Expected: PASS.

Commit:

```bash
git add trading_agent/generated_strategy_runtime.py trading_agent/generated_strategy_artifact.py tests/test_generated_strategy_runtime.py tests/test_generated_strategy_artifact.py
git commit -m "feat: persist runtime-bound generated strategies"
```

### Task 2: Researcher artifact publication and unrestricted-code Critic

**Files:**
- Modify: `trading_agent/researcher_pipeline.py`
- Modify: `trading_agent/researcher_llm.py`
- Modify: `trading_agent/critic_agent.py`
- Modify: `run_researcher_propose.py`
- Modify: `tests/test_researcher_agent.py`
- Modify: `tests/test_researcher_llm_contract.py`
- Modify: `tests/test_run_researcher_propose_cli.py`
- Modify: `tests/test_researcher_pipeline_e2e.py`

- [ ] **Step 1: Write failing pipeline lineage tests**

Extend pipeline construction with a `GeneratedStrategyArtifactStore`. Assert that accepted output exposes `strategy_artifact`, that source publication exists before the hypothesis manifest, and that tampered source prevents registration and queue publication.

```python
assert accepted.strategy_artifact.artifact.payload.response_sha256 == accepted.proposal.llm_receipt.response_sha256
assert accepted.strategy_artifact.source_path.read_text() == accepted.proposal.strategy_draft.source_code
```

Run: `uv run pytest tests/test_researcher_agent.py tests/test_researcher_pipeline_e2e.py -q`

Expected: FAIL because accepted proposals do not carry artifacts.

- [ ] **Step 2: Publish the artifact before registration**

Add the strategy store to `ResearcherPipelineStores` and `PublishedGeneratedStrategy` to `AcceptedResearchProposal`. In `ResearcherPipeline.run`, order side effects exactly as:

```text
record call -> critique receipt -> generated artifact -> hypothesis manifest -> ledger registration -> queue projection
```

If artifact publication fails, do not create the manifest, ledger card or queue.

- [ ] **Step 3: Replace heuristic AST restriction with protocol contract**

Delete `_look_ahead_evidence` and its `ast`-based callers from `DeterministicHypothesisCritic`. Keep rejected-hypothesis deduplication and the four-free-parameter ceiling. Update the LLM output contract to require `create_strategy(context)` returning an object with `observe(bar, candidate)`, but test only the parsed machine contract fields, never prompt prose.

- [ ] **Step 4: Extend proposal CLI arguments and reports**

Add required `--strategy-root` and optional `--python-executable` defaulting to `sys.executable`. Resolve runtime once, construct the artifact store, and report only artifact ID/path name plus hashes. Never report source, stderr, account identifiers or environment values.

- [ ] **Step 5: Verify red-to-green and commit**

Run:

```bash
uv run pytest tests/test_researcher_agent.py tests/test_researcher_llm_contract.py tests/test_run_researcher_propose_cli.py tests/test_researcher_pipeline_e2e.py -q
```

Expected: PASS.

Commit:

```bash
git add trading_agent/researcher_pipeline.py trading_agent/researcher_llm.py trading_agent/critic_agent.py run_researcher_propose.py tests/test_researcher_agent.py tests/test_researcher_llm_contract.py tests/test_run_researcher_propose_cli.py tests/test_researcher_pipeline_e2e.py
git commit -m "feat: bind researcher output to strategy artifacts"
```

### Task 3: Framed protocol and real macOS sandbox execution

**Files:**
- Create: `trading_agent/generated_strategy_protocol.py`
- Create: `trading_agent/generated_strategy_runner.py`
- Create: `trading_agent/generated_strategy_sandbox.py`
- Create: `tests/test_generated_strategy_protocol.py`
- Create: `tests/test_generated_strategy_sandbox.py`

- [ ] **Step 1: Write failing protocol boundary tests**

Define frozen Pydantic variants `RunnerReady`, `ObserveRequest`, `NoSignalResponse`, `SignalResponse`, and `RunnerFailure`; every frame carries `sequence`. Test canonical newline-delimited JSON, 64 KiB frame cap, extra-field rejection, NaN rejection, mismatched symbol/timestamp and duplicate/out-of-order sequence rejection.

Run: `uv run pytest tests/test_generated_strategy_protocol.py -q`

Expected: FAIL because protocol types are absent.

- [ ] **Step 2: Implement protocol encode/parse**

Use a discriminated union on `kind` and exhaustive `match` at both ends. Convert validated `SignalResponse` to the existing frozen `StrategySignal` only after checking:

```python
response.sequence == request.sequence
response.symbol == request.bar.symbol
response.timestamp == request.bar.timestamp
math.isfinite(response.entry) and math.isfinite(response.stop)
response.entry > response.stop > 0.0
```

- [ ] **Step 3: Run protocol tests green**

Run: `uv run pytest tests/test_generated_strategy_protocol.py -q`

Expected: PASS.

- [ ] **Step 4: Write failing real-sandbox tests**

Use `/usr/bin/sandbox-exec` rather than a mock for the security boundary. Test four generated sources:

1. stateful happy strategy emits after two bars;
2. `urllib.request.urlopen` cannot access loopback or public network;
3. `Path(outside_sentinel).read_text()` is denied;
4. infinite loop exits through timeout and memory allocation exits within the configured RSS cap.

Also assert the rendered profile starts deny-by-default, contains `(deny network*)`, has no network allow, exposes only system/runtime/artifact/task roots, clears sensitive environment variables and uses a new process group.

Run: `uv run pytest tests/test_generated_strategy_sandbox.py -q`

Expected: FAIL because the sandbox adapter does not exist.

- [ ] **Step 5: Implement the child runner**

Launch only through:

```python
command = (
    "/usr/bin/sandbox-exec",
    "-p",
    profile,
    str(runtime.python_executable),
    "-I",
    str(runner_path),
    str(source_path),
)
```

The runner imports the exact generated file with `importlib.util.spec_from_file_location`, redirects generated stdout to bounded stderr, creates one strategy object, responds `ready`, and then handles one validated request at a time. It never reads a dataset file and never calculates PnL or orders.

- [ ] **Step 6: Implement the host adapter and cleanup**

Use `subprocess.Popen` with pipes, `start_new_session=True`, a pre-exec resource-limit function, `selectors.DefaultSelector` for bounded reads, and a monotonic deadline. `GeneratedStrategySession` is a context manager implementing `IntradayStrategy.observe`; `__exit__` closes stdin, waits, then TERM/KILLs the process group if needed. Map exit/timeout/protocol outcomes to a typed `GeneratedStrategyExecutionError(reason)`.

- [ ] **Step 7: Run real sandbox tests green and commit**

Run:

```bash
uv run pytest tests/test_generated_strategy_protocol.py tests/test_generated_strategy_sandbox.py -q
```

Expected: PASS, including observable network/file denial and timeout cleanup.

Commit:

```bash
git add trading_agent/generated_strategy_protocol.py trading_agent/generated_strategy_runner.py trading_agent/generated_strategy_sandbox.py tests/test_generated_strategy_protocol.py tests/test_generated_strategy_sandbox.py
git commit -m "feat: execute generated strategies in macOS sandbox"
```

### Task 4: Generated manifest and strategy registration

**Files:**
- Create: `trading_agent/generated_intraday_research_models.py`
- Create: `trading_agent/generated_intraday_registration.py`
- Create: `tests/test_generated_intraday_registration.py`

- [ ] **Step 1: Write failing manifest and registration tests**

Test a one-item `generated_python_intraday_v1` manifest, canonical hashes, maximum three hypotheses, bounded sessions/bars/resources, source queue/card lineage, runtime/artifact match, idempotent replay and rejected stale/substituted queue artifacts.

The selection contract is:

```python
class GeneratedStrategySelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["generated_python"] = "generated_python"
    artifact_id: str
    hypothesis_id: str
    strategy_version: str
    queue_card_key: str
    data_foundation_sha256: str
    runtime_fingerprint: str
    sandbox_profile_version: Literal["generated_strategy_sandbox_v1"]
```

Run: `uv run pytest tests/test_generated_intraday_registration.py -q`

Expected: FAIL because generated manifest/registration do not exist.

- [ ] **Step 2: Implement generated manifest parsing**

Mirror the existing bounded budgets without modifying `IntradayResearchManifest`. Require source-backed queue snapshot/input hashes, aware registration time, unique artifact/hypothesis/version IDs, costs between 20 and 100 bps and `minimum_training_sessions < max_sessions`.

- [ ] **Step 3: Implement source/card/artifact-bound registration**

Register:

```python
StrategyVersionRegistration(
    strategy_id="generated_python",
    strategy_version=f"generated-python:{artifact.artifact_id}",
    hypothesis_id=card.hypothesis.hypothesis_id,
    experiment_scope_key=card.hypothesis.experiment_scope_key,
    lane_id=card.hypothesis.primary_lane,
    code_version=artifact.payload.source_sha256,
    parameter_set=artifact.payload.free_parameters,
    data_contract=CURRENT_DATA_CONTRACT,
    cost_model=CURRENT_COST_MODEL,
    portfolio_policy=SHADOW_PORTFOLIO_POLICY,
    source_registered_at=card.hypothesis.source_registered_at,
    ledger_recorded_at=manifest.registered_at,
)
```

Require the queue item to be `STRATEGY_DESIGN` for first registration or an exact replay/refresh route thereafter. Require artifact response/card/source/runtime identity to match ledger evidence before writer registration.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/test_generated_intraday_registration.py -q`

Expected: PASS.

Commit:

```bash
git add trading_agent/generated_intraday_research_models.py trading_agent/generated_intraday_registration.py tests/test_generated_intraday_registration.py
git commit -m "feat: register generated intraday strategy versions"
```

### Task 5: Generated walk-forward, ledger terminal events and existing Reviewer

**Files:**
- Create: `trading_agent/heavy_empirical_lease.py`
- Create: `trading_agent/generated_intraday_trial.py`
- Create: `trading_agent/generated_intraday_loop.py`
- Modify: `trading_agent/intraday_research_loop.py`
- Modify: `trading_agent/intraday_research_artifacts.py`
- Modify: `trading_agent/intraday_walk_forward_models.py`
- Modify: `trading_agent/intraday_research_reviewer.py`
- Create: `tests/test_generated_intraday_trial.py`
- Create: `tests/test_generated_intraday_loop.py`
- Modify: `tests/test_intraday_walk_forward.py`
- Modify: `tests/test_intraday_research_reviewer.py`

- [ ] **Step 1: Write failing generated walk-forward tests**

Use the real example bars and real sandbox happy strategy. Assert the host `RecommendationEngine` derives targets, applies at least 20 bps cost, resolves same-bar stop/target at stop, records session outcomes and returns a schema-v3 `GeneratedIntradayWalkForwardResult` containing artifact/version/runtime/signal-stream hashes.

Run: `uv run pytest tests/test_generated_intraday_trial.py -q`

Expected: FAIL because the generated trial path does not exist.

- [ ] **Step 2: Add a generated result variant without changing v1/v2 serialization**

Keep `IntradayWalkForwardResult` byte-compatible. Add `GeneratedIntradayWalkForwardResult` with the same reviewer metric fields plus `strategy_version`, `strategy_artifact_id`, `runtime_fingerprint` and `signal_stream_sha256`. Extend `IntradayExperimentPayload.result` to the explicit union and allow payload/artifact schema `3`. Existing v1/v2 tests must remain unchanged and green.

- [ ] **Step 3: Implement generated evaluation with the existing host engine**

For each OOS session:

```python
with sandbox.open_session(artifact) as strategy:
    engine = RecommendationEngine(MomentumScanner(ScannerConfig()), strategy, RiskConfig(), store)
    for bar in session_bars:
        engine.process(bar)
    engine.finalize_day(last_bar)
```

Only the strategy `observe` call crosses the sandbox. Reuse `PaperStore`, `extract_paper_trades`, `summarize_performance`, cost handling and `INTRADAY_BOOTSTRAP_SEED`; do not trust generated PnL.

- [ ] **Step 4: Write failing terminal-event and Reviewer tests**

Assert successful generated trials append STARTED then COMPLETED with one experiment artifact. Import/contract/timeout/OOM/non-determinism append FAILED with exact reason; sandbox/runtime/data unavailability append CENSORED. Assert the unchanged Reviewer produces HOLD/PROMOTE/DEMOTE from generated metrics and never changes lifecycle or order authority.

- [ ] **Step 5: Implement trial and loop orchestration**

Move the existing lock implementation unchanged into `heavy_empirical_lease.py` and import it from both loops. Generated loop responsibilities are: load bounded bars, verify input hash, validate foundation for US-equity/day-trading/minute historical use, register artifact-bound version, acquire the one heavy lease, run/replay trial, publish experiment artifact, call existing Reviewer, return counts/decisions.

- [ ] **Step 6: Run regression and generated suites green**

Run:

```bash
uv run pytest tests/test_generated_intraday_trial.py tests/test_generated_intraday_loop.py tests/test_intraday_walk_forward.py tests/test_intraday_research_reviewer.py tests/test_source_backed_intraday_research.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add trading_agent/heavy_empirical_lease.py trading_agent/generated_intraday_trial.py trading_agent/generated_intraday_loop.py trading_agent/intraday_research_loop.py trading_agent/intraday_research_artifacts.py trading_agent/intraday_walk_forward_models.py trading_agent/intraday_research_reviewer.py tests/test_generated_intraday_trial.py tests/test_generated_intraday_loop.py tests/test_intraday_walk_forward.py tests/test_intraday_research_reviewer.py
git commit -m "feat: evaluate generated strategies through reviewer"
```

### Task 6: Bounded autonomous cycle and feedback CLI

**Files:**
- Create: `trading_agent/autonomous_research_cycle.py`
- Create: `run_autonomous_research_cycle.py`
- Create: `tests/test_autonomous_research_cycle.py`
- Create: `tests/test_autonomous_research_cycle_cli.py`
- Modify: `examples/research/researcher-response-fixture-v1.json`

- [ ] **Step 1: Write failing full-cycle application test**

Drive fixture LLM response through real library surfaces:

```text
context -> Researcher -> Critic -> source artifact -> version registration
        -> real sandbox -> historical result -> Reviewer -> rebuilt feedback context
```

Assert one artifact/version/trial/review, deterministic HOLD on the tiny fixture, next context includes generated terminal evidence, and all authority/mutation counters remain false/zero.

Run: `uv run pytest tests/test_autonomous_research_cycle.py -q`

Expected: FAIL because the application service does not exist.

- [ ] **Step 2: Implement one bounded application service**

Use frozen config/result dataclasses. The service accepts the existing generator/critic, ledger/receipt/artifact stores, bounded input/foundation/lane evidence and runtime identity. It permits one accepted generated artifact and one heavy empirical trial per invocation, then calls `build_researcher_context` again after the terminal event. No retry loop exists outside the Researcher maximum of three proposal attempts.

- [ ] **Step 3: Write failing CLI help, bad-input and happy-path tests**

The CLI requires context, ledger, receipts, generated strategy root, hypothesis manifest/queue roots, input CSV, source queue, data foundation, review/experiment/output roots and either response fixture or Hermes executable. It accepts `--python-executable`, proposal/resource budgets and prints no source/secrets. Bad runtime/source returns 1 with a private blocked report; fixture happy path returns 0 with artifact/trial/review IDs and mutation `0`.

- [ ] **Step 4: Implement CLI and update fixture entrypoint**

Add the standard PEP 723 header and existing `argparse`/private-report style. Update only the fixture's `strategy_source` to implement `create_strategy`/`observe`; keep hypothesis/source evidence unchanged.

- [ ] **Step 5: Run cycle and CLI tests green**

Run:

```bash
uv run pytest tests/test_autonomous_research_cycle.py tests/test_autonomous_research_cycle_cli.py tests/test_researcher_pipeline_e2e.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add trading_agent/autonomous_research_cycle.py run_autonomous_research_cycle.py tests/test_autonomous_research_cycle.py tests/test_autonomous_research_cycle_cli.py tests/test_researcher_pipeline_e2e.py examples/research/researcher-response-fixture-v1.json
git commit -m "feat: run bounded autonomous strategy research cycle"
```

### Task 7: Quality gates and manual surface verification

**Files:**
- Modify only files proven defective by this task's verification.

- [ ] **Step 1: Run targeted lint and type checks**

Run:

```bash
uv run ruff check trading_agent/generated_strategy_*.py trading_agent/generated_intraday_*.py trading_agent/autonomous_research_cycle.py run_researcher_propose.py run_autonomous_research_cycle.py tests/test_generated_*.py tests/test_autonomous_research_cycle*.py
uv run basedpyright trading_agent/generated_strategy_*.py trading_agent/generated_intraday_*.py trading_agent/autonomous_research_cycle.py run_researcher_propose.py run_autonomous_research_cycle.py
```

Expected: exit 0, zero diagnostics.

- [ ] **Step 2: Run the Python no-excuse and size audit**

Run:

```bash
uv run /Users/goyunseo/.codex/plugins/cache/sisyphuslabs/omo/4.19.3/skills/programming/scripts/python/check-no-excuse-rules.py trading_agent/generated_strategy_artifact.py trading_agent/generated_strategy_runtime.py trading_agent/generated_strategy_protocol.py trading_agent/generated_strategy_runner.py trading_agent/generated_strategy_sandbox.py trading_agent/generated_intraday_research_models.py trading_agent/generated_intraday_registration.py trading_agent/generated_intraday_trial.py trading_agent/generated_intraday_loop.py trading_agent/autonomous_research_cycle.py run_researcher_propose.py run_autonomous_research_cycle.py
```

Then count pure LOC for those paths. Split any new module over 250 pure LOC before continuing; do not suppress the rule.

Expected: exit 0 and every new module at or below 250 pure LOC.

- [ ] **Step 3: Run the full test suite once**

Run: `uv run pytest -q`

Expected: all tests PASS; never delete or weaken a failing test.

- [ ] **Step 4: Manually verify the real CLI surface**

Run:

```bash
uv run run_autonomous_research_cycle.py --help
research_bad_root=$(mktemp -d)
uv run run_autonomous_research_cycle.py --context examples/research/researcher-context-v1.json --response-fixture examples/research/researcher-response-fixture-v1.json --experiment-ledger "$research_bad_root/experiment.sqlite3" --receipt-root "$research_bad_root/receipts" --strategy-root "$research_bad_root/strategies" --manifest-root "$research_bad_root/manifests" --queue-root "$research_bad_root/queue" --input-csv examples/example_intraday.csv --data-foundation-manifest examples/data/us-orb-data-foundation-v1.json --artifact-root "$research_bad_root/experiments" --review-root "$research_bad_root/reviews" --output-dir "$research_bad_root/report" --python-executable /bin/false
research_happy_root=$(mktemp -d)
uv run run_autonomous_research_cycle.py --context examples/research/researcher-context-v1.json --response-fixture examples/research/researcher-response-fixture-v1.json --experiment-ledger "$research_happy_root/experiment.sqlite3" --receipt-root "$research_happy_root/receipts" --strategy-root "$research_happy_root/strategies" --manifest-root "$research_happy_root/manifests" --queue-root "$research_happy_root/queue" --input-csv examples/example_intraday.csv --data-foundation-manifest examples/data/us-orb-data-foundation-v1.json --artifact-root "$research_happy_root/experiments" --review-root "$research_happy_root/reviews" --output-dir "$research_happy_root/report"
uv run pytest tests/test_generated_strategy_sandbox.py -q
```

Inspect the private report and experiment ledger. Confirm the happy path produced one generated source artifact, one completed historical trial, one Reviewer artifact and zero broker mutations. Run adversarial network/file strategies through the same sandbox surface and confirm terminal failure receipts.

- [ ] **Step 5: Verify broker boundary remained untouched**

Run:

```bash
uv run pytest tests/test_alpaca_paper_config.py tests/test_alpaca_paper_mutation_client.py tests/test_alpaca_paper_entry_mutation_client.py -q
rg -n "alpaca|\.config/trading-agent|paper-api" trading_agent/generated_strategy_*.py trading_agent/generated_intraday_*.py trading_agent/autonomous_research_cycle.py
```

Expected: endpoint-guard tests PASS and the source scan has no generated-runtime broker or credential reference.

- [ ] **Step 6: Commit verification fixes, if any**

Stage only fixes caused by this feature and commit with the repository's `fix:` style. If no fixes were needed, do not create an empty commit.
