# Professional Multi-Market Agent OS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing brownfield research system into an observable, always-on product that delivers separate agent recommendations through Hermes, closes the US and KR Day operating verticals, and then adds Swing, automated research lifecycle, Systematic, Derivatives, and Allocation in evidence-gated stages.

**Architecture:** Keep the existing modular monolith, append-only stores, and single Paper writer. Add only the user-facing contracts missing from the current vertical: acceptance evidence manifests, a durable Hermes delivery projection, a versioned Hermes plugin, and a signed one-use arm gateway. Every later agent family reuses the shared point-in-time evidence and Reviewer kernel but owns independent lane manifests, state machines, trials, and outcomes.

**Tech Stack:** Python 3.12, Pydantic v2, mode-600 SQLite, existing Alpaca Paper REST/WSS runtime, KIS/LS/OpenDART read-only adapters, Hermes plugin API, Telegram Bot API owned by Hermes, pytest, Ruff, basedpyright, macOS launchd.

---

## Source Of Truth

- Seed: `docs/superpowers/specs/2026-07-22-professional-multi-market-agent-os-seed.yaml`
- Canonical design: `docs/superpowers/specs/2026-07-17-institutional-multi-market-quant-research-os-design.md`
- Product status: `docs/milestones_status_ko.md`
- Safety rules: `AGENTS.md`

The Seed acceptance criteria are product outcomes. Passing unit tests, fixture E2E, schemas, or CLIs does not by itself complete an outcome. Operational completion requires a clean immutable commit and schema-valid real-session manifests.

## Delivery Order

| Stage | Product outcome | Child plan created at entry | Entry gate | Exit gate |
|---|---|---|---|---|
| 1 | Hermes + US Day | This document, Tasks 1-9 | Current brownfield tests green | Hermes query/delivery works and US has three scheduled sessions plus one natural Paper lifecycle |
| 2 | KR Day | `docs/superpowers/plans/2026-07-22-kr-day-operating-product.md` | Stage 1 delivery contract stable | Three open KRX sessions complete the real shadow lifecycle with zero KIS/LS mutations |
| 3 | Always-on soak | `docs/superpowers/plans/2026-07-22-hermes-multi-market-soak.md` | US and KR daily terminals available | Five consecutive sessions per market plus restart and provider-fault reconciliation |
| 4 | US Swing | `docs/superpowers/plans/2026-07-22-us-swing-operating-product.md` | Hermes lifecycle delivery stable | Replayable multi-session shadow entry, overnight state, exit, review, and message lineage |
| 5 | Loop Engineer + lifecycle v2 | `docs/superpowers/plans/2026-07-22-loop-engineer-lifecycle-v2.md` | Swing produces finalized forward evidence | One provenance-bound challenger reaches an immutable deterministic lifecycle decision |
| 6 | Systematic Quant | `docs/superpowers/plans/2026-07-22-systematic-quant-agent.md` | Shared lifecycle v2 passes | ETF trend/relative-strength and leveraged variants have separate reviewed shadow outcomes |
| 7 | Options + Futures | `docs/superpowers/plans/2026-07-22-derivatives-research-agents.md` | Capability and entitlement decision recorded | Options and futures lanes produce read-only/shadow outcomes and Hermes results |
| 8 | Allocation | `docs/superpowers/plans/2026-07-22-allocation-manager.md` | Two independent executable champions | Next-session budgets are derived from finalized prior-day snapshots without order authority |

Only the active stage receives detailed implementation tasks. A child plan is written from the then-current code and evidence after its entry gate passes, preventing stale filenames and speculative infrastructure.

## Stage 1 File Map

**Acceptance evidence**

- Create `trading_agent/acceptance_evidence.py`: typed manifest, artifact hashing, clean-commit and real-session verification.
- Create `run_acceptance_evidence.py`: `build` and `verify` CLI used by every Seed criterion.
- Create `tests/test_acceptance_evidence.py`: fixture-label rejection, commit mismatch, missing session, tamper, and happy path.

**Hermes delivery and query**

