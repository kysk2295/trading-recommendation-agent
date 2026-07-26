# Dashboard v2 Vertical Implementation Plan

> Execution source of truth: `.omo/plans/dashboard-v2.md`. This document expands Todo 1 into
> file-level vertical sequencing; it does not replace the approved OpenAgent plan.

**Goal:** implement and deploy the nine-workspace Ember Operations Workstation for exactly six
LLM-backed research families with persistent conversation, user-directed tool work and autonomous
research, using strict redacted projections while preserving public reads, private operator
commands, Paper-only safety, per-claim at-most-once paid execution, event-driven delivery, and
rolling v1 rollback.

**Architecture:** Python owns point-in-time read models and redaction. Hono/Postgres accepts strict
rolling-compatible envelopes, stores canonical v2 separately from a retained v1 rollback
projection, and emits one canonical v2 viewer stream. Vanilla TypeScript renders one fixed shell,
reusable source-state primitives, and accessible Evidence Trace. `watchfiles` and one WebSocket
remain the only idle update mechanism.

## 1. Rules for every vertical

### TDD loop

For each step:

1. add the smallest named failing test/fixture;
2. run the exact targeted command and capture its expected nonzero exit plus the intended assertion;
3. implement only the behavior under test;
4. rerun the same command to green;
5. run the full vertical gate after refactor;
6. capture artifact output under the parent Todo's fixed directory:
   `.omo/evidence/dashboard-v2/task-2/` through `task-14/` as mapped by the dependency table.

Do not weaken an assertion, add sleeps, invent fixture success data, or treat a missing source as
empty. Strict test fixtures label all demonstration/showcase values and never enter production
storage.

### Common commands

```bash
cd dashboard
bun run typecheck
bun run lint
bun test
bun run build

cd ..
uv run pytest -q tests/test_dashboard_*.py
uv run ruff check trading_agent/dashboard_*.py tests/test_dashboard_*.py \
  run_dashboard_publisher.py
uv run basedpyright trading_agent/dashboard_*.py tests/test_dashboard_*.py \
  run_dashboard_publisher.py
uv run run_dashboard_publisher.py --help
```

No-excuse audits for changed files:

```bash
rg -n '\bany\b|as [A-Za-z]|!\.|@ts-ignore|@ts-expect-error' dashboard/src dashboard/tests
rg -n '\bAny\b|\bcast\(|# type: ignore|except Exception|except BaseException' \
  trading_agent/dashboard_*.py tests/test_dashboard_*.py run_dashboard_publisher.py
```

Every production browser run uses a production build and real Playwright Chromium/Chrome, not a
dev server and not Lighthouse CLI. The final accessibility harness must run axe and fail on both
`violations` and `incomplete`.

## 2. Dependency and commit order

| Order | Vertical | Depends on | Atomic commit |
| ---: | --- | --- | --- |
| 1 | design contract | none | `docs(dashboard): define dashboard v2 workstation` |
| 2 | v2 schema + rolling store | design contract | `feat(dashboard): add snapshot v2 contract` |
| 3 | primitive showcase + QA harness | design contract, v2 types | `feat(dashboard): build workstation primitives` |
| 4 | read-only projector/watch inventory | v2 schema | `feat(dashboard): project redacted snapshot v2` |
| 5 | nine-workspace shell + Evidence Trace | v2 types, primitives | `feat(dashboard): add nine-workspace shell` |
| 6 | six-family dual-channel control plane | v2 schema/projector | `feat(research): add autonomous agent control plane` |
| 7 | persistent Command Center + directed jobs | control plane, shell | `feat(dashboard): persist Hermes agent sessions` |
| 8 | Markets + Data Sources | projector, shell | `feat(dashboard): connect markets and data sources` |
| 9 | Research + Strategies | control plane, projector, shell | `feat(dashboard): connect research and strategies` |
| 10 | Derivatives + Paper | projector, shell | `feat(dashboard): connect derivatives and paper` |
| 11 | System | control plane, projector, shell | `feat(dashboard): connect system operations` |
| 12 | cross-surface hardening | all workspaces | `test(dashboard): harden dashboard v2 release gates` |
| 13 | rolling live rollout evidence | hardening | `docs(dashboard): record dashboard v2 deployment` |
| 14 | v1 ingest removal | live rollback/recovery proof | `refactor(dashboard): complete snapshot v2 migration` |

Shared files (`dashboard/src/schema.ts`, `render.ts`, `client.ts`, `store.ts`,
`trading_agent/dashboard_models_v2.py`, `dashboard_snapshot_v2.py`) change only in the earliest
owning commit that needs the contract. Later verticals import them and do not redefine schema or
render primitives. If a genuine shared addition is required, serialize it before parallel
vertical work and keep its direct tests in the same atomic commit.

## 3. Vertical 1 — snapshot v2 and rolling storage

**Exact files**

- Modify: `dashboard/src/schema.ts`, `dashboard/src/store.ts`, `dashboard/src/app.ts`,
  `dashboard/src/realtime.ts`.
- Create: `dashboard/src/snapshot_normalizer.ts`.
- Modify: `dashboard/tests/app.test.ts`, `dashboard/tests/realtime.test.ts`,
  `dashboard/tests/interaction_store.test.ts`.
- Create: `dashboard/tests/schema_v2.test.ts`, `dashboard/tests/snapshot_rolling.test.ts`.
- Create: `trading_agent/dashboard_models_v2.py`,
  `tests/test_dashboard_models_v2.py`.
- Modify: `trading_agent/dashboard_models.py`, `trading_agent/dashboard_snapshot.py`,
  `tests/test_dashboard_snapshot.py`.

**Red**

```bash
cd dashboard
bun test tests/schema_v2.test.ts tests/snapshot_rolling.test.ts
cd ..
uv run pytest -q tests/test_dashboard_models_v2.py tests/test_dashboard_snapshot.py
```

The intended failures are missing strict v2 parser/model, no canonical normalizer, no separate v1
rollback storage, and no dangling/cycle/count/state rejection.

**Green and refactor**

- Add strict state/workspace/trace/cap/blocker models in both languages.
- Characterize v1 unchanged before adding the discriminated ingest union.
- Normalize v1 once to canonical v2. Store v1 and v2 under separate keys/tables and commit the pair
  atomically.
- Down-project accepted v2 to bounded v1 rollback data. Reject mixed/unknown schema and delayed v1
  overwrite after the v2 publisher epoch.
- Emit canonical v2 to new viewers while retaining characterized compatibility behavior until
  cutover.

