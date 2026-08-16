# Outcome-First 6-Agent Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve safe bounded source values through Decide, bind every non-no-action decision to an input subject, and prevent prose-only decisions from becoming completed research results.

**Architecture:** Extend the existing JSON envelope stored in `ResearchAgentCycleStore`; do not add a table or database. Source adapters publish canonical bounded payloads and available subject identities, the decision parser validates selected subjects, and the runtime passes an explicit action context to the existing executor. Until family adapters arrive in later slices, only deterministic no-action and the existing Systematic heavy executor may terminate successfully.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite JSON payloads, pytest, Ruff, basedpyright

---

## File map

- `trading_agent/research_agent_cycle_models.py`: evidence payload/subject fields, decision subjects, terminal result invariants.
- `trading_agent/research_agent_source_common.py`: turn canonical adapter payloads into bounded evidence without discarding values.
- `trading_agent/research_agent_decision.py`: render bounded payloads, validate selected subject refs, preserve them in audited decisions.
- `trading_agent/research_agent_actions.py`: define action context/protocol and reject prose-only completion.
- `trading_agent/research_agent_runtime.py`: pass evidence/open-work/decision context into action execution.
- `trading_agent/research_agent_runtime_support.py`: give scheduled/retry/failure evidence small canonical payloads.
- `trading_agent/research_agent_service_runtime.py`: construct the simplified action config without the dead verified-ref allowlist.
- `tests/test_research_agent_cycle_models.py`: payload hash/canonical and result-status invariants.
- `tests/test_research_agent_sources.py`: actual source values survive projection.
- `tests/test_research_agent_decision.py`: prompt and subject validation.
- `tests/test_research_agent_actions.py`: no-action, Systematic, and prose-only behavior.
- `tests/test_research_agent_runtime.py`: action-context wiring, failure isolation, cursor/restart behavior.
- `tests/test_research_agent_service_runtime.py`, `tests/test_research_agent_systematic.py`: constructor and protocol regression coverage.
- Direct-constructor fixtures returned by `rg 'ResearchAgent(Evidence|Decision|Result)V1\('`: add compatible payload/subject fields only where execution reaches Decide/Act.

## Task 1: Preserve bounded canonical evidence

**Files:**
- Modify: `tests/test_research_agent_cycle_models.py`
- Modify: `tests/test_research_agent_sources.py`
- Modify: `trading_agent/research_agent_cycle_models.py`
- Modify: `trading_agent/research_agent_source_common.py`

- [ ] **Step 1: Write failing evidence-contract tests**

Add tests that use one canonical JSON payload and one real Opportunity source projection:

```python
def test_evidence_binds_canonical_payload_hash_and_subjects() -> None:
    payload = '{"candidates":[{"symbol":"AAPL"}],"schema_version":1}'
    digest = hashlib.sha256(payload.encode()).hexdigest()

    evidence = ResearchAgentEvidenceV1(
        evidence_id=EvidenceId("e" * 64),
        agent_family_id="opportunity_manager",
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key="opportunity.current.001",
        evidence_refs=(digest,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256=digest,
        market_id="us_equities",
        bounded_payload_json=payload,
        payload_truncated=False,
        subject_refs=("opportunity.current.001",),
    )

    assert json.loads(evidence.bounded_payload_json or "{}") == {
        "candidates": [{"symbol": "AAPL"}],
        "schema_version": 1,
    }


@pytest.mark.parametrize(
    ("payload", "digest", "reason"),
    (
        ('{"b":1,"a":2}', hashlib.sha256(b'{"a":2,"b":1}').hexdigest(), "bounded_payload_not_canonical"),
        ('{"a":1}', "0" * 64, "bounded_payload_hash_mismatch"),
    ),
)
def test_evidence_rejects_noncanonical_or_mismatched_payload(
    payload: str,
    digest: str,
    reason: str,
) -> None:
    with pytest.raises(ValidationError, match=reason):
        ResearchAgentEvidenceV1(
            evidence_id=EvidenceId("e" * 64),
            agent_family_id="opportunity_manager",
            trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
            source_key="opportunity.current.001",
            evidence_refs=(digest,),
            observed_at=NOW,
            available_at=NOW,
            payload_sha256=digest,
            market_id="us_equities",
            bounded_payload_json=payload,
            subject_refs=("opportunity.current.001",),
        )
```