- Create `trading_agent/hermes_delivery_models.py`: immutable event, attempt, acknowledgement, and reply-lineage types.
- Create `trading_agent/hermes_delivery_store.py`: mode-600 append-only SQLite store and one-writer lease.
- Create `trading_agent/hermes_delivery_projection.py`: project existing recommendation and terminal events without changing source ledgers.
- Create `trading_agent/hermes_query_service.py`: deterministic Opportunity, Context, Day, Swing, Systematic, Derivatives, status, and result reads.
- Create `run_hermes_delivery.py`: project, claim, acknowledge, retry, reconcile, and query CLI.
- Create `tests/test_hermes_delivery_store.py`, `tests/test_hermes_delivery_e2e.py`, and `tests/test_hermes_query_service.py`.

**Hermes plugin**

- Create `integrations/hermes/trading-agent/plugin.yaml`: version and required non-secret project-root configuration.
- Create `integrations/hermes/trading-agent/__init__.py`: tool, slash-command, and worker registration.
- Create `integrations/hermes/trading-agent/delivery_worker.py`: bounded poller that sends through Hermes-owned Telegram credentials and writes broker acknowledgements back to the project store.
- Create `integrations/hermes/trading-agent/skills/trading-agent/SKILL.md`: query routing and explicit separation of agent opinions.
- Create `tests/test_hermes_plugin_contract.py` and `tests/test_hermes_plugin_delivery.py` using fake Hermes context and fake Telegram sender.

**Owner arm and US Day operating coordinator**

- Create `trading_agent/hermes_arm_request.py`: signed, expiring, one-use owner request and state transitions.
- Create `trading_agent/hermes_arm_store.py`: append-only request, confirmation, consumption, expiry, and revocation evidence.
- Create `trading_agent/hermes_arm_gateway.py`: session-owner validation and conversion to the existing `PaperMutationArm` only at consumption time.
- Create `run_hermes_arm_gateway.py`: `prepare`, `confirm`, `status`, and `revoke` CLI; it never imports an Alpaca mutation client.
- Create `trading_agent/us_day_operating_coordinator.py`: one-session owner around current ORB evidence, `PaperOperatingSession`, OCO, recovery, safety actions, and delivery projection.
- Create `run_us_day_operating_session.py`: `preflight`, `run`, `recover`, `finalize`, and `evidence` CLI.
- Create `tests/test_hermes_arm_gateway.py`, `tests/test_us_day_operating_vertical_e2e.py`, and `tests/test_paper_operating_session.py` extensions.

## Task 1: Acceptance Evidence Manifest

**Files:**
- Create: `trading_agent/acceptance_evidence.py`
- Create: `run_acceptance_evidence.py`
- Create: `tests/test_acceptance_evidence.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing manifest tests**

```python
def test_operational_manifest_rejects_fixture_session(tmp_path: Path) -> None:
    manifest = manifest_fixture(tmp_path, session_kind="fixture")
    with pytest.raises(InvalidAcceptanceEvidenceError, match="real session"):
        verify_acceptance_manifest(manifest, tmp_path, require_clean_commit=True, require_session_binding=True)


def test_operational_manifest_detects_artifact_tamper(tmp_path: Path) -> None:
    manifest = manifest_fixture(tmp_path, session_kind="real")
    manifest.artifacts[0].path.write_text("changed", encoding="utf-8")
    with pytest.raises(InvalidAcceptanceEvidenceError, match="content hash"):
        verify_acceptance_manifest(manifest, tmp_path, require_clean_commit=True, require_session_binding=True)
```

- [ ] **Step 2: Run the focused test and observe the missing module failure**

Run: `uv run pytest -q tests/test_acceptance_evidence.py`

Expected: FAIL during collection because `trading_agent.acceptance_evidence` does not exist.

- [ ] **Step 3: Implement the typed verifier and CLI**

```python
class AcceptanceEvidenceManifest(BaseModel):
    schema_version: Literal[1] = 1
    criterion_id: str
    policy_version: str
    commit_sha: str
    verifier_version: str
    generated_at: AwareDatetime
    sessions: tuple[AcceptanceSessionEvidence, ...]
    artifacts: tuple[AcceptanceArtifactEvidence, ...]