```bash
cd dashboard
bun run check
bun run build
cd ..
uv run pytest -q tests/test_dashboard_models_v2.py tests/test_dashboard_snapshot.py
uv run ruff check trading_agent/dashboard_models.py trading_agent/dashboard_models_v2.py \
  trading_agent/dashboard_snapshot.py tests/test_dashboard_models_v2.py \
  tests/test_dashboard_snapshot.py
uv run basedpyright trading_agent/dashboard_models.py trading_agent/dashboard_models_v2.py \
  trading_agent/dashboard_snapshot.py tests/test_dashboard_models_v2.py \
  tests/test_dashboard_snapshot.py
```

**Failure QA**

- unknown top-level/section fields, invalid count metadata, naive/future timestamps, mixed schema,
  dangling/cyclic trace, invalid terminal, oversized text/rows, v1-after-v2 overwrite, and partial
  dual-write all return typed failure with prior storage unchanged;
- restart reads the same paired v1/v2 snapshots.

## 4. Vertical 2 — primitive showcase and browser harness

**Exact files**

- Modify: `dashboard/public/showcase.html`.
- Create: `dashboard/public/assets/fonts/PretendardVariable.woff2`,
  `IBMPlexMono-Regular.woff2`, `IBMPlexMono-Medium.woff2`,
  `IBMPlexMono-SemiBold.woff2`.
- Create: `dashboard/public/assets/licenses/Pretendard-LICENSE.txt`,
  `IBM-Plex-LICENSE.txt`, `dashboard/public/assets/fonts/manifest.json`.
- Modify: `dashboard/public/assets/base.css`, `layout.css`, `components.css`,
  `data-components.css`, `showcase.css`, `responsive.css`.
- Create: `dashboard/src/source_state.ts`, `dashboard/src/evidence_trace.ts`,
  `dashboard/src/showcase.ts`.
- Create: `dashboard/tests/source_state.test.ts`, `dashboard/tests/evidence_trace.test.ts`,
  `dashboard/tests/font_assets.test.ts`, `dashboard/tests/showcase.test.ts`.
- Create: `dashboard/tests/e2e/showcase.spec.ts`, `dashboard/playwright.config.ts`,
  `dashboard/scripts/run-browser-qa.ts`.
- Modify: `dashboard/package.json`, `dashboard/bun.lock`, `dashboard/biome.json`.

**Red**

```bash
cd dashboard
bun test tests/source_state.test.ts tests/evidence_trace.test.ts \
  tests/font_assets.test.ts tests/showcase.test.ts
```

Expected failures prove the eight source states, demonstration labels, font/license/hash manifest,
trace focus contract, and bounded primitives do not yet exist.

**Green and refactor**

- Check in exact same-origin font binaries and unmodified OFL licenses with upstream tag and
  SHA-256 manifest. Preload only Pretendard Variable and IBM Plex Mono Regular.
- Build every primitive/state from `dashboard/DESIGN.md`; all showcase values carry visible and
  machine-readable `DEMONSTRATION ONLY`.
- Add Playwright plus axe as dev-only QA dependencies; do not ship them in the production bundle.

```bash
cd dashboard
bun run check
bun run build
bun run scripts/run-browser-qa.ts --route /showcase --widths 375,768,1280 \
  --axe --keyboard --reduced-motion --zoom 200
```

Captured artifacts: three screenshots, accessibility JSON, keyboard/focus transcript, reduced
motion computed-style JSON, font network/hash report, and server cleanup receipt.

## 5. Vertical 3 — read-only projector and stable watch inventory

**Exact files**

- Create: `trading_agent/dashboard_snapshot_v2.py`,
  `dashboard_projection_common.py`, `dashboard_projection_agents.py`,
  `dashboard_projection_overview.py`, `dashboard_projection_markets.py`,
  `dashboard_projection_sources.py`, `dashboard_projection_research.py`,
  `dashboard_projection_strategies.py`, `dashboard_projection_derivatives.py`,
  `dashboard_projection_paper.py`, `dashboard_projection_system.py`,
  `dashboard_system_evidence.py`.
- Modify: `run_dashboard_publisher.py`.
- Create: `tests/test_dashboard_snapshot_v2.py`,
  `test_dashboard_projection_sources.py`, `test_dashboard_projection_research.py`,
  `test_dashboard_projection_strategies.py`, `test_dashboard_projection_derivatives.py`,
  `test_dashboard_projection_paper.py`, `test_dashboard_projection_system.py`,
  `test_dashboard_watch_roots.py`, `test_dashboard_redaction.py`,
  `tests/dashboard_v2_cli_harness.py`.

These projector modules import only read-only readers/models named in the master matrix. They do
not import provider credential loaders, provider mutation clients, writable broker adapters, or
generic raw-log readers.

**Red**

```bash
uv run pytest -q tests/test_dashboard_snapshot_v2.py \
  tests/test_dashboard_projection_sources.py \
  tests/test_dashboard_projection_research.py \
  tests/test_dashboard_projection_strategies.py \
  tests/test_dashboard_projection_derivatives.py \
  tests/test_dashboard_projection_paper.py \
  tests/test_dashboard_projection_system.py \
  tests/test_dashboard_watch_roots.py tests/test_dashboard_redaction.py
```

Expected failures: no v2 orchestration, source-state distinction, declared cap metadata,
point-in-time join, stable-root event, or recursive redaction.

**Green and refactor**

- Implement the field matrix without opening credentials/network/provider clients.
- Inventory all stable roots even when a child source does not yet exist; fall back to the
  configured outputs parent only while establishing a missing parent.
- Coalesce one filesystem burst into one rebuild/event. Preserve section-local state and same
  snapshot epoch.
- Replace current weekday/clock market inference with authoritative calendar/session receipts.

```bash
uv run pytest -q tests/test_dashboard_snapshot_v2.py \
  tests/test_dashboard_projection_*.py tests/test_dashboard_watch_roots.py \
  tests/test_dashboard_redaction.py tests/test_dashboard_publisher_cli.py
uv run ruff check trading_agent/dashboard_*.py tests/test_dashboard_*.py \
  run_dashboard_publisher.py
uv run basedpyright trading_agent/dashboard_*.py tests/test_dashboard_*.py \
  run_dashboard_publisher.py
uv run tests/dashboard_v2_cli_harness.py help
uv run tests/dashboard_v2_cli_harness.py invalid-source
uv run tests/dashboard_v2_cli_harness.py happy
```

The harness invokes `run_dashboard_publisher.py` through an argv array and creates its mode-0600
localhost credential fixture inside an isolated temporary directory. Binary observables are exact:

- `help` exits 0 and prints `CLI_HELP_OK publisher_exit=0`;
- `invalid-source` injects a symlink, a world-readable file, malformed/mixed-epoch/future-dated
  receipts, and raw-path/header/account/session canaries; it exits 0 only after the publisher exits
  2, emits `CLI_INVALID_OK publisher_exit=2 snapshot_writes=0 events=0`, and leaks none of the
  canaries;