In `tests/test_research_agent_sources.py`, extend the existing seeded Opportunity test:

```python
opportunity = next(item for item in projected if item.agent_family_id == "opportunity_manager")
payload = json.loads(opportunity.bounded_payload_json or "{}")
assert "ACME" in json.dumps(payload)
assert opportunity.subject_refs == (opportunity.source_key,)
assert opportunity.payload_sha256 == hashlib.sha256(opportunity.bounded_payload_json.encode()).hexdigest()
```

- [ ] **Step 2: Run the tests and confirm the contract is missing**

Run:

```bash
uv run pytest -q \
  tests/test_research_agent_cycle_models.py::test_evidence_binds_canonical_payload_hash_and_subjects \
  tests/test_research_agent_cycle_models.py::test_evidence_rejects_noncanonical_or_mismatched_payload \
  tests/test_research_agent_sources.py::test_source_projection_routes_evidence_without_cross_family_leakage
```

Expected: FAIL because `bounded_payload_json`, `payload_truncated`, and `subject_refs` are not model fields.

- [ ] **Step 3: Add backward-readable evidence fields and validation**

In `trading_agent/research_agent_cycle_models.py`, import `json` and add:

```python
_MAX_BOUNDED_PAYLOAD_BYTES = 48 * 1024


class ResearchAgentEvidenceV1(BaseModel):
    # existing fields remain unchanged
    bounded_payload_json: str | None = None
    payload_truncated: bool = False
    subject_refs: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def require_ordered_evidence(self) -> Self:
        if self.available_at < self.observed_at:
            raise InvalidResearchAgentCycleFieldError(reason="evidence_time_order_invalid")
        _require_references(self.evidence_refs, allow_empty=False)
        _require_references(self.subject_refs, allow_empty=self.bounded_payload_json is None)
        if self.bounded_payload_json is None:
            if self.payload_truncated or self.subject_refs:
                raise InvalidResearchAgentCycleFieldError(reason="bounded_payload_identity_invalid")
            return self
        encoded = self.bounded_payload_json.encode()
        if len(encoded) > _MAX_BOUNDED_PAYLOAD_BYTES:
            raise InvalidResearchAgentCycleFieldError(reason="bounded_payload_too_large")
        try:
            value = json.loads(self.bounded_payload_json)
        except (TypeError, ValueError):
            raise InvalidResearchAgentCycleFieldError(reason="bounded_payload_invalid") from None
        canonical = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if canonical != self.bounded_payload_json:
            raise InvalidResearchAgentCycleFieldError(reason="bounded_payload_not_canonical")
        if hashlib.sha256(encoded).hexdigest() != self.payload_sha256:
            raise InvalidResearchAgentCycleFieldError(reason="bounded_payload_hash_mismatch")
        return self
```

The optional default is only for reading existing cycle databases. New source material must always set the payload.

- [ ] **Step 4: Stop discarding `canonical_payload`**

Extend `ResearchAgentEvidenceMaterial` and its `evidence()` method:

```python
@dataclass(frozen=True, slots=True)
class ResearchAgentEvidenceMaterial:
    # existing fields
    canonical_payload: str
    subject_refs: tuple[str, ...] = ()
    payload_truncated: bool = False

    def evidence(self) -> ResearchAgentEvidenceV1:
        payload_sha256 = hashlib.sha256(self.canonical_payload.encode()).hexdigest()
        identity = hashlib.sha256(
            f"{self.family}:{self.trigger}:{self.source_key}:{payload_sha256}:evidence-v1".encode()
        ).hexdigest()
        subjects = self.subject_refs or (self.source_key,)
        return ResearchAgentEvidenceV1(
            evidence_id=EvidenceId(identity),
            agent_family_id=self.family,
            trigger_kind=self.trigger,
            source_key=self.source_key,
            evidence_refs=(payload_sha256,),
            observed_at=self.observed_at,
            available_at=self.available_at,
            payload_sha256=payload_sha256,
            market_id=self.market_id,
            bounded_payload_json=self.canonical_payload,
            payload_truncated=self.payload_truncated,
            subject_refs=tuple(sorted(set(subjects))),
        )
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest -q tests/test_research_agent_cycle_models.py tests/test_research_agent_sources.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add trading_agent/research_agent_cycle_models.py trading_agent/research_agent_source_common.py \
  tests/test_research_agent_cycle_models.py tests/test_research_agent_sources.py
git commit -m "feat(agent): preserve bounded evidence payloads"
```