def verify_acceptance_manifest(
    manifest: AcceptanceEvidenceManifest,
    repository: Path,
    *,
    require_clean_commit: bool,
    require_session_binding: bool,
) -> None:
    require_exact_head(repository, manifest.commit_sha, require_clean=require_clean_commit)
    require_real_sessions(manifest.sessions, required=require_session_binding)
    verify_artifact_hashes(repository, manifest.artifacts)
```

The CLI must support the Seed command shape:

```bash
uv run python -m trading_agent.acceptance_evidence verify \
  --criterion AC-001 \
  --manifest outputs/acceptance/hermes/manifest.json \
  --require-clean-commit \
  --require-session-binding
```

- [ ] **Step 4: Prove malformed, fixture, dirty-worktree, wrong-commit, missing-session, and valid cases**

Run: `uv run pytest -q tests/test_acceptance_evidence.py`

Expected: PASS.

- [ ] **Step 5: Run static checks and commit**

Run: `uv run ruff check trading_agent/acceptance_evidence.py run_acceptance_evidence.py tests/test_acceptance_evidence.py && uv run basedpyright trading_agent/acceptance_evidence.py run_acceptance_evidence.py`

```bash
git add trading_agent/acceptance_evidence.py run_acceptance_evidence.py tests/test_acceptance_evidence.py pyproject.toml
git commit -m "feat: add operational acceptance evidence verifier"
```

## Task 2: Durable Hermes Delivery Store

**Files:**
- Create: `trading_agent/hermes_delivery_models.py`
- Create: `trading_agent/hermes_delivery_store.py`
- Create: `tests/test_hermes_delivery_store.py`

- [ ] **Step 1: Write state-machine and crash-recovery tests**

```python
def test_delivery_restarts_from_expired_claim_without_duplicate_identity(tmp_path: Path) -> None:
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    event = delivery_event_fixture()
    assert store.append_event(event).inserted is True
    first = store.claim_next(worker_id="worker-a", now=AT, lease_seconds=30)
    second = store.claim_next(worker_id="worker-b", now=AT + timedelta(seconds=31), lease_seconds=30)
    assert first is not None and second is not None
    assert first.event.delivery_id == second.event.delivery_id
    assert store.events() == (event,)
```

Also cover conflicting duplicate content, acknowledgement after lease loss, retry budget exhaustion, dead letter, and root/reply message IDs.

- [ ] **Step 2: Run the focused test and observe failure**

Run: `uv run pytest -q tests/test_hermes_delivery_store.py`

Expected: FAIL because the delivery models and store are absent.

- [ ] **Step 3: Implement immutable identities and append-only transitions**

```python
class HermesDeliveryKind(StrEnum):
    WATCH = "watch"
    ACTIONABLE = "actionable"
    INVALIDATION = "invalidation"
    EXIT = "exit"
    INCIDENT = "incident"
    NO_RECOMMENDATION = "no_recommendation"
    RESEARCH = "research"
    DAILY_SUMMARY = "daily_summary"


class HermesDeliveryEvent(BaseModel, frozen=True):
    delivery_id: str
    root_delivery_id: str
    kind: HermesDeliveryKind
    source_event_id: str
    market_id: str
    lane_id: str | None
    occurred_at: AwareDatetime
    payload_sha256: str
    rendered_text: str
```

The schema must reject UPDATE and DELETE on immutable event/transition tables, use mode `600`, require a non-blocking writer lease, and derive `delivery_id` from source identity plus delivery contract version.

- [ ] **Step 4: Run focused tests and SQLite integrity probes**

Run: `uv run pytest -q tests/test_hermes_delivery_store.py`

Expected: PASS with exact replay adding zero rows.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/hermes_delivery_models.py trading_agent/hermes_delivery_store.py tests/test_hermes_delivery_store.py
git commit -m "feat: add durable Hermes delivery lifecycle"
```

## Task 3: Deterministic Projection And Agent Queries

**Files:**
- Create: `trading_agent/hermes_delivery_projection.py`
- Create: `trading_agent/hermes_query_service.py`
- Create: `run_hermes_delivery.py`
- Create: `tests/test_hermes_delivery_e2e.py`
- Create: `tests/test_hermes_query_service.py`