- `happy` exits 0 and prints
  `CLI_HAPPY_OK publisher_exit=0 schema_version=2 redacted=true events=1`;
- cleanup removes the temporary credential/source tree and prints
  `CLI_CLEANUP_OK files=0 processes=0`.

## 6. Vertical 4 — nine-workspace shell and Evidence Trace

**Exact files**

- Rewrite: `dashboard/public/index.html`.
- Modify: `dashboard/src/client.ts`, `workspace_tabs.ts`, `render.ts`, `dom.ts`,
  `realtime_client.ts`.
- Create: `dashboard/src/workstation_shell.ts`, `dashboard/src/workspace_registry.ts`,
  `dashboard/src/workspaces/command_center.ts`, `overview.ts`, `markets.ts`,
  `data_sources.ts`, `research.ts`, `strategies.ts`, `derivatives.ts`, `paper.ts`, `system.ts`.
- Modify: `dashboard/public/assets/layout.css`, `components.css`, `data-components.css`,
  `workspace.css`, `responsive.css`.
- Modify: `dashboard/tests/app.test.ts`.
- Create: `dashboard/tests/workspace_registry.test.ts`,
  `workspace_tabs_v2.test.ts`, `render_states.test.ts`, `trace_traversal.test.ts`,
  `dashboard/tests/e2e/workstation-shell.spec.ts`.

**Red**

```bash
cd dashboard
bun test tests/workspace_registry.test.ts tests/workspace_tabs_v2.test.ts \
  tests/render_states.test.ts tests/trace_traversal.test.ts
```

Expected failures establish default `#command-center`, nine exact hashes, scroll owner, route
history, all state renderers, non-dangling traces, and drawer focus are absent.

**Green and browser gate**

```bash
cd dashboard
bun run check
bun run build
bun run scripts/run-browser-qa.ts --route / --widths 375,768,1280 \
  --all-workspaces --all-states --trace-traversal --axe --keyboard \
  --reduced-motion --zoom 200
```

The harness reloads every hash, exercises back/forward and Arrow/Home/End, verifies exactly one
visible main and one vertical workspace scroll owner, opens at least one trace control in each
workspace by mouse and keyboard, checks source-to-terminal resolution, traps/returns focus, closes
on Escape, and stresses long CJK/unbroken SHA/table content. An invalid hash safely selects
`#command-center`. Missing trace renders corrupt/unavailable and never throws.

## 7. Vertical 5 — six-family dual-channel agent control plane

**Exact files**

- Create: `trading_agent/dashboard_agent_registry.py`,
  `dashboard_agent_control_plane.py`, `dashboard_autonomous_research.py`,
  `dashboard_agent_tool_jobs.py`.
- Modify: `trading_agent/dashboard_models_v2.py`,
  `dashboard_projection_agents.py`, `dashboard_snapshot_v2.py`,
  `run_dashboard_publisher.py`.
- Create: `tests/test_dashboard_agent_registry.py`,
  `test_dashboard_agent_control_plane.py`, `test_dashboard_autonomous_research.py`,
  `test_dashboard_agent_tool_jobs.py`.
- Modify: `tests/test_dashboard_models_v2.py`,
  `test_dashboard_projection_agents.py`, `test_dashboard_snapshot_v2.py`,
  `test_dashboard_publisher_cli.py`.
- Modify: `dashboard/src/schema.ts`, `realtime.ts`, `store.ts`,
  `workspaces/command_center.ts`, `workspaces/research.ts`,
  `workspaces/strategies.ts`, `workspaces/system.ts`.
- Create: `dashboard/tests/agent_control_plane.test.ts`,
  `dashboard/tests/e2e/agent-channels.spec.ts`.

**Red**

```bash
uv run pytest -q tests/test_dashboard_agent_registry.py \
  tests/test_dashboard_agent_control_plane.py \
  tests/test_dashboard_autonomous_research.py \
  tests/test_dashboard_agent_tool_jobs.py
cd dashboard
bun test tests/agent_control_plane.test.ts
```

Expected failures prove the registry does not yet enforce exactly the six IDs, launchd groups can
be mistaken for identity, the three capability flags and two channels are absent, and there is no
strict autonomous trigger/claim/policy/worktree/tool/receipt contract.

**Green and fault-injection QA**

- Register exactly `opportunity_manager`, `day_trading`, `swing_trading`, `systematic_quant`,
  `derivatives_research`, `market_context`; reject missing/extra/duplicate IDs. Keep
  `allocation_manager` absent until persisted authority proves at least two independent champions.
  Reject KR theme/US intraday/US systematic/US swing/research/delivery launchd aliases;
  `delivery` is never an agent.
- Prove every family advertises persistent conversation, directed tool execution and autonomous
  research. Reviewer, Lifecycle, Execution and Loop Engineer remain typed control-plane roles.
- Implement `AutonomousTriggerV1` with the exact addendum fields and only the five authorized types.
  Claim `(family, policy_version, dedupe_key)` durably before launch. Invalid, unauthorized,
  duplicate, budget-exhausted, cooldown, concurrency and rolling-failure cases launch zero models
  and append a typed terminal/blocker receipt.
- Run each accepted autonomous task in a pinned isolated git worktree/experiment environment with
  allowlisted roots/tools/network, append-only step/evidence/result/cleanup receipts and outbound
  redaction. The integration worktree must remain byte-for-byte unchanged by the fixture task.
- Inject crash at trigger authorization, claim, process launch, tool step, result persistence and
  event send. Each claim launches at most one process, terminates `failed|uncertain`, and never
  performs automatic paid retry.
- Block promotion without Independent Reviewer plus lifecycle decisions. Assert forbidden provider
  mutation and live-money calls are zero; assert Alpaca Paper mutation is zero without the existing
  Paper gate chain.