## Task 2: Bind decisions to visible subjects

**Files:**
- Modify: `tests/test_research_agent_decision.py`
- Modify: `trading_agent/research_agent_cycle_models.py`
- Modify: `trading_agent/research_agent_decision.py`

- [ ] **Step 1: Write failing prompt and parser tests**

Update the test evidence to contain canonical payload and `subject_refs=("market_context.us.current",)`. Add
`subject_refs` to valid response JSON. Add these tests:

```python
def test_decision_prompt_contains_bounded_values_and_available_subjects() -> None:
    prompt = render_research_agent_prompt(_request())

    assert '"regime":"risk_on"' in prompt
    assert '"subject_refs":["market_context.us.current"]' in prompt


def test_decision_prompt_rejects_legacy_hash_only_evidence() -> None:
    legacy = _evidence().model_copy(
        update={"bounded_payload_json": None, "payload_truncated": False, "subject_refs": ()}
    )
    request = _request().model_copy(update={"evidence": (legacy,)})

    with pytest.raises(InvalidResearchAgentDecisionError, match="bounded_payload_missing"):
        render_research_agent_prompt(request)


def test_parser_rejects_subject_not_present_in_request() -> None:
    payload = json.loads(_response()) | {"subject_refs": ["market_context.fabricated"]}
    context = ResearchAgentDecisionParseContext(
        request=_request(),
        model_id="hermes-research-actor-v1",
        prompt_sha256="d" * 64,
    )

    with pytest.raises(InvalidResearchAgentDecisionError, match="decision_subject_unresolved"):
        parse_research_agent_decision(json.dumps(payload).encode(), context)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest -q tests/test_research_agent_decision.py
```

Expected: FAIL because prompt values and decision subject validation do not exist.

- [ ] **Step 3: Add decision subject fields and invariants**

In `ResearchAgentDecisionV1` and `HermesResearchAgentDecisionResponse`, add:

```python
subject_refs: tuple[str, ...] = Field(max_length=32)
```

In both decision validators:

```python
_require_references(self.subject_refs, allow_empty=True)
no_action = self.primary_decision is ResearchAgentDecisionKind.NO_ACTION
if no_action != (not self.subject_refs):
    raise InvalidResearchAgentCycleFieldError(reason="decision_subject_required")
```

Use the corresponding decision-module error type in `HermesResearchAgentDecisionResponse`.

- [ ] **Step 4: Render payload values and validate selected refs**

In `render_research_agent_prompt()` build each evidence entry as:

```python
if item.bounded_payload_json is None:
    raise InvalidResearchAgentDecisionError(reason="bounded_payload_missing")
evidence_item = {
    "evidence_id": item.evidence_id,
    "evidence_refs": item.evidence_refs,
    "market_id": item.market_id,
    "observed_at": item.observed_at.isoformat(),
    "payload": json.loads(item.bounded_payload_json),
    "payload_truncated": item.payload_truncated,
    "source_key": item.source_key,
    "subject_refs": item.subject_refs,
    "trigger_kind": item.trigger_kind,
}
```

Reject a request whose combined bounded payload bytes exceed `48 * 1024`. In
`parse_research_agent_decision()` validate against evidence and open work:

```python
available_subjects = {
    reference
    for item in context.request.evidence
    for reference in (str(item.evidence_id), *item.subject_refs)
} | {item.work_id for item in context.request.open_work}
if not set(response.subject_refs).issubset(available_subjects):
    raise InvalidResearchAgentDecisionError(reason="decision_subject_unresolved")
```

Copy `response.subject_refs` into `ResearchAgentDecisionV1` and state in the prompt that every non-no-action
decision must select at least one available subject while no-action selects none.

- [ ] **Step 5: Run focused tests**

```bash
uv run pytest -q tests/test_research_agent_decision.py tests/test_research_agent_cycle_models.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add trading_agent/research_agent_cycle_models.py trading_agent/research_agent_decision.py \
  tests/test_research_agent_cycle_models.py tests/test_research_agent_decision.py
git commit -m "feat(agent): bind decisions to evidence subjects"
```

## Task 3: Make terminal results artifact-bound

**Files:**
- Modify: `tests/test_research_agent_cycle_models.py`
- Modify: `tests/test_research_agent_actions.py`
- Modify: `trading_agent/research_agent_cycle_models.py`
- Modify: `trading_agent/research_agent_actions.py`