- [ ] **Step 1: Write projection and opinion-separation tests**

```python
def test_query_returns_separate_agent_opinions_without_blended_verdict(tmp_path: Path) -> None:
    result = query_service_fixture(tmp_path).query("AAPL", observed_at=AT)
    assert [item.agent_family for item in result.opinions] == [
        "opportunity_manager",
        "market_context",
        "day_trading",
        "swing_trading",
    ]
    assert result.blended_verdict is None
```

Add E2E coverage for watch to actionable to exit reply lineage, no-recommendation, incident, research result, and daily summary. Projection must read `contract_outbox.py` and existing terminal stores but never modify them.

- [ ] **Step 2: Run tests and observe failure**

Run: `uv run pytest -q tests/test_hermes_delivery_e2e.py tests/test_hermes_query_service.py`

Expected: FAIL because projection and query services are absent.

- [ ] **Step 3: Implement read-only projection and query result contracts**

```python
class AgentOpinion(BaseModel, frozen=True):
    agent_family: AgentFamily
    lane_id: str | None
    strategy_version: str | None
    status: str
    observed_at: AwareDatetime
    evidence_refs: tuple[str, ...]
    summary: str


class HermesAgentQueryResult(BaseModel, frozen=True):
    instrument_id: str | None
    observed_at: AwareDatetime
    opinions: tuple[AgentOpinion, ...]
    blended_verdict: None = None
```

The CLI prints only redacted JSON. Unknown symbols, stale projections, and incomplete source coverage return explicit blocked opinions rather than empty success.

- [ ] **Step 4: Run focused tests and manual CLI QA**

Run: `uv run pytest -q tests/test_hermes_delivery_e2e.py tests/test_hermes_query_service.py`

Run: `uv run python run_hermes_delivery.py --help`

Run one fixture `query` happy path and one malformed-path command; expected results are redacted JSON success and exit code 2 without traceback respectively.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/hermes_delivery_projection.py trading_agent/hermes_query_service.py run_hermes_delivery.py tests/test_hermes_delivery_e2e.py tests/test_hermes_query_service.py
git commit -m "feat: project agent outcomes for Hermes"
```

## Task 4: Installable Hermes Plugin And Query Tools

**Files:**
- Create: `integrations/hermes/trading-agent/plugin.yaml`
- Create: `integrations/hermes/trading-agent/__init__.py`
- Create: `integrations/hermes/trading-agent/skills/trading-agent/SKILL.md`
- Create: `tests/test_hermes_plugin_contract.py`

- [ ] **Step 1: Write fake-context plugin tests**

```python
def test_plugin_registers_query_and_arm_tools(fake_context: FakePluginContext) -> None:
    plugin = load_plugin_module()
    plugin.register(fake_context)
    assert set(fake_context.tool_names) == {
        "trading_agent_query",
        "trading_agent_status",
        "trading_agent_arm_prepare",
        "trading_agent_arm_confirm",
        "trading_agent_arm_revoke",
    }
```

Verify tool handlers derive the caller from Hermes session context, accept no broker URL or credential arguments, and execute only allow-listed project CLIs under the configured absolute project root.

- [ ] **Step 2: Run the plugin test and observe failure**

Run: `uv run pytest -q tests/test_hermes_plugin_contract.py`

Expected: FAIL because the plugin package is absent.

- [ ] **Step 3: Add the versioned manifest and plugin registration**

```yaml
name: trading-agent
version: 1.0.0
description: "Read-only agent queries, durable alerts, and signed Paper arm routing."
author: trading-recommendation-agent
kind: standalone
provides_tools:
  - trading_agent_query
  - trading_agent_status
  - trading_agent_arm_prepare
  - trading_agent_arm_confirm
  - trading_agent_arm_revoke
requires_env:
  - name: TRADING_AGENT_PROJECT_ROOT
    description: "Absolute path to the active clean trading-recommendation-agent checkout"
    prompt: "Trading agent project root"
    password: false