```bash
uv run pytest -q tests/test_dashboard_agent_registry.py \
  tests/test_dashboard_agent_control_plane.py \
  tests/test_dashboard_autonomous_research.py \
  tests/test_dashboard_agent_tool_jobs.py \
  tests/test_dashboard_models_v2.py tests/test_dashboard_projection_agents.py \
  tests/test_dashboard_snapshot_v2.py tests/test_dashboard_publisher_cli.py
uv run ruff check trading_agent/dashboard_agent_registry.py \
  trading_agent/dashboard_agent_control_plane.py \
  trading_agent/dashboard_autonomous_research.py \
  trading_agent/dashboard_agent_tool_jobs.py \
  trading_agent/dashboard_models_v2.py trading_agent/dashboard_projection_agents.py \
  trading_agent/dashboard_snapshot_v2.py run_dashboard_publisher.py \
  tests/test_dashboard_agent_*.py tests/test_dashboard_autonomous_research.py
uv run basedpyright trading_agent/dashboard_agent_registry.py \
  trading_agent/dashboard_agent_control_plane.py \
  trading_agent/dashboard_autonomous_research.py \
  trading_agent/dashboard_agent_tool_jobs.py \
  trading_agent/dashboard_models_v2.py trading_agent/dashboard_projection_agents.py \
  trading_agent/dashboard_snapshot_v2.py run_dashboard_publisher.py \
  tests/test_dashboard_agent_*.py tests/test_dashboard_autonomous_research.py
uv run run_dashboard_publisher.py autonomous-agent --help
uv run run_dashboard_publisher.py autonomous-agent \
  --trigger-fixture tests/fixtures/dashboard/invalid-autonomous-trigger.json \
  --dry-run
uv run run_dashboard_publisher.py autonomous-agent \
  --trigger-fixture tests/fixtures/dashboard/authorized-new-data-trigger.json \
  --fake-hermes --dry-run --expect-cleanup
cd dashboard
bun run check
bun run build
bun run scripts/run-browser-qa.ts --route '/#command-center' \
  --agent-channels --routes '/#research,/#strategies,/#system' \
  --axe --keyboard --network-artifact
```

Binary observables are
`AGENT_REGISTRY_OK primary=6 allocation=conditional launchd_aliases=0 capabilities=18`,
`AUTONOMOUS_BLOCKED model_processes=0 receipt=1`,
`AUTONOMOUS_OK claims=1 model_processes=1 duplicate_launches=0 evidence=append_only cleanup=1`,
and `AGENT_CHANNELS_OK conversation=1 directed=1 autonomous=1 leaks=0`.

## 8. Vertical 6 — persistent Command Center and directed tool jobs

**Exact files**

- Create: `trading_agent/dashboard_hermes_sessions.py`,
  `dashboard_execution_claims.py`, `dashboard_outbound_redaction.py`.
- Modify: `trading_agent/dashboard_commands.py`, `dashboard_relay.py`,
  `run_dashboard_publisher.py`.
- Modify: `dashboard/src/agent_workspace.ts`, `operator_client.ts`, `operator_auth.ts`,
  `realtime.ts`, `store.ts`, `schema.ts`, `workspaces/command_center.ts`.
- Create: `tests/test_dashboard_hermes_sessions.py`,
  `test_dashboard_execution_claims.py`, `test_dashboard_outbound_redaction.py`,
  `test_dashboard_exactly_once.py`.
- Modify: `tests/test_dashboard_commands.py`, `test_dashboard_publisher_cli.py`,
  `dashboard/tests/interaction_order.test.ts`, `interaction_store.test.ts`,
  `operator_auth.test.ts`, `realtime.test.ts`.
- Create: `dashboard/tests/e2e/command-center.spec.ts`.

**Red**

```bash
uv run pytest -q tests/test_dashboard_hermes_sessions.py \
  tests/test_dashboard_execution_claims.py tests/test_dashboard_outbound_redaction.py \
  tests/test_dashboard_exactly_once.py tests/test_dashboard_commands.py
cd dashboard
bun test tests/interaction_order.test.ts tests/interaction_store.test.ts \
  tests/operator_auth.test.ts tests/realtime.test.ts
```

Expected failures prove no binding/resume, pre-launch claim, uncertain state, CAS terminal
protection, or final outbound validator.

**Green and fault-injection QA**

- Fake Hermes executable emits a strict first-session result and counts process launches.
- First same-agent submit launches once and creates 0600 binding/claim; second submit launches exact
  `--resume` once; a different agent is isolated; restart retains receipts.
- A research/analysis/hypothesis/experiment/allowed-code message creates a typed directed job whose
  allowlisted steps stream progress, evidence and result; generic text-only completion fails.
- Inject faults at claim, running-send, process-start, process-exit, and terminal-send. Duplicate
  delivery/reconnect always keeps launch count `<=1` and never auto retries.
- Test 0644, symlink, multiple hard link, corrupt/missing resume, unknown agent, timeout, forbidden
  provider/Paper request, and session/path/account/header canaries.

```bash
uv run pytest -q tests/test_dashboard_hermes_sessions.py \
  tests/test_dashboard_execution_claims.py tests/test_dashboard_outbound_redaction.py \
  tests/test_dashboard_exactly_once.py tests/test_dashboard_commands.py \
  tests/test_dashboard_publisher_cli.py
uv run ruff check trading_agent/dashboard_hermes_sessions.py \
  trading_agent/dashboard_execution_claims.py \
  trading_agent/dashboard_outbound_redaction.py trading_agent/dashboard_commands.py \
  trading_agent/dashboard_relay.py run_dashboard_publisher.py tests/test_dashboard_*.py
uv run basedpyright trading_agent/dashboard_hermes_sessions.py \
  trading_agent/dashboard_execution_claims.py \
  trading_agent/dashboard_outbound_redaction.py trading_agent/dashboard_commands.py \
  trading_agent/dashboard_relay.py run_dashboard_publisher.py tests/test_dashboard_*.py
cd dashboard
bun run check
bun run build
bun run scripts/run-browser-qa.ts --route '/#command-center' --command-center \
  --agent-channels --directed-job-stream --axe --keyboard --public-private
```

## 9. Vertical 7 — Markets and Data Sources

**Exact files**

- Modify: `trading_agent/dashboard_projection_markets.py`,
  `dashboard_projection_sources.py`, `dashboard_snapshot_v2.py`.
- Modify: `tests/test_dashboard_projection_sources.py`,
  `test_dashboard_snapshot_v2.py`.
- Modify: `dashboard/src/workspaces/markets.ts`, `data_sources.ts`.
- Create: `dashboard/tests/markets_workspace.test.ts`,
  `data_sources_workspace.test.ts`, `dashboard/tests/e2e/markets-data.spec.ts`.

Fixture coverage includes FRED/ALFRED, Treasury, CFTC, OpenDART, KIS, LS, Alpaca; valid zero,
stale, missing entitlement, collection failure, corrupt receipt, populated/truncated, and
authoritative closed session.

**Red/green**