- [ ] **Step 1: Write failing terminal/action tests**

Add a completed-without-artifact rejection and replace the old prose-completion assertion:

```python
def test_completed_result_requires_authority_artifact() -> None:
    with pytest.raises(ValidationError, match="completed_artifact_required"):
        _result(status=ResearchAgentResultStatus.COMPLETED, artifact_refs=())


def test_non_systematic_prose_action_is_rejected(tmp_path: Path) -> None:
    executor = ResearchAgentActionExecutor(_config(tmp_path))

    with pytest.raises(InvalidResearchAgentActionError, match="prose_only_result"):
        executor.execute(_context("market_context", ResearchAgentDecisionKind.PUBLISH_CONTEXT))


def test_no_action_remains_a_valid_terminal_without_artifact(tmp_path: Path) -> None:
    executor = ResearchAgentActionExecutor(_config(tmp_path))

    result = executor.execute(_context("market_context", ResearchAgentDecisionKind.NO_ACTION))

    assert result.status is ResearchAgentResultStatus.NO_ACTION
    assert result.reason == "no_eligible_action"
    assert result.artifact_refs == ()
```

- [ ] **Step 2: Run tests and verify failure**

```bash
uv run pytest -q tests/test_research_agent_cycle_models.py tests/test_research_agent_actions.py
```

Expected: FAIL because completed artifacts and action context are not enforced.

- [ ] **Step 3: Strengthen result invariants**

Extend `ResearchAgentResultV1.require_result_invariants()`:

```python
if self.status is ResearchAgentResultStatus.COMPLETED and not self.artifact_refs:
    raise InvalidResearchAgentCycleFieldError(reason="completed_artifact_required")
if self.status is ResearchAgentResultStatus.NO_ACTION:
    if self.reason is None or self.continuation is None or self.artifact_refs:
        raise InvalidResearchAgentCycleFieldError(reason="no_action_terminal_invalid")
if self.status in {ResearchAgentResultStatus.FAILED, ResearchAgentResultStatus.BLOCKED} and self.reason is None:
    raise InvalidResearchAgentCycleFieldError(reason="failure_reason_required")
```

- [ ] **Step 4: Introduce the action protocol/context and remove prose completion**

In `research_agent_actions.py` add:

```python
@dataclass(frozen=True, slots=True)
class ResearchAgentActionContext:
    cycle: ResearchAgentCycleV1
    evidence: tuple[ResearchAgentEvidenceV1, ...]
    open_work: tuple[ResearchAgentOpenWorkV1, ...]
    decision: ResearchAgentDecisionV1
    observed_at: dt.datetime


class ResearchAgentActionClient(Protocol):
    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1: ...
```

Keep `ResearchAgentActionConfig` with only the existing Systematic executor. Change
`ResearchAgentActionExecutor.execute()` to accept one context, verify cycle/family/subject identities, and implement
only these successful paths:

```python
match decision.primary_decision:
    case ResearchAgentDecisionKind.NO_ACTION:
        return _no_action_result(context)
    case ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT:
        if cycle.agent_family_id != "systematic_quant":
            raise InvalidResearchAgentActionError(reason="heavy_experiment_systematic_only")
        return self._config.systematic.execute(cycle, decision)
    case _:
        raise InvalidResearchAgentActionError(reason="prose_only_result")
```

`_no_action_result()` copies decision question/summary/reason/continuation/evidence/next-wake, sets
`artifact_refs=()`, and preserves all three authority fields as false. Delete `result_from_decision()`.

- [ ] **Step 5: Update action fixtures and run tests**

Add canonical evidence, subject refs, open work and `observed_at` to `_context()`. Remove
`verified_trade_signal_refs` from `_config()`.

```bash
uv run pytest -q tests/test_research_agent_actions.py tests/test_research_agent_cycle_models.py \
  tests/test_research_agent_systematic.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add trading_agent/research_agent_cycle_models.py trading_agent/research_agent_actions.py \
  tests/test_research_agent_cycle_models.py tests/test_research_agent_actions.py \
  tests/test_research_agent_systematic.py
git commit -m "fix(agent): reject prose-only completed results"
```

## Task 4: Wire action context through the runtime