```

Register the skill with `ctx.register_skill`, tools with `ctx.register_tool`, and a `/trading-status` slash command. The plugin must not expose a generic shell command or import Alpaca mutation clients.

- [ ] **Step 4: Verify real installer discovery without enabling delivery**

Run against a temporary Hermes home:

```bash
HERMES_HOME="$(mktemp -d)" \
TRADING_AGENT_PROJECT_ROOT="/Users/goyunseo/work/trading-recommendation-agent" \
hermes plugins install \
  "file:///Users/goyunseo/work/trading-recommendation-agent#integrations/hermes/trading-agent" \
  --enable
```

Expected: plugin installs as `trading-agent`; no credentials are printed and no Telegram or broker call occurs.

- [ ] **Step 5: Commit**

```bash
git add integrations/hermes/trading-agent tests/test_hermes_plugin_contract.py
git commit -m "feat: add installable Hermes trading agent plugin"
```

## Task 5: Automatic Telegram Delivery With Acknowledgement

**Files:**
- Create: `integrations/hermes/trading-agent/delivery_worker.py`
- Create: `tests/test_hermes_plugin_delivery.py`
- Modify: `integrations/hermes/trading-agent/__init__.py`

- [ ] **Step 1: Write delivery, retry, reply, and restart tests**

```python
def test_worker_acknowledges_telegram_message_and_replies_to_root(tmp_path: Path) -> None:
    sender = FakeTelegramSender(message_ids=("100", "101"))
    worker = plugin_worker_fixture(tmp_path, sender=sender)
    worker.tick()
    worker.tick()
    assert sender.calls[1].reply_to_message_id == "100"
    assert worker.store.acknowledgements()[-1].platform_message_id == "101"
```

Also prove timeout retry keeps the same `delivery_id`, a crash after Telegram ACK recovers by platform message identity, terminal rejection becomes a dead letter, and two worker instances cannot own the same lease.

- [ ] **Step 2: Run tests and observe failure**

Run: `uv run pytest -q tests/test_hermes_plugin_delivery.py`

Expected: FAIL because the worker is absent.

- [ ] **Step 3: Implement a bounded plugin-owned worker**

```python
class HermesDeliveryWorker:
    def tick(self) -> DeliveryTickResult:
        claim = self._store.claim_next(self._worker_id, self._clock(), lease_seconds=30)
        if claim is None:
            return DeliveryTickResult.idle()
        result = self._sender.send(
            text=claim.event.rendered_text,
            reply_to_message_id=self._store.root_platform_message_id(claim.event.root_delivery_id),
        )
        self._store.acknowledge(claim, result.message_id, self._clock())
        return DeliveryTickResult.acknowledged(claim.event.delivery_id)
```

Only the Hermes plugin may read Hermes Telegram configuration. The trading repository receives only the redacted acknowledgement identity and never reads or logs the bot token or chat ID. Start one daemon worker during plugin registration; stop is process-bound and all claims expire safely after crash.

- [ ] **Step 4: Verify fixture E2E and a disabled real-plugin status check**

Run: `uv run pytest -q tests/test_hermes_plugin_delivery.py tests/test_hermes_delivery_e2e.py`

Run: `hermes plugins list`

Expected: fixture delivery passes; the installed production plugin is not changed during this task unless explicitly configured by the owner.

- [ ] **Step 5: Commit**

```bash
git add integrations/hermes/trading-agent/delivery_worker.py integrations/hermes/trading-agent/__init__.py tests/test_hermes_plugin_delivery.py
git commit -m "feat: deliver trading events through Hermes"
```

## Task 6: Signed One-Use Owner Arm Gateway

**Files:**
- Create: `trading_agent/hermes_arm_request.py`
- Create: `trading_agent/hermes_arm_store.py`
- Create: `trading_agent/hermes_arm_gateway.py`
- Create: `run_hermes_arm_gateway.py`
- Create: `tests/test_hermes_arm_gateway.py`

- [ ] **Step 1: Write authorization and replay tests**

```python
def test_confirmed_arm_is_consumed_once_for_exact_session_lane_and_risk(tmp_path: Path) -> None:
    gateway = gateway_fixture(tmp_path)
    prepared = gateway.prepare(owner="owner-1", session_id="2026-07-22", lane_id=ORB_LANE, risk=FIXED_RISK)
    confirmed = gateway.confirm(owner="owner-1", nonce=prepared.nonce, confirmation=prepared.challenge)
    assert gateway.consume(confirmed.request_id, expected_session="2026-07-22", expected_lane=ORB_LANE) == PaperMutationArm(PAPER_MUTATION_ARM_VALUE)
    with pytest.raises(InvalidHermesArmRequestError, match="consumed"):
        gateway.consume(confirmed.request_id, expected_session="2026-07-22", expected_lane=ORB_LANE)