```bash
uv run pytest -q tests/test_dashboard_projection_sources.py \
  tests/test_dashboard_snapshot_v2.py -k 'markets or sources or calendar or entitlement'
cd dashboard
bun test tests/markets_workspace.test.ts tests/data_sources_workspace.test.ts
cd ..
uv run pytest -q tests/test_dashboard_projection_sources.py \
  tests/test_dashboard_snapshot_v2.py
uv run ruff check trading_agent/dashboard_projection_markets.py \
  trading_agent/dashboard_projection_sources.py tests/test_dashboard_projection_sources.py
uv run basedpyright trading_agent/dashboard_projection_markets.py \
  trading_agent/dashboard_projection_sources.py tests/test_dashboard_projection_sources.py
cd dashboard
bun run check
bun run build
bun run scripts/run-browser-qa.ts --routes '/#markets,/#data-sources' \
  --widths 375,768,1280 --all-states --trace-traversal --axe --keyboard
```

No current quote is rendered without real-time entitlement, allowed redistribution, current
capability health, and freshness. No weekday/clock guess may render a market open.

## 10. Vertical 8 — Research and Strategies

**Exact files**

- Modify: `trading_agent/dashboard_projection_research.py`,
  `dashboard_projection_strategies.py`, `dashboard_snapshot_v2.py`.
- Modify: `tests/test_dashboard_projection_research.py`,
  `test_dashboard_projection_strategies.py`, `test_dashboard_snapshot_v2.py`.
- Modify: `dashboard/src/workspaces/research.ts`, `strategies.ts`.
- Create: `dashboard/tests/research_workspace.test.ts`,
  `strategies_workspace.test.ts`, `dashboard/tests/e2e/research-strategies.spec.ts`.

Fixtures cover a complete source → hypothesis → exact dataset/code SHA → trial → walk-forward and
overfit diagnostic → Reviewer → lifecycle chain, plus every missing stage, conflicting Reviewer,
mixed version, no champion, and Allocation Manager lock.

**Red/green**

```bash
uv run pytest -q tests/test_dashboard_projection_research.py \
  tests/test_dashboard_projection_strategies.py
cd dashboard
bun test tests/research_workspace.test.ts tests/strategies_workspace.test.ts
cd ..
uv run pytest -q tests/test_dashboard_projection_research.py \
  tests/test_dashboard_projection_strategies.py tests/test_dashboard_snapshot_v2.py
uv run ruff check trading_agent/dashboard_projection_research.py \
  trading_agent/dashboard_projection_strategies.py \
  tests/test_dashboard_projection_research.py \
  tests/test_dashboard_projection_strategies.py
uv run basedpyright trading_agent/dashboard_projection_research.py \
  trading_agent/dashboard_projection_strategies.py \
  tests/test_dashboard_projection_research.py \
  tests/test_dashboard_projection_strategies.py
cd dashboard
bun run check
bun run build
bun run scripts/run-browser-qa.ts --routes '/#research,/#strategies' \
  --widths 375,768,1280 --all-states --trace-traversal --axe --keyboard
```

Replay/backtest labels never imply profitability. Missing dataset SHA or Reviewer blocks promotion;
the UI does not infer a champion from score.

## 11. Vertical 9 — Derivatives and Paper

**Exact files**

- Modify: `trading_agent/dashboard_projection_derivatives.py`,
  `dashboard_projection_paper.py`, `dashboard_snapshot_v2.py`.
- Modify: `tests/test_dashboard_projection_derivatives.py`,
  `test_dashboard_projection_paper.py`, `test_dashboard_snapshot_v2.py`.
- Modify: `dashboard/src/workspaces/derivatives.ts`, `paper.ts`.
- Create: `dashboard/tests/derivatives_workspace.test.ts`,
  `paper_workspace.test.ts`, `dashboard/tests/e2e/derivatives-paper.spec.ts`.

Fixtures cover licensed-current, delayed, research-only, missing entitlement, stale/mixed-epoch
surface, option/spot mismatch, futures master/roll/CFTC mismatch, complete finalized Paper ledger,
valid zero positions/orders, incomplete verification, missing OCO, pending reconcile/cutoff/EOD,
and corrupt lifecycle order.

**Red/green**

```bash
uv run pytest -q tests/test_dashboard_projection_derivatives.py \
  tests/test_dashboard_projection_paper.py
cd dashboard
bun test tests/derivatives_workspace.test.ts tests/paper_workspace.test.ts
cd ..
uv run pytest -q tests/test_dashboard_projection_derivatives.py \
  tests/test_dashboard_projection_paper.py tests/test_dashboard_snapshot_v2.py
uv run ruff check trading_agent/dashboard_projection_derivatives.py \
  trading_agent/dashboard_projection_paper.py \
  tests/test_dashboard_projection_derivatives.py \
  tests/test_dashboard_projection_paper.py
uv run basedpyright trading_agent/dashboard_projection_derivatives.py \
  trading_agent/dashboard_projection_paper.py \
  tests/test_dashboard_projection_derivatives.py \
  tests/test_dashboard_projection_paper.py
cd dashboard
bun run check
bun run build
bun run scripts/run-browser-qa.ts --routes '/#derivatives,/#paper' \
  --widths 375,768,1280 --all-states --trace-traversal --axe --keyboard \
  --assert-no-mutation-controls
```

Security QA submits to every public route and expects `404` or `401` with no storage/event change.
No release step calls a broker mutation endpoint.

## 12. Vertical 10 — System

**Exact files**

- Modify: `trading_agent/dashboard_system_evidence.py`,
  `dashboard_projection_system.py`, `dashboard_snapshot_v2.py`,
  `dashboard_relay.py`.
- Modify: `tests/test_dashboard_projection_system.py`,
  `test_dashboard_snapshot_v2.py`.
- Modify: `dashboard/src/workspaces/system.ts`.
- Create: `dashboard/tests/system_workspace.test.ts`,
  `dashboard/tests/e2e/system.spec.ts`.

Fixtures include exactly M0–M10 and typed launchd/deploy/relay receipts. Failure fixtures include
missing milestone authority, stale PID, nonzero exit, arbitrary log success text, secret/path
canaries, unreachable Railway, SHA mismatch, stale relay, and an extra Railway service.

**Red/green**

```bash
uv run pytest -q tests/test_dashboard_projection_system.py \
  tests/test_dashboard_snapshot_v2.py -k system
cd dashboard
bun test tests/system_workspace.test.ts
cd ..
uv run pytest -q tests/test_dashboard_projection_system.py \
  tests/test_dashboard_snapshot_v2.py
uv run ruff check trading_agent/dashboard_system_evidence.py \
  trading_agent/dashboard_projection_system.py \
  tests/test_dashboard_projection_system.py
uv run basedpyright trading_agent/dashboard_system_evidence.py \
  trading_agent/dashboard_projection_system.py \
  tests/test_dashboard_projection_system.py
cd dashboard
bun run check
bun run build
bun run scripts/run-browser-qa.ts --route '/#system' --widths 375,768,1280 \
  --all-states --trace-traversal --axe --keyboard
```