**Files:**
- Modify: `tests/test_research_agent_runtime.py`
- Modify: `tests/test_research_agent_service_runtime.py`
- Modify: `trading_agent/research_agent_runtime.py`
- Modify: `trading_agent/research_agent_runtime_support.py`
- Modify: `trading_agent/research_agent_service_runtime.py`
- Modify: direct-constructor fixtures reported by `rg`

- [ ] **Step 1: Write a failing action-context runtime test**

Use a fake action client rather than the production prose-rejecting executor:

```python
@dataclass(frozen=True, slots=True)
class RecordingArtifactActionClient:
    contexts: list[ResearchAgentActionContext]

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        self.contexts.append(context)
        return ResearchAgentResultV1(
            result_id=research_agent_result_id(context.cycle.cycle_id),
            cycle_id=context.cycle.cycle_id,
            agent_family_id=context.cycle.agent_family_id,
            market_id=context.cycle.market_id,
            status=ResearchAgentResultStatus.COMPLETED,
            question=context.decision.question,
            summary="A deterministic fixture artifact was recorded for runtime contract testing.",
            reason=None,
            continuation=None,
            evidence_refs=context.decision.evidence_refs,
            artifact_refs=(context.evidence[0].payload_sha256,),
            occurred_at=context.observed_at,
            next_wake_kind=context.decision.next_wake_kind,
            next_wake_at=context.decision.next_wake_at,
        )
```

Assert after one tick:

```python
assert contexts[0].evidence[0].bounded_payload_json == '{"sequence":1}'
assert contexts[0].decision.subject_refs == contexts[0].evidence[0].subject_refs
assert contexts[0].observed_at == NOW + dt.timedelta(minutes=2)
```

Also change the existing research-family blocked-evidence test to expect `FAILED`, reason
`prose_only_result`, and zero artifacts when it uses the production executor.

- [ ] **Step 2: Run runtime tests and verify failure**

```bash
uv run pytest -q tests/test_research_agent_runtime.py tests/test_research_agent_service_runtime.py
```

Expected: FAIL because `ResearchAgentRuntimeServices` and `_actions.execute()` still use the old signature.

- [ ] **Step 3: Pass request evidence and open work into Act**

Change `ResearchAgentRuntimeServices.actions` to `ResearchAgentActionClient`. In `_tick()` retain the family open
work tuple used for the request and call:

```python
context = ResearchAgentActionContext(
    cycle=cycle,
    evidence=request.evidence,
    open_work=request.open_work,
    decision=decision,
    observed_at=now,
)
result = self._actions.execute(context)
```

The production executor constructor becomes:

```python
ResearchAgentActionExecutor(ResearchAgentActionConfig(systematic=systematic))
```

- [ ] **Step 4: Give runtime-generated evidence canonical payloads**

In `research_agent_runtime_support.py`, construct scheduled, retry, open-work and source-failure evidence through
`ResearchAgentEvidenceMaterial` and `canonical_payload_json()`. Example:

```python
payload = canonical_payload_json(
    {
        "reason": reason,
        "schema_version": 1,
        "status": "source_failure",
    }
)
return ResearchAgentEvidenceMaterial(
    family=family,
    trigger=ResearchAgentTriggerKind.NEW_DATA,
    source_key=f"source_failure.{reason}",
    observed_at=now,
    available_at=now,
    market_id="none",
    canonical_payload=payload,
).evidence()
```

Use the existing open-work identity as `subject_refs` for retry evidence. Preserve current evidence identities when
the canonical payload content is unchanged.

- [ ] **Step 5: Update direct constructors without weakening legacy reads**

Use:

```bash
rg -n "ResearchAgentEvidenceV1\(|ResearchAgentDecisionV1\(|ResearchAgentResultV1\(" \
  trading_agent tests run_*.py
```

For execution fixtures, add canonical payload and source subject refs. For stored/operations fixtures that only
exercise backward reads, leave the optional payload absent and add a regression proving the store still opens.
Every non-no-action decision fixture must select a subject from its request.

- [ ] **Step 6: Run runtime/service/store regression tests**