```

Cover wrong owner, wrong session, wrong lane, altered fixed risk, expired nonce, replayed confirmation, revoked request, dirty commit, account-fingerprint mismatch, and absent current-session champion binding.

- [ ] **Step 2: Run tests and observe failure**

Run: `uv run pytest -q tests/test_hermes_arm_gateway.py`

Expected: FAIL because the arm gateway is absent.

- [ ] **Step 3: Implement deterministic request transitions**

```python
class HermesArmRequest(BaseModel, frozen=True):
    request_id: str
    owner_id_hash: str
    nonce: str
    session_id: str
    lane_id: str
    strategy_version: str
    account_fingerprint: str
    risk_contract_hash: str
    expires_at: AwareDatetime
    commit_sha: str
```

Use an owner-local signing key loaded from a separate mode-600 config path. Store only hashes and signatures, never owner identifiers or key material. `consume` returns the existing `PaperMutationArm` only after all bindings are revalidated and atomically appends the consumed transition. This module imports no HTTP client.

The default signing-key path is `~/.config/trading-agent/hermes-arm.env`; it is a current-user-owned, no-follow regular file with exact mode `600` and contains only `HERMES_ARM_SIGNING_KEY`. Neither the CLI nor reports print the key, owner identity, account fingerprint, nonce, or signature.

- [ ] **Step 4: Run focused tests and CLI QA**

Run: `uv run pytest -q tests/test_hermes_arm_gateway.py`

Run: `uv run python run_hermes_arm_gateway.py --help`

Run one expired fixture request and one exact happy path; expected results are fail-closed exit code 1 and a redacted `confirmed` then `consumed` lifecycle with no broker call.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/hermes_arm_request.py trading_agent/hermes_arm_store.py trading_agent/hermes_arm_gateway.py run_hermes_arm_gateway.py tests/test_hermes_arm_gateway.py
git commit -m "feat: add signed Hermes Paper arm gateway"
```

## Task 7: US Day Operating Coordinator

**Files:**
- Create: `trading_agent/us_day_operating_coordinator.py`
- Create: `run_us_day_operating_session.py`
- Create: `tests/test_us_day_operating_vertical_e2e.py`

- [ ] **Step 1: Write fake-broker full-lifecycle tests**

```python
def test_us_day_vertical_closes_entry_protection_exit_reconciliation_and_delivery(tmp_path: Path) -> None:
    result = operating_fixture(tmp_path).run_natural_setup()
    assert result.transitions == (
        "actionable",
        "entry_acknowledged",
        "protective_oco_acknowledged",
        "flat",
        "reconciled",
        "hermes_result_projected",
    )
    assert result.broker.open_orders == ()
    assert result.broker.positions == ()
```

Add partial-fill OCO resize, entry rejection, ambiguous timeout targeted recovery, process restart, external account activity, stale bar, stale quote, closed market, risk latch, duplicate arm, and EOD cancel-then-flatten scenarios.

- [ ] **Step 2: Run tests and observe failure**

Run: `uv run pytest -q tests/test_us_day_operating_vertical_e2e.py`

Expected: FAIL because the coordinator is absent.

- [ ] **Step 3: Implement orchestration around the existing sole writer**

```python
class UsDayOperatingCoordinator:
    def run(self, request: UsDayOperatingRequest) -> UsDayOperatingResult:
        arm = self._arm_gateway.consume(
            request.arm_request_id,
            expected_session=request.session_id,
            expected_lane=request.lane_id,
        )
        with open_paper_operating_session(self._credentials, self._execution_store) as session:
            session.recover_mutations()
            admission = session.evaluate_order(request.order_admission)
            entry = session.execute_entry(request.order_admission, arm)
            protection = session.execute_protective_oco(
                entry.approval.sized_order.intent.intent_id,
                arm,
            )
            terminal = self._drive_until_terminal(session, entry, protection, arm)
        return self._finalize_and_project(terminal)
```