PID without a fresh typed receipt is never healthy. Arbitrary stdout/stderr and prose cannot
establish milestone success.

## 13. Cross-surface browser, accessibility, CLI, security, and cost QA

**Exact files**

- Modify: `dashboard/scripts/run-browser-qa.ts`, `dashboard/playwright.config.ts`,
  `dashboard/package.json`.
- Create: `dashboard/tests/e2e/golden-journey.spec.ts`,
  `accessibility.spec.ts`, `idle-cost.spec.ts`, `security-boundary.spec.ts`,
  `content-stress.spec.ts`.
- Create: `tests/test_dashboard_security_adversarial.py`,
  `test_dashboard_rolling_rollback.py`, `tests/dashboard_v2_railway_harness.py`.
- Create: `dashboard/scripts/live-dashboard-qa.ts`.

**Full gate**

```bash
cd dashboard
bun run check
bun run build
bun run scripts/run-browser-qa.ts --all-workspaces --all-states \
  --widths 375,768,1280 --trace-traversal --axe --keyboard \
  --reduced-motion --zoom 200 --cjk-stress --idle-seconds 300 \
  --public-private --agent-channels --directed-job-stream \
  --authorized-autonomous-trigger --duplicate-trigger-count 2 \
  --blocked-trigger-matrix --network-artifact
cd ..
uv run pytest -q tests/test_dashboard_*.py
uv run ruff check trading_agent/dashboard_*.py tests/test_dashboard_*.py \
  run_dashboard_publisher.py
uv run basedpyright trading_agent/dashboard_*.py tests/test_dashboard_*.py \
  run_dashboard_publisher.py
uv run run_dashboard_publisher.py --help
```

The five-minute true-idle artifact, collected while there is no user input and no authorized
autonomous trigger, must show zero periodic data/DB HTTP requests, zero interactive processes and
zero autonomous processes. This does not prohibit the separately authorized trigger scenario from
calling a model. The security scan recursively checks snapshot/interactions/DOM/localStorage/
sessionStorage and captured logs for credential, account, header, raw payload,
Unix/macOS/Windows path, session ID, worktree path and binding canaries. The browser golden journey
is:

1. keyless public snapshot and all nine workspaces;
2. open/close a trace from every workspace;
3. public command rejected;
4. single-use operator pairing sets `Secure; HttpOnly; SameSite`;
5. one explicit read-only message creates one interaction claim and at most one process with
   queued → running → terminal events;
6. one directed tool message streams allowlisted progress/evidence/result and cannot terminate as
   generic text only;
7. one authorized new-data trigger plus two duplicates creates one autonomous claim and at most
   one process; each invalid/budget/cooldown/concurrency/failure-budget trigger creates zero;
8. autonomous code work leaves the integration worktree unchanged and records isolated-worktree
   cleanup; Reviewer/lifecycle absence changes no promotion authority;
9. reconnect and every interactive/autonomous crash seam create no replacement paid process;
10. reduced motion, keyboard, 200% zoom, and CJK stress remain usable;
11. all browser/server/task processes are terminated and cleanup receipts are captured.

Required output is
`COST_SAFETY_OK idle_http_db=0 idle_interactive=0 idle_autonomous=0 interaction_claims=1 directed_evidence=1 autonomous_claims=1 duplicate_launches=0 blocked_launches=0 paid_retries=0 worktree_leaks=0 promotion_without_review=0 provider_mutations=0 secret_leaks=0`.

## 14. Rolling Railway rollout

Only these discovered targets are valid:

```text
project     ee149dc8-82b8-46e7-8ef7-582400fed6f9
environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9
observatory a7cae053-9289-4120-b5ac-7a0aefc36778
Postgres    21b11148-2386-47a4-b2dd-2a8dfbce94bd
```

The inventory must remain exactly two services. Run every block with shell tracing disabled
(`set +x`). Never invoke `railway variables`, `railway logs`, `env`, `set`, or `printenv`; never
redirect bearer/cookie/header content into evidence. `tests/dashboard_v2_railway_harness.py`
accepts Railway JSON on stdin, retains only allowlisted deployment/service/URL/SHA/status fields,
rejects any environment-variable value field, and writes mode-0600 redacted evidence. Every
invocation selects the exact project, production environment, and `observatory` service IDs; no
command may create a project, environment, service, database, worker, or variable.

**Exact link, environment/service selection, and preflight**

```bash
set +x
git fetch origin main
git merge-base --is-ancestor origin/main HEAD
test -z "$(git status --porcelain=v1)"
railway link \
  --project ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --json
railway environment link 8b37a20f-6b0d-4137-a787-ad90b4b482b9 --json
railway service link a7cae053-9289-4120-b5ac-7a0aefc36778
railway status --json | uv run tests/dashboard_v2_railway_harness.py assert-target \
  --project-id ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment-id 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service-id a7cae053-9289-4120-b5ac-7a0aefc36778
railway service list \
  --project ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --json | uv run tests/dashboard_v2_railway_harness.py assert-services \
  --exact-service-id a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --exact-service-id 21b11148-2386-47a4-b2dd-2a8dfbce94bd
```

The target checker prints exactly
`RAILWAY_TARGET_OK project=ee149dc8-82b8-46e7-8ef7-582400fed6f9 environment=8b37a20f-6b0d-4137-a787-ad90b4b482b9 service=a7cae053-9289-4120-b5ac-7a0aefc36778`.
The inventory checker prints exactly
`RAILWAY_SERVICES_OK count=2 observatory=a7cae053-9289-4120-b5ac-7a0aefc36778 postgres=21b11148-2386-47a4-b2dd-2a8dfbce94bd`.
Any other ID/count, dirty status, missing `origin/main` ancestry, or JSON field outside its
allowlist exits nonzero before push/deploy.

**Exact non-force push, compatibility deploy, wait, status, and live check**