```bash
uv run pytest -q \
  tests/test_research_agent_runtime.py \
  tests/test_research_agent_service_runtime.py \
  tests/test_research_agent_cycle_store.py \
  tests/test_dashboard_agent_runtime.py \
  tests/test_research_agent_operations.py
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

Stage only the files changed for runtime integration and direct fixture compatibility, then commit:

```bash
git add trading_agent/research_agent_runtime.py \
  trading_agent/research_agent_runtime_support.py \
  trading_agent/research_agent_service_runtime.py \
  tests/test_research_agent_runtime.py \
  tests/test_research_agent_service_runtime.py \
  tests/test_research_agent_cycle_store.py \
  tests/test_dashboard_agent_runtime.py \
  tests/test_research_agent_operations.py
git commit -m "refactor(agent): pass evidence context into actions"
```

## Task 5: Verify Slice 1 on its real surfaces

**Files:**
- Modify only if verification finds a Slice 1 defect.

- [ ] **Step 1: Run all targeted Slice 1 tests**

```bash
uv run pytest -q \
  tests/test_research_agent_cycle_models.py \
  tests/test_research_agent_cycle_store.py \
  tests/test_research_agent_sources.py \
  tests/test_research_agent_decision.py \
  tests/test_research_agent_actions.py \
  tests/test_research_agent_runtime.py \
  tests/test_research_agent_service_runtime.py \
  tests/test_research_agent_systematic.py \
  tests/test_dashboard_agent_runtime.py \
  tests/test_research_agent_operations.py
```

Expected: PASS.

- [ ] **Step 2: Run changed-file lint and type checks**

```bash
uv run ruff check \
  trading_agent/research_agent_cycle_models.py \
  trading_agent/research_agent_source_common.py \
  trading_agent/research_agent_decision.py \
  trading_agent/research_agent_actions.py \
  trading_agent/research_agent_runtime.py \
  trading_agent/research_agent_runtime_support.py \
  trading_agent/research_agent_service_runtime.py \
  tests/test_research_agent_cycle_models.py \
  tests/test_research_agent_sources.py \
  tests/test_research_agent_decision.py \
  tests/test_research_agent_actions.py \
  tests/test_research_agent_runtime.py

uv run basedpyright \
  trading_agent/research_agent_cycle_models.py \
  trading_agent/research_agent_source_common.py \
  trading_agent/research_agent_decision.py \
  trading_agent/research_agent_actions.py \
  trading_agent/research_agent_runtime.py \
  trading_agent/research_agent_runtime_support.py \
  trading_agent/research_agent_service_runtime.py
```

Expected: both commands exit 0.

- [ ] **Step 3: Manually run CLI help and a bad config**

```bash
uv run python run_research_agent_runtime.py --help
uv run python run_research_agent_runtime.py tick --config /tmp/nonexistent-outcome-first-agent.json
```

Expected: help exits 0; bad config exits 2 without secrets, paths, headers, or account identifiers.

- [ ] **Step 4: Observe a real configured source without model or broker effects**

Run a read-only driver against the current private config. It must print only safe booleans and identities:

```bash
uv run python -c 'import datetime as dt,json; from pathlib import Path; from trading_agent.research_agent_service_config import load_research_agent_service_config; from trading_agent.research_agent_sources import collect_research_agent_evidence_isolated; c=load_research_agent_service_config(Path("/Users/goyunseo/.config/trading-agent/research-agent-runtime-v2.json")); b=collect_research_agent_evidence_isolated(c.source_paths,now=dt.datetime.now(dt.UTC)); print(json.dumps({"families":sorted({e.agent_family_id for e in b.evidence}),"payloads_present":all(e.bounded_payload_json is not None for e in b.evidence),"subject_refs_present":all(bool(e.subject_refs) for e in b.evidence),"source_failures":len(b.failures),"model_calls":0,"broker_mutation":0},sort_keys=True))'
```

Expected: exit 0, at least one family, `payloads_present=true`, `subject_refs_present=true`, `model_calls=0`, and
`broker_mutation=0`. Do not print the payload bodies.

- [ ] **Step 5: Prove prose-only completion is rejected through the runtime**

Run the focused runtime regression:

```bash
uv run pytest -q tests/test_research_agent_runtime.py -k 'prose_only or action_context'
```

Expected: PASS and persisted result reason `prose_only_result` with empty artifact refs.

- [ ] **Step 6: Record Slice 1 evidence and inspect the final diff**

```bash
git status --short
git log --oneline 278a097..HEAD
git diff --check 278a097..HEAD
```

Expected: only Slice 1 plan/code/tests are present, all commits are atomic, and diff check exits 0. Slice 2 may begin
only after these acceptance results are recorded in the task handoff.