Do not create a second writer or new broker client. Read the parent intent ID from the existing `PaperEntryMutationExecution.approval.sized_order.intent.intent_id`. The coordinator freezes strategy version and arm before mutation, and every mismatch projects an incident or blocked result.

- [ ] **Step 4: Prove the full fake-broker matrix**

Run: `uv run pytest -q tests/test_us_day_operating_vertical_e2e.py tests/test_paper_operating_session.py tests/test_paper_operating_mutation_recovery.py`

Expected: PASS; broker requests target only the exact Alpaca Paper origin in injected contract tests.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/us_day_operating_coordinator.py run_us_day_operating_session.py tests/test_us_day_operating_vertical_e2e.py
git commit -m "feat: close US Day Paper operating vertical"
```

## Task 8: Session Terminal, EOD, And Three-Session Evidence

**Files:**
- Modify: `trading_agent/us_day_operating_coordinator.py`
- Modify: `run_us_day_operating_session.py`
- Modify: `tests/test_us_day_operating_vertical_e2e.py`
- Create: `tests/test_us_day_acceptance_evidence.py`
- Modify: `docs/runbooks/alpaca-paper-first-regular-session-smoke-ko.md`

- [ ] **Step 1: Write terminal and censored-session tests**

```python
def test_ten_censored_sessions_do_not_complete_natural_paper_gate(tmp_path: Path) -> None:
    report = build_three_session_report(censored_sessions(tmp_path, count=10))
    assert report.delivery_subgate_passed is True
    assert report.natural_paper_lifecycle_passed is False
    assert report.operating_product_complete is False
```

Also require one daily terminal per scheduled session, open order zero, position zero, broker-shadow-ledger equality, and an explicit reason for no setup or every blocked session.

- [ ] **Step 2: Run tests and observe failure**

Run: `uv run pytest -q tests/test_us_day_acceptance_evidence.py tests/test_us_day_operating_vertical_e2e.py`

Expected: FAIL because session aggregation and evidence output are absent.

- [ ] **Step 3: Implement terminal aggregation and evidence build**

The `evidence` command must write:

```text
outputs/acceptance/us_day/three_session_report.json
outputs/acceptance/us_day/natural_paper_lifecycle.json
outputs/acceptance/us_day/final_reconciliation.json
outputs/acceptance/us_day/hermes_outcome_receipt.json
outputs/acceptance/day/manifest.json
```

Every JSON file includes the exact clean commit, NYSE session ID, policy version, source artifact hashes, and fixture label. Only real scheduled sessions are eligible for the acceptance manifest.

- [ ] **Step 4: Update the runbook with exact safe commands**

Document `preflight`, Telegram prepare/confirm, `run`, `recover`, `finalize`, and `evidence`. State that a closed market, no natural setup, missing current minute bar, stale quote, or any reconciliation mismatch ends without forcing a POST.

- [ ] **Step 5: Run focused tests and commit**

Run: `uv run pytest -q tests/test_us_day_acceptance_evidence.py tests/test_us_day_operating_vertical_e2e.py`

```bash
git add trading_agent/us_day_operating_coordinator.py run_us_day_operating_session.py tests/test_us_day_acceptance_evidence.py tests/test_us_day_operating_vertical_e2e.py docs/runbooks/alpaca-paper-first-regular-session-smoke-ko.md
git commit -m "feat: attest US Day operating sessions"
```

## Task 9: Stage 1 Verification And Operational Rollout

**Files:**
- Modify: `README.md`
- Modify: `docs/milestones_status_ko.md`
- Create: `docs/checkpoints/2026-07-22-hermes-us-day-operating-product-ko.md`

- [ ] **Step 1: Run the complete automated gate once**

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
uv run python -m compileall -q trading_agent integrations/hermes/trading-agent
```

Expected: all commands exit 0. Record exact counts and commit SHA in the checkpoint; do not copy secrets, account IDs, or Telegram IDs.

- [ ] **Step 2: Run CLI manual QA**