```bash
set +x
DASHBOARD_COMPAT_SHA="$(git rev-parse HEAD)"
git push origin HEAD:main
railway up dashboard --path-as-root --detach --json \
  --project ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --message "dashboard-v2-compat:${DASHBOARD_COMPAT_SHA}" |
  uv run tests/dashboard_v2_railway_harness.py capture-start \
    --expected-sha "$DASHBOARD_COMPAT_SHA" \
    --output .omo/evidence/dashboard-v2/task-13/compat-deploy-start.json
uv run tests/dashboard_v2_railway_harness.py wait \
  --project-id ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment-id 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service-id a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --start-receipt .omo/evidence/dashboard-v2/task-13/compat-deploy-start.json \
  --expected-sha "$DASHBOARD_COMPAT_SHA" --timeout-seconds 900 --poll-seconds 10 \
  --output .omo/evidence/dashboard-v2/task-13/compat-deploy-terminal.json
railway service status \
  --project ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --json | uv run tests/dashboard_v2_railway_harness.py assert-status \
  --expected-sha "$DASHBOARD_COMPAT_SHA" --expected-status SUCCESS \
  --output .omo/evidence/dashboard-v2/task-13/compat-service-status.json
DASHBOARD_V2_URL="$(uv run tests/dashboard_v2_railway_harness.py production-url \
  --status-receipt .omo/evidence/dashboard-v2/task-13/compat-service-status.json)"
curl --fail --silent --show-error "$DASHBOARD_V2_URL/api/health" |
  uv run tests/dashboard_v2_railway_harness.py assert-health \
  --output .omo/evidence/dashboard-v2/task-13/compat-health.json
curl --fail --silent --show-error "$DASHBOARD_V2_URL/api/snapshot" |
  uv run tests/dashboard_v2_railway_harness.py assert-snapshot \
  --accepted-schema 1 --accepted-schema 2 --redacted \
  --output .omo/evidence/dashboard-v2/task-13/compat-snapshot.json
cd dashboard
bun run scripts/live-dashboard-qa.ts --base-url "$DASHBOARD_V2_URL" \
  --expect-viewer-event --expect-public-command-status 401 \
  --expect-private-boundary --idle-seconds 300 \
  --output ../.omo/evidence/dashboard-v2/task-13/compat-live-qa.json
cd ..
```

`git push origin HEAD:main` is deliberately non-force: it contains no `--force`,
`--force-with-lease`, `+refspec`, or history rewrite. `capture-start` prints
`RAILWAY_DEPLOY_STARTED deployment_id=NONEMPTY sha=64_HEX`. `wait` polls only Railway
deployment status, never product HTTP/DB data, and prints
`RAILWAY_DEPLOY_OK status=SUCCESS sha=64_HEX services=2`; FAILED/CRASHED/REMOVED, timeout,
SHA mismatch, or service-count drift exits nonzero. The live QA prints
`LIVE_COMPAT_OK health=200 viewer_events=1 public_command=401 true_idle_data_requests=0 true_idle_interactive=0 true_idle_autonomous=0`.

After compatibility proof, switch the Mac mini publisher to strict v2 with the exact publisher
command from Vertical 3 and rerun `assert-snapshot --accepted-schema 2 --redacted` plus
the following live check using an allowlisted harmless test trigger (no provider or Paper
mutation):

```bash
cd dashboard
bun run scripts/live-dashboard-qa.ts --base-url "$DASHBOARD_V2_URL" \
  --expect-viewer-event --expect-public-command-status 401 \
  --expect-private-boundary --idle-seconds 300 \
  --expect-authorized-autonomous-event \
  --autonomous-trigger-fixture ../tests/fixtures/dashboard/authorized-new-data-trigger.json \
  --duplicate-trigger-count 2 \
  --output ../.omo/evidence/dashboard-v2/task-13/v2-live-agent-qa.json
cd ..
```

The binary observable is
`LIVE_V2_OK schema=2 viewer_events=1 explicit_messages=1 interactive_processes=1 autonomous_events=1 autonomous_claims=1 duplicate_launches=0 leaks=0`.

**Exact compatibility rollback and recovery**

The v2 release receipt records `compatibility_sha` before publisher cutover. Rollback uses a
detached temporary worktree, so it never rewrites or cleans the integration worktree. No provider
command or Paper mutation runs.

```bash
set +x
DASHBOARD_V2_SHA="$(git rev-parse HEAD)"
DASHBOARD_COMPAT_SHA="$(uv run tests/dashboard_v2_railway_harness.py compatibility-sha \
  --receipt .omo/evidence/dashboard-v2/task-13/compat-deploy-terminal.json)"
DASHBOARD_ROLLBACK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/dashboard-v2-rollback.XXXXXX")"
git worktree add --detach "$DASHBOARD_ROLLBACK_ROOT/source" "$DASHBOARD_COMPAT_SHA"
railway up "$DASHBOARD_ROLLBACK_ROOT/source/dashboard" --path-as-root --detach --json \
  --project ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --message "dashboard-v2-rollback:${DASHBOARD_COMPAT_SHA}" |
  uv run tests/dashboard_v2_railway_harness.py capture-start \
    --expected-sha "$DASHBOARD_COMPAT_SHA" \
    --output .omo/evidence/dashboard-v2/task-13/rollback-deploy-start.json
uv run tests/dashboard_v2_railway_harness.py wait \
  --project-id ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment-id 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service-id a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --start-receipt .omo/evidence/dashboard-v2/task-13/rollback-deploy-start.json \
  --expected-sha "$DASHBOARD_COMPAT_SHA" --timeout-seconds 900 --poll-seconds 10 \
  --output .omo/evidence/dashboard-v2/task-13/rollback-deploy-terminal.json
uv run tests/dashboard_v2_railway_harness.py verify-retained-v1 \
  --base-url "$DASHBOARD_V2_URL" --require-no-publisher-republish \
  --output .omo/evidence/dashboard-v2/task-13/rollback-v1-read.json
railway redeploy --from-source --yes --json \
  --project ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service a7cae053-9289-4120-b5ac-7a0aefc36778 |
  uv run tests/dashboard_v2_railway_harness.py capture-start \
    --expected-sha "$DASHBOARD_V2_SHA" \
    --output .omo/evidence/dashboard-v2/task-13/recovery-deploy-start.json
uv run tests/dashboard_v2_railway_harness.py wait \
  --project-id ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment-id 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service-id a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --start-receipt .omo/evidence/dashboard-v2/task-13/recovery-deploy-start.json \
  --expected-sha "$DASHBOARD_V2_SHA" --timeout-seconds 900 --poll-seconds 10 \
  --output .omo/evidence/dashboard-v2/task-13/recovery-deploy-terminal.json
railway service status \
  --project ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --json | uv run tests/dashboard_v2_railway_harness.py assert-status \
  --expected-sha "$DASHBOARD_V2_SHA" --expected-status SUCCESS \
  --output .omo/evidence/dashboard-v2/task-13/recovery-service-status.json
uv run tests/dashboard_v2_railway_harness.py verify-recovery \
  --base-url "$DASHBOARD_V2_URL" --expected-schema 2 \
  --exact-service-id a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --exact-service-id 21b11148-2386-47a4-b2dd-2a8dfbce94bd \
  --output .omo/evidence/dashboard-v2/task-13/recovery-live.json
git worktree remove "$DASHBOARD_ROLLBACK_ROOT/source"
rmdir "$DASHBOARD_ROLLBACK_ROOT"
```