```bash
uv run python run_acceptance_evidence.py --help
uv run python run_hermes_delivery.py --help
uv run python run_hermes_arm_gateway.py --help
uv run python run_us_day_operating_session.py --help
```

Run one bad input and one fixture happy path for every CLI. Verify stderr and reports contain no secret, account fingerprint, local owner identifier, or raw authentication response.

- [ ] **Step 3: Install the plugin from the immutable pushed commit**

```bash
hermes plugins install \
  kysk2295/trading-recommendation-agent/integrations/hermes/trading-agent \
  --enable
hermes gateway restart
hermes plugins list
```

Expected: version `1.0.0` is enabled and its five tools are discoverable. Installation occurs only after the plugin commit is pushed and reviewed.

- [ ] **Step 4: Perform Telegram query and delivery QA**

From the owner-allowlisted Telegram chat, request current status and one symbol query. Inject one fixture delivery only into a dedicated QA store, verify one Telegram root message and one reply message, then verify both platform message IDs are acknowledged in the QA store. No production signal, arm, or broker mutation is used for this test.

- [ ] **Step 5: Collect real-session evidence without forcing market activity**

For each scheduled NYSE session, run the clean-commit preflight before open and allow the deterministic coordinator to produce recommendation, no-recommendation, or incident. Arm only the exact approved session. If no natural eligible setup occurs, record `censored_no_setup`; never loosen thresholds or submit a synthetic order.

- [ ] **Step 6: Verify AC-001 and the US subgate of AC-002**

```bash
uv run python -m trading_agent.acceptance_evidence verify \
  --criterion AC-001 \
  --manifest outputs/acceptance/hermes/manifest.json \
  --require-clean-commit \
  --require-session-binding

uv run python -m trading_agent.acceptance_evidence verify \
  --criterion AC-002 \
  --manifest outputs/acceptance/day/manifest.json \
  --require-clean-commit \
  --require-session-binding
```

AC-002 remains incomplete until the later KR child plan also contributes its independent three-session subgate. The US subgate remains incomplete without one natural entry to protection to flat to reconciliation to Hermes lifecycle.

- [ ] **Step 7: Update status documents and commit the checkpoint**

Record observed facts only: session dates, redacted statuses, manifest hashes, test counts, and unresolved subgates. Never claim strategy profitability from Paper or shadow evidence.

```bash
git add README.md docs/milestones_status_ko.md docs/checkpoints/2026-07-22-hermes-us-day-operating-product-ko.md
git commit -m "docs: record Hermes and US Day operating evidence"
git push origin main
```

## Stage Transition Rules

1. Stage 2 may begin after Hermes delivery identity and acknowledgement are stable, even if the natural US setup is censored; however, `operating_product_v1` remains incomplete.
2. Stage 3 begins only after US and KR each emit reliable daily terminal events.
3. Stage 4 may proceed during a ten-session US no-setup censoring window because it uses a separate Swing state machine and shadow authority.
4. Stage 5 cannot promote anything until a full challenger path and prospective Reviewer policy exist. Existing lifecycle history is never rewritten.
5. Stages 6 and 7 add a provider only after a challenger proves the missing capability and entitlement is recorded.
6. Stage 8 remains disabled until two distinct non-composite executable champions have separate forward evidence and Paper-eligible authority.

## Plan Self-Review

- **Spec coverage:** AC-001 and the US subgate of AC-002 map to Tasks 1-9. AC-002 KR and AC-003 through AC-007 map to independently gated child plans in delivery order.
- **No speculative platform:** Stage 1 reuses `ContractOutbox`, current recommendation cards, `PaperOperatingSession`, OCO/recovery/safety state machines, experiment identity, and existing LaunchAgent patterns.
- **Authority:** Hermes owns Telegram credentials and messaging only. The trading command gateway owns arm validation. The existing sole writer owns every Alpaca Paper mutation.
- **Observable completion:** Fixture tests prove behavior before deployment; real-session manifests, Telegram acknowledgements, and broker reconciliation decide operational completion.
- **Resource bounds:** No full-universe backtest is part of Stage 1. Later research keeps one heavy-work lease and the 10 GiB RSS stop.