Required observables are
`ROLLBACK_OK retained_v1=true publisher_republish=0 services=2` and
`RECOVERY_OK schema=2 health=200 viewer_events=1 services=2`. An extra service, SHA mismatch,
missing retained v1, public command success, leakage, dirty integration worktree, or failed
temporary-worktree cleanup blocks release.

## 15. Remove v1 ingest acceptance

This begins only after the live compatibility rollback and v2 recovery artifacts pass.

**Exact files**

- Modify: `dashboard/src/schema.ts`, `snapshot_normalizer.ts`, `store.ts`, `app.ts`,
  `realtime.ts`.
- Modify: `dashboard/tests/schema_v2.test.ts`, `snapshot_rolling.test.ts`, `app.test.ts`,
  `realtime.test.ts`.
- Modify: `trading_agent/dashboard_snapshot.py`, `dashboard_snapshot_v2.py`.
- Modify: `tests/test_dashboard_snapshot.py`, `test_dashboard_rolling_rollback.py`.

**Red**

Add assertions that v1 and unknown ingest return 400 without overwriting current v2 while the
retained v1 down-projection remains readable by the documented rollback reader:

```bash
cd dashboard
bun test tests/schema_v2.test.ts tests/snapshot_rolling.test.ts \
  tests/app.test.ts tests/realtime.test.ts
cd ..
uv run pytest -q tests/test_dashboard_snapshot.py \
  tests/test_dashboard_rolling_rollback.py
```

**Green, deploy, verify**

Remove only temporary v1 input/normalization acceptance. Keep the bounded v1 down-projector,
historical documentation, retained rollback storage, and explicit v1 rejection tests.

```bash
cd dashboard
bun run check
bun run build
cd ..
uv run pytest -q tests/test_dashboard_*.py
uv run ruff check trading_agent/dashboard_*.py tests/test_dashboard_*.py \
  run_dashboard_publisher.py
uv run basedpyright trading_agent/dashboard_*.py tests/test_dashboard_*.py \
  run_dashboard_publisher.py
```

After commit `refactor(dashboard): complete snapshot v2 migration`, run the exact non-force final
push/deploy/status/live gate:

```bash
set +x
DASHBOARD_FINAL_SHA="$(git rev-parse HEAD)"
git push origin HEAD:main
railway up dashboard --path-as-root --detach --json \
  --project ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --message "dashboard-v2-final:${DASHBOARD_FINAL_SHA}" |
  uv run tests/dashboard_v2_railway_harness.py capture-start \
    --expected-sha "$DASHBOARD_FINAL_SHA" \
    --output .omo/evidence/dashboard-v2/task-14/final-deploy-start.json
uv run tests/dashboard_v2_railway_harness.py wait \
  --project-id ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment-id 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service-id a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --start-receipt .omo/evidence/dashboard-v2/task-14/final-deploy-start.json \
  --expected-sha "$DASHBOARD_FINAL_SHA" --timeout-seconds 900 --poll-seconds 10 \
  --output .omo/evidence/dashboard-v2/task-14/final-deploy-terminal.json
railway service status \
  --project ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --service a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --json | uv run tests/dashboard_v2_railway_harness.py assert-status \
  --expected-sha "$DASHBOARD_FINAL_SHA" --expected-status SUCCESS \
  --output .omo/evidence/dashboard-v2/task-14/final-service-status.json
DASHBOARD_V2_URL="$(uv run tests/dashboard_v2_railway_harness.py production-url \
  --status-receipt .omo/evidence/dashboard-v2/task-14/final-service-status.json)"
curl --fail --silent --show-error "$DASHBOARD_V2_URL/api/health" |
  uv run tests/dashboard_v2_railway_harness.py assert-health \
  --output .omo/evidence/dashboard-v2/task-14/final-health.json
curl --fail --silent --show-error "$DASHBOARD_V2_URL/api/snapshot" |
  uv run tests/dashboard_v2_railway_harness.py assert-snapshot \
  --accepted-schema 2 --redacted \
  --output .omo/evidence/dashboard-v2/task-14/final-snapshot.json
cd dashboard
bun run scripts/live-dashboard-qa.ts --base-url "$DASHBOARD_V2_URL" \
  --expect-viewer-event --expect-public-command-status 401 \
  --expect-private-boundary --idle-seconds 300 \
  --expect-authorized-autonomous-event \
  --autonomous-trigger-fixture ../tests/fixtures/dashboard/authorized-new-data-trigger.json \
  --duplicate-trigger-count 2 \
  --output ../.omo/evidence/dashboard-v2/task-14/final-live-qa.json
cd ..
railway service list \
  --project ee149dc8-82b8-46e7-8ef7-582400fed6f9 \
  --environment 8b37a20f-6b0d-4137-a787-ad90b4b482b9 \
  --json | uv run tests/dashboard_v2_railway_harness.py assert-services \
  --exact-service-id a7cae053-9289-4120-b5ac-7a0aefc36778 \
  --exact-service-id 21b11148-2386-47a4-b2dd-2a8dfbce94bd
```

Required output is
`FINAL_V2_OK schema=2 health=200 viewer_events=1 public_command=401 true_idle_data_requests=0 true_idle_interactive=0 true_idle_autonomous=0 autonomous_event_delivery=1 services=2`.
V1/unknown ingest must return 400 with no overwrite while the retained down-projection remains
present for the documented compatibility rollback reader.

## 16. Atomic commit protocol and completion packet

Before every commit:

```bash
git status --short
git diff --check
git diff --stat
git diff --staged --check
git diff --staged --stat
git log -1 --format='%H %s'
```

The executor stages only the exact files listed in its vertical section, inspects their full staged
diff, and uses that vertical's exact atomic message from the dependency table. A broad `git add .`
or `git add -A` is prohibited.

Never stage `.omo` evidence with product commits unless the approved parent plan explicitly assigns
that evidence to the milestone. Never stage unrelated shared-worktree changes. Do not push or
deploy the Todo 1 documentation commit until its parent execution phase authorizes rollout.

Each DoneClaim names:

- exact changed files and full commit SHA;
- each success scenario, invocation, binary observable, and captured artifact path;
- browser widths/routes/states, axe/keyboard/reduced-motion/zoom results;
- CLI help/bad/happy results;
- security, idle cost, exactly-once, entitlement, stale PID/log, service-drift, and rollback results;
- cleanup of servers, browser sessions, temporary mode-600 fixtures, and processes;
- remaining risks and explicit accepted debt only.

No milestone is complete from a passing unit test alone. The captured artifact must exist, be
non-empty, correspond to the exact tested/deployed SHA, and contain no prohibited value.
