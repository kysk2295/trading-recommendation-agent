# Options Research Workbench Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the existing `#derivatives` route into a strict, fixture-verifiable five-view Options Research Workbench with a unified option-chain contract, deterministic research payoff calculation, promotion summaries, and Evidence Trace integration.

**Architecture:** Keep the nine top-level dashboard routes and the public read-only GET/WebSocket boundary unchanged. Add provider-neutral Pydantic models and a fail-closed local projection to the Python snapshot, mirror that contract in Zod, then render the Workbench through small vanilla TypeScript modules under the existing Ember design system. This foundation uses production-shaped fixtures and an explicit unavailable production projection; Alpaca, KIS, and LS store bindings are separate follow-up plans.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, Ruff, basedpyright, Bun, TypeScript 5.9, Zod 4, Hono, Biome, Playwright, axe-core, vanilla DOM/CSS.

---

## File map

### New files

- `trading_agent/dashboard_options_workbench_models.py` — strict provider-neutral Workbench projection models.
- `trading_agent/dashboard_options_workbench_projection.py` — builds the fail-closed production foundation projection without provider or broker calls.
- `tests/test_dashboard_options_workbench_models.py` — Python boundary and invariant tests.
- `tests/test_dashboard_options_workbench_projection.py` — production unavailable/blocked projection tests.
- `dashboard/src/options_workbench_schema.ts` — Zod mirror of the Python Workbench contract.
- `dashboard/src/workspaces/options_workbench_presenters.ts` — pure source-state and deterministic payoff presenters.
- `dashboard/src/workspaces/options_chain_table.ts` — calls-left/strike-center/puts-right semantic table.
- `dashboard/src/workspaces/options_workbench.ts` — internal five-view navigation and panel composition.
- `dashboard/public/assets/options-workbench.css` — Workbench layout and responsive component styles using existing tokens.
- `dashboard/tests/options_workbench_schema.test.ts` — Zod boundary tests.
- `dashboard/tests/options_workbench_presenters.test.ts` — deterministic payoff and authority-state tests.
- `dashboard/tests/options_workbench_render.test.ts` — DOM rendering and interaction tests.
- `dashboard/scripts/run-options-workbench-qa.ts` — production-browser route, interaction, axe, overflow, and screenshot evidence.

### Modified files

- `dashboard/DESIGN.md` — document the five internal Workbench views and reusable primitives before UI code.
- `trading_agent/dashboard_models_v2.py` — specialize the derivatives workspace with a Workbench payload.
- `trading_agent/dashboard_snapshot_v2.py` — attach the local Workbench projection.
- `tests/test_dashboard_snapshot_v2.py` — prove the Workbench is present and fail-closed.
- `dashboard/src/schema_v2.ts` — parse the specialized derivatives workspace.
- `dashboard/src/workspaces/derivatives.ts` — compose the contract strip, Workbench, and legacy evidence table.
- `dashboard/tests/snapshot_v2_fixture.ts` — add the canonical unavailable Workbench fixture.
- `dashboard/tests/e2e/derivatives_paper_fixture.ts` — add populated and adverse Workbench fixtures.
- `dashboard/public/index.html` — load the Workbench stylesheet.
- `dashboard/public/showcase.html` — add demonstration-only Workbench primitives before product composition.
- `dashboard/package.json` — add the bounded Workbench browser-QA command.

## Safety invariants for every task

- Never read credential files or call a provider, model, account, balance, order, or broker endpoint.
- Do not add a mutation API to the dashboard.
- Preserve the exact Alpaca Paper endpoint guard and every existing Risk Kernel gate.
- Do not load `data/regend_us_stocks` or run a full-universe backtest.
- Fixture values are always labelled demonstration/research-only and never presented as realized or expected profitability.
- Use at most two subagents concurrently; subagents must not spawn children.

### Task 1: Lock the Workbench design contract and capture the current surface

**Files:**
- Modify: `dashboard/DESIGN.md`
- Evidence: `.omo/evidence/options-workbench-foundation/before/`

- [ ] **Step 1: Capture the current production build before changing UI code**

Run:

```bash
cd dashboard
bun run build
OPTIONS_WORKBENCH_BASELINE_PORT="$(uv run python - <<'PY'
import socket

with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
PY
)"
DASHBOARD_INGEST_TOKEN=options-workbench-baseline-ingest-token \
DASHBOARD_OPERATOR_TOKEN=options-workbench-baseline-operator-token \
PORT="$OPTIONS_WORKBENCH_BASELINE_PORT" bun run start
```

In a second terminal, open `http://127.0.0.1:$OPTIONS_WORKBENCH_BASELINE_PORT/#derivatives` with Playwright Chrome and capture 375, 768, and 1280 pixel screenshots into `.omo/evidence/options-workbench-foundation/before/`. Record page overflow, console errors, and the current `data-source-state` marker. Stop the server and prove the allocated port is free. The two literal tokens above are disposable local QA values, not provider or broker credentials.

Expected: three non-empty screenshots; the current route contains the derivatives contract strip and generic evidence surface but no internal Workbench tabs or option-chain table.

- [ ] **Step 2: Add the approved internal-view and primitive contract to `DESIGN.md`**

Add the following rows to Section 5 without changing the nine top-level routes:

```markdown
| `OptionsWorkbench` | internal Market Pulse, Option Chain, Strategy & Agent, Experiment Lab, Promotion & Operations views inside `#derivatives` | roving tab focus, one active panel, section-local source states, route reload returns to Market Pulse |
| `OptionChainTable` | calls left, strike center, puts right; bounded rows and local horizontal overflow | expiration selection, cell trace, research-leg selection, unavailable/stale/blocked rows never selectable |
| `StrategyScenarioPanel` | immutable selected legs, expiry payoff, break-even and bounded scenario table | deterministic calculation only; research-only source label; no LLM-authored numeric authority |
| `ToolReceiptCard` | safe parameters, progress, terminal result and evidence links | details/summary disclosure; secrets, raw payloads and local paths prohibited |
| `PromotionGateLedger` | evidence, Reviewer, manual approval and next-session authority as separate rows | read-only; missing gate remains explicit and no button can create broker authority |
```

Add a Workbench subsection stating that the visual grammar borrows the exact comparison behaviors from ORATS/OptionStrat/Market Chameleon while Ember tokens, public-read safety, and Evidence Trace remain authoritative.

- [ ] **Step 3: Verify the design contract has no placeholder or token bypass**

Run:

```bash
rg -n 'TBD|TODO|#[0-9A-Fa-f]{6}|rgb\(' dashboard/DESIGN.md
git diff --check -- dashboard/DESIGN.md
```

Expected: no placeholder and no new raw color declaration; `git diff --check` exits 0.

- [ ] **Step 4: Commit the design contract and baseline evidence reference**

```bash
git add dashboard/DESIGN.md
git commit -m "docs(dashboard): define options workbench primitives"
```

### Task 2: Add strict Python Workbench models

**Files:**
- Create: `trading_agent/dashboard_options_workbench_models.py`
- Create: `tests/test_dashboard_options_workbench_models.py`

- [ ] **Step 1: Write failing model tests**

Create tests with explicit Given/When/Then blocks for a valid chain, an inconsistent call/put strike, a selectable stale quote, and a promotion with impossible gate counts:

```python
def test_chain_row_accepts_call_and_put_at_same_strike() -> None:
    # Given
    call = option_cell(contract_id="alpaca.aapl.call", side="call", state="indicative")
    put = option_cell(contract_id="alpaca.aapl.put", side="put", state="indicative")

    # When
    row = OptionChainRowV2(strike="200.00", call=call, put=put)

    # Then
    assert row.call is not None
    assert row.put is not None


def test_stale_quote_cannot_be_selectable() -> None:
    # Given / When / Then
    with pytest.raises(InvalidOptionsWorkbenchError, match="selectable_quote_not_usable"):
        option_cell(contract_id="alpaca.aapl.call", side="call", state="stale", selectable=True)
```

- [ ] **Step 2: Run the tests and confirm RED for the missing module**

Run:

```bash
uv run pytest -q tests/test_dashboard_options_workbench_models.py
```

Expected: collection fails because `trading_agent.dashboard_options_workbench_models` does not exist.

- [ ] **Step 3: Implement the provider-neutral models**

Implement frozen, extra-forbid Pydantic models with these exact public shapes:

```python
from __future__ import annotations

from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class InvalidOptionsWorkbenchError(ValueError):
    def __init__(self, *, reason: str) -> None:
        super().__init__()
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


class StrictOptionsWorkbenchModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OptionChainCellV2(StrictOptionsWorkbenchModel):
    contract_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    side: Literal["call", "put"]
    provider: Literal["alpaca", "kis", "ls"]
    state: Literal["indicative", "delayed", "current", "stale", "blocked", "unavailable"]
    bid: str | None = Field(default=None, pattern=r"^-?[0-9]+(?:\.[0-9]{1,8})?$")
    ask: str | None = Field(default=None, pattern=r"^-?[0-9]+(?:\.[0-9]{1,8})?$")
    last: str | None = Field(default=None, pattern=r"^-?[0-9]+(?:\.[0-9]{1,8})?$")
    implied_volatility: str | None = Field(default=None, pattern=r"^-?[0-9]+(?:\.[0-9]{1,8})?$")
    delta: str | None = Field(default=None, pattern=r"^-?[0-9]+(?:\.[0-9]{1,8})?$")
    gamma: str | None = Field(default=None, pattern=r"^-?[0-9]+(?:\.[0-9]{1,8})?$")
    theta: str | None = Field(default=None, pattern=r"^-?[0-9]+(?:\.[0-9]{1,8})?$")
    vega: str | None = Field(default=None, pattern=r"^-?[0-9]+(?:\.[0-9]{1,8})?$")
    volume: int | None = Field(default=None, ge=0)
    open_interest: int | None = Field(default=None, ge=0)
    observed_at: AwareDatetime | None
    trace_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    selectable: bool

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if self.selectable and self.state not in {"indicative", "delayed", "current"}:
            raise InvalidOptionsWorkbenchError(reason="selectable_quote_not_usable")
        return self


class OptionChainRowV2(StrictOptionsWorkbenchModel):
    strike: str = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,8})?$")
    call: OptionChainCellV2 | None
    put: OptionChainCellV2 | None

    @model_validator(mode="after")
    def validate_sides(self) -> Self:
        if self.call is not None and self.call.side != "call":
            raise InvalidOptionsWorkbenchError(reason="call_cell_side_mismatch")
        if self.put is not None and self.put.side != "put":
            raise InvalidOptionsWorkbenchError(reason="put_cell_side_mismatch")
        if self.call is None and self.put is None:
            raise InvalidOptionsWorkbenchError(reason="empty_chain_row")
        return self
```

The same file must also define `WorkbenchSectionV2`, `OptionChainViewV2` (maximum 41 rows and 12 expirations), `StrategyScenarioV2`, `PromotionSummaryV2`, and `OptionsWorkbenchV2`. Use literal states, bounded identifiers, nullable missing values, and validators that enforce count metadata and blocker presence.

Use these exact remaining contracts:

| Model | Required fields | Boundary validator |
|---|---|---|
| `WorkbenchSectionV2` | `state`, `observed_at`, `blocker_code`, `summary`, `trace_id` | unavailable/stale/blocked requires `blocker_code`; usable state requires aware `observed_at` |
| `OptionChainViewV2` | section fields plus `underlying`, `selected_expiration`, `expirations`, `total_count`, `projected_count`, `truncated`, `rows` | `projected_count == len(rows)`, `total_count >= projected_count`, `truncated == (total_count > projected_count)`, selected expiration belongs to `expirations` |
| `StrategyLegV2` | `contract_id`, `action`, `side`, decimal `strike`, decimal `premium`, positive `quantity`, positive `multiplier`, `trace_id` | contract side matches `side`; all numeric strings finite and bounded |
| `StrategyScenarioV2` | `state`, `currency`, `spot`, `legs`, `scenario_spots`, `trace_id` | 1–8 immutable legs, 2–41 sorted scenario spots, research-only state |
| `PromotionSummaryV2` | `promotion_id`, `state`, `passed_gate_count`, `total_gate_count`, `blockers`, `trace_id` | passed count cannot exceed total; non-approved state requires at least one blocker |
| `OptionsWorkbenchV2` | `schema_version=1`, `selected_view`, `market`, `chain`, `scenario`, `agent`, `experiment`, `promotions` | selected view is one of the five approved view identifiers; at most 20 promotions |

- [ ] **Step 4: Run focused tests and strict static checks**

```bash
uv run pytest -q tests/test_dashboard_options_workbench_models.py
uv run ruff check trading_agent/dashboard_options_workbench_models.py tests/test_dashboard_options_workbench_models.py
uv run basedpyright trading_agent/dashboard_options_workbench_models.py tests/test_dashboard_options_workbench_models.py
```

Expected: all tests pass; Ruff exits 0; basedpyright reports 0 errors.

- [ ] **Step 5: Commit the model boundary**

```bash
git add trading_agent/dashboard_options_workbench_models.py tests/test_dashboard_options_workbench_models.py
git commit -m "feat(dashboard): model options workbench snapshot"
```

### Task 3: Attach a fail-closed Workbench projection to Dashboard Snapshot v2

**Files:**
- Create: `trading_agent/dashboard_options_workbench_projection.py`
- Create: `tests/test_dashboard_options_workbench_projection.py`
- Modify: `trading_agent/dashboard_models_v2.py`
- Modify: `trading_agent/dashboard_snapshot_v2.py`
- Modify: `tests/test_dashboard_snapshot_v2.py`

- [ ] **Step 1: Write failing projection and snapshot tests**

```python
def test_projection_is_unavailable_without_canonical_chain() -> None:
    # Given
    now = dt.datetime(2026, 8, 3, 0, 0, tzinfo=dt.UTC)

    # When
    result = project_options_workbench(now=now, derivatives_trace_id="trace-derivatives")

    # Then
    assert result.chain.state == "unavailable"
    assert result.chain.blocker_code == "canonical_option_chain_missing"
    assert result.chain.rows == ()
    assert result.selected_view == "market_pulse"


def test_snapshot_contains_fail_closed_options_workbench(tmp_path: Path) -> None:
    # Given / When
    snapshot = collect_dashboard_snapshot_v2(tmp_path, now=NOW)

    # Then
    assert snapshot.workspaces.derivatives.workbench.chain.state == "unavailable"
    assert snapshot.workspaces.derivatives.workbench.promotions == ()
```

- [ ] **Step 2: Run the focused tests and confirm RED**

```bash
uv run pytest -q tests/test_dashboard_options_workbench_projection.py tests/test_dashboard_snapshot_v2.py
```

Expected: tests fail because the projector and specialized derivatives field do not exist.

- [ ] **Step 3: Implement the no-I/O projector**

`project_options_workbench` receives only an aware timestamp and the derivatives trace identifier. It returns `OptionsWorkbenchV2` with five section summaries, an unavailable empty chain, no promotions, no selected legs, and no provider/model/broker effects:

```python
def project_options_workbench(*, now: dt.datetime, derivatives_trace_id: str) -> OptionsWorkbenchV2:
    return OptionsWorkbenchV2(
        schema_version=1,
        selected_view="market_pulse",
        market=WorkbenchSectionV2(
            state="unavailable",
            observed_at=None,
            blocker_code="canonical_option_market_missing",
            summary="통합 옵션 시장 snapshot이 아직 연결되지 않았습니다",
            trace_id=derivatives_trace_id,
        ),
        chain=OptionChainViewV2(
            state="unavailable",
            observed_at=None,
            blocker_code="canonical_option_chain_missing",
            summary="통합 옵션 체인이 아직 연결되지 않았습니다",
            trace_id=derivatives_trace_id,
            underlying=None,
            selected_expiration=None,
            expirations=(),
            total_count=0,
            projected_count=0,
            truncated=False,
            rows=(),
        ),
        scenario=None,
        agent=WorkbenchSectionV2(
            state="unavailable",
            observed_at=None,
            summary="파생상품 Researcher 도구 receipt가 아직 연결되지 않았습니다",
            blocker_code="derivatives_agent_receipt_missing",
            trace_id=derivatives_trace_id,
        ),
        experiment=WorkbenchSectionV2(
            state="unavailable",
            observed_at=None,
            summary="옵션 실험 chain이 아직 연결되지 않았습니다",
            blocker_code="options_experiment_missing",
            trace_id=derivatives_trace_id,
        ),
        promotions=(),
    )
```

- [ ] **Step 4: Specialize the derivatives workspace and attach the projection**

In `dashboard_models_v2.py`, add:

```python
class DerivativesWorkspaceV2(SourceStateV2):
    workbench: OptionsWorkbenchV2
```

Change `WorkspacesV2.derivatives` from `SourceStateV2` to `DerivativesWorkspaceV2`. In `collect_dashboard_snapshot_v2`, build that model from the existing derivatives workspace plus `project_options_workbench(...)`. Do not change the projection totals because Workbench rows are a structured view of derivative evidence rather than another top-level item collection.

- [ ] **Step 5: Run tests and Python gates**

```bash
uv run pytest -q tests/test_dashboard_options_workbench_projection.py tests/test_dashboard_snapshot_v2.py tests/test_dashboard_projection_derivatives.py tests/test_dashboard_authoritative_derivatives_v2.py
uv run ruff check trading_agent/dashboard_options_workbench_projection.py trading_agent/dashboard_models_v2.py trading_agent/dashboard_snapshot_v2.py tests/test_dashboard_options_workbench_projection.py tests/test_dashboard_snapshot_v2.py
uv run basedpyright trading_agent/dashboard_options_workbench_projection.py trading_agent/dashboard_models_v2.py trading_agent/dashboard_snapshot_v2.py tests/test_dashboard_options_workbench_projection.py tests/test_dashboard_snapshot_v2.py
```

Expected: all focused tests and static checks pass with zero external effects.

- [ ] **Step 6: Commit the snapshot integration**

```bash
git add trading_agent/dashboard_options_workbench_projection.py trading_agent/dashboard_models_v2.py trading_agent/dashboard_snapshot_v2.py tests/test_dashboard_options_workbench_projection.py tests/test_dashboard_snapshot_v2.py
git commit -m "feat(dashboard): project fail-closed options workbench"
```

### Task 4: Mirror the Workbench contract in Zod and fixtures

**Files:**
- Create: `dashboard/src/options_workbench_schema.ts`
- Create: `dashboard/tests/options_workbench_schema.test.ts`
- Modify: `dashboard/src/schema_v2.ts`
- Modify: `dashboard/tests/snapshot_v2_fixture.ts`
- Modify: `dashboard/tests/e2e/derivatives_paper_fixture.ts`

- [ ] **Step 1: Write failing Zod boundary tests**

```typescript
test("accepts a bounded calls-left strike-center puts-right chain", () => {
  // Given
  const input = populatedOptionsWorkbenchFixture();

  // When
  const result = optionsWorkbenchSchema.safeParse(input);

  // Then
  expect(result.success).toBeTrue();
});

test("rejects a selectable stale quote", () => {
  // Given
  const input = populatedOptionsWorkbenchFixture({
    firstCallOverride: { state: "stale", selectable: true },
  });

  // When
  const result = optionsWorkbenchSchema.safeParse(input);

  // Then
  expect(result.success).toBeFalse();
});
```

- [ ] **Step 2: Confirm RED**

```bash
cd dashboard
bun test tests/options_workbench_schema.test.ts
```

Expected: FAIL because `options_workbench_schema.ts` does not exist.

- [ ] **Step 3: Implement the strict Zod mirror**

Use `z.strictObject`, `readonly` inferred types, maximum 41 rows, maximum 12 expirations, decimal strings, nullable absent data, and `.superRefine` invariants. The selectable-state check must be structural:

```typescript
const quoteStateSchema = z.enum([
  "indicative",
  "delayed",
  "current",
  "stale",
  "blocked",
  "unavailable",
]);

const optionChainCellSchema = z
  .strictObject({
    contract_id: boundedIdSchema,
    side: z.enum(["call", "put"]),
    provider: z.enum(["alpaca", "kis", "ls"]),
    state: quoteStateSchema,
    bid: decimalSchema.nullable(),
    ask: decimalSchema.nullable(),
    last: decimalSchema.nullable(),
    implied_volatility: decimalSchema.nullable(),
    delta: decimalSchema.nullable(),
    gamma: decimalSchema.nullable(),
    theta: decimalSchema.nullable(),
    vega: decimalSchema.nullable(),
    volume: z.number().int().nonnegative().nullable(),
    open_interest: z.number().int().nonnegative().nullable(),
    observed_at: timestampSchema.nullable(),
    trace_id: boundedIdSchema,
    selectable: z.boolean(),
  })
  .superRefine((value, context) => {
    const usable = ["indicative", "delayed", "current"].includes(value.state);
    if (value.selectable && !usable) {
      context.addIssue({ code: "custom", message: "selectable_quote_not_usable" });
    }
  });
```

Export `optionsWorkbenchSchema`, `OptionsWorkbench`, and `OptionsWorkbenchInput`.

- [ ] **Step 4: Parse the specialized derivatives workspace**

In `schema_v2.ts`, replace only the derivatives entry with:

```typescript
const derivativesWorkspaceSchema = z
  .strictObject({ ...sourceStateFields, workbench: optionsWorkbenchSchema })
  .superRefine(checkSourceState);
```

Update base, near-maximum, populated, and adverse fixtures. The populated fixture builder accepts a typed `firstCallOverride: Partial<Pick<OptionChainCellInput, "state" | "selectable">>` argument so adverse tests never mutate readonly data. The base production-shaped fixture must use the same unavailable blocker codes as Python. The happy fixture must include at least three strikes, both calls and puts, one indicative Alpaca source, one unavailable KIS source summary, one unavailable LS source summary, one strategy scenario, and one held promotion.

- [ ] **Step 5: Run the contract and regression suite**

```bash
bun test tests/options_workbench_schema.test.ts tests/schema_v2.test.ts tests/schema_v2_semantics.test.ts tests/derivatives_workspace.test.ts
bun run typecheck
bun run lint
```

Expected: all tests pass; TypeScript and Biome exit 0.

- [ ] **Step 6: Commit the browser boundary**

```bash
git add dashboard/src/options_workbench_schema.ts dashboard/src/schema_v2.ts dashboard/tests/options_workbench_schema.test.ts dashboard/tests/snapshot_v2_fixture.ts dashboard/tests/e2e/derivatives_paper_fixture.ts
git commit -m "feat(dashboard): parse options workbench contract"
```

### Task 5: Implement deterministic research scenario presenters

**Files:**
- Create: `dashboard/src/workspaces/options_workbench_presenters.ts`
- Create: `dashboard/tests/options_workbench_presenters.test.ts`

- [ ] **Step 1: Write failing payoff and selection tests**

```typescript
test("calculates a long call expiry payoff without LLM arithmetic", () => {
  // Given
  const legs: readonly StrategyLeg[] = [
    { action: "long", side: "call", strike: 100, premium: 5, quantity: 1, multiplier: 100 },
  ];

  // When
  const result = payoffAtExpiration(legs, 110);

  // Then
  expect(result).toBe(500);
});

test("refuses a blocked chain cell", () => {
  // Given
  const cell = optionCell({ state: "blocked", selectable: false });

  // When
  const result = selectableResearchLeg(cell);

  // Then
  expect(result).toEqual({ kind: "blocked", reason: "quote_not_selectable" });
});
```

- [ ] **Step 2: Confirm RED**

```bash
cd dashboard
bun test tests/options_workbench_presenters.test.ts
```

Expected: FAIL because the presenter module does not exist.

- [ ] **Step 3: Implement the pure functions and exhaustive unions**

```typescript
export type StrategyLeg = Readonly<{
  action: "long" | "short";
  side: "call" | "put";
  strike: number;
  premium: number;
  quantity: number;
  multiplier: number;
}>;

class OptionsWorkbenchPresenterError extends Error {
  override readonly name = "OptionsWorkbenchPresenterError";
}

export function payoffAtExpiration(legs: readonly StrategyLeg[], spot: number): number {
  return legs.reduce((total, leg) => {
    let intrinsic: number;
    switch (leg.side) {
      case "call":
        intrinsic = Math.max(spot - leg.strike, 0);
        break;
      case "put":
        intrinsic = Math.max(leg.strike - spot, 0);
        break;
      default:
        return assertNever(leg.side);
    }
    let direction: 1 | -1;
    switch (leg.action) {
      case "long":
        direction = 1;
        break;
      case "short":
        direction = -1;
        break;
      default:
        return assertNever(leg.action);
    }
    return total + direction * (intrinsic - leg.premium) * leg.quantity * leg.multiplier;
  }, 0);
}

function assertNever(value: never): never {
  throw new OptionsWorkbenchPresenterError(`unexpected variant: ${String(value)}`);
}
```

Also implement `scenarioSeries`, `breakEvenPoints`, `selectableResearchLeg`, and state presentation. Reject non-finite numeric conversion at the typed fixture boundary with a discriminated result; do not use `any`, assertions, non-null assertions, or bare errors.

- [ ] **Step 4: Run unit and static checks**

```bash
bun test tests/options_workbench_presenters.test.ts
bun run typecheck
bun run lint
```

Expected: tests pass; typecheck and Biome exit 0.

- [ ] **Step 5: Commit the deterministic calculator**

```bash
git add dashboard/src/workspaces/options_workbench_presenters.ts dashboard/tests/options_workbench_presenters.test.ts
git commit -m "feat(dashboard): calculate research option payoff"
```

### Task 6: Pass the Primitive Showcase gate

**Files:**
- Modify: `dashboard/public/showcase.html`
- Create: `dashboard/public/assets/options-workbench.css`
- Modify: `dashboard/public/index.html`

- [ ] **Step 1: Add demonstration-only Workbench primitives to `/showcase`**

Add one section containing:

```html
<section class="showcase-section" aria-labelledby="options-workbench-heading">
  <div class="section-heading">
    <p class="eyebrow">10 / OPTIONS RESEARCH WORKBENCH</p>
    <h2 id="options-workbench-heading">Chain, scenario, receipt and promotion primitives</h2>
  </div>
  <p class="fixture-notice" role="note">
    DEMONSTRATION DATA — INDICATIVE RESEARCH ONLY. NOT A RECOMMENDATION OR PAPER RESULT.
  </p>
  <nav class="workbench-tabs" aria-label="Demonstration options research views">
    <button type="button" aria-selected="true">Market Pulse</button>
    <button type="button" aria-selected="false">Option Chain</button>
    <button type="button" aria-selected="false">Strategy &amp; Agent</button>
    <button type="button" aria-selected="false">Experiment Lab</button>
    <button type="button" aria-selected="false">Promotion &amp; Operations</button>
  </nav>
</section>
```

The section must also contain a three-strike chain table, a two-leg scenario summary, one tool receipt, and one promotion gate row. Every value remains visibly labelled demonstration-only.

- [ ] **Step 2: Implement token-only Workbench CSS**

Use only existing `DESIGN.md` variables, 4px spacing multiples, tonal surfaces, sparse borders, and no shadow/gradient/glass. Add the stylesheet link to both `index.html` and `showcase.html` after `workspace.css` and before `responsive.css`.

- [ ] **Step 3: Build and run the showcase browser checks**

```bash
cd dashboard
bun run build
OPTIONS_WORKBENCH_SHOWCASE_PORT="$(uv run python - <<'PY'
import socket

with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
PY
)"
DASHBOARD_INGEST_TOKEN=options-workbench-showcase-ingest-token \
DASHBOARD_OPERATOR_TOKEN=options-workbench-showcase-operator-token \
PORT="$OPTIONS_WORKBENCH_SHOWCASE_PORT" bun run start
```

In a second terminal, use Playwright Chrome to open `/showcase` on the printed task-specific port at 375, 768, and 1280 pixels. Verify zero page-level horizontal overflow, visible focus, readable CJK and long identifiers, no axe violation/incomplete finding, and a local scroll owner for the option table. Stop the server and prove the allocated port is free. The literal tokens above are disposable local QA values, not provider or broker credentials.

- [ ] **Step 4: Commit the primitive showcase**

```bash
git add dashboard/public/showcase.html dashboard/public/index.html dashboard/public/assets/options-workbench.css
git commit -m "feat(dashboard): showcase options workbench primitives"
```

### Task 7: Render and interact with the five-view Workbench

**Files:**
- Create: `dashboard/src/workspaces/options_chain_table.ts`
- Create: `dashboard/src/workspaces/options_workbench.ts`
- Create: `dashboard/tests/options_workbench_render.test.ts`
- Modify: `dashboard/src/workspaces/derivatives.ts`

- [ ] **Step 1: Write failing DOM interaction tests**

```typescript
test("renders calls left, strike center, and puts right", () => {
  // Given
  const snapshot = derivativesPaperHappyFixture;

  // When
  const fragment = renderOptionsWorkbench(snapshot, drawerFixture());
  document.body.replaceChildren(fragment);

  // Then
  expect(document.querySelector("[data-chain-side='call']")).not.toBeNull();
  expect(document.querySelector("[data-chain-strike='200.00']")).not.toBeNull();
  expect(document.querySelector("[data-chain-side='put']")).not.toBeNull();
});

test("blocked production chain does not render selectable leg buttons", () => {
  // Given / When
  const fragment = renderOptionsWorkbench(derivativesPaperAdverseFixture, drawerFixture());
  document.body.replaceChildren(fragment);

  // Then
  expect(document.querySelectorAll("button[data-select-leg]").length).toBe(0);
});
```

- [ ] **Step 2: Confirm RED**

```bash
cd dashboard
bun test tests/options_workbench_render.test.ts
```

Expected: FAIL because the renderer modules do not exist.

- [ ] **Step 3: Implement the semantic option-chain table**

`options_chain_table.ts` must render a caption, scoped headers, call and put groups, a central strike column, provider/state labels, trace controls, and leg-selection buttons only when `selectable=true`. Use native buttons and one labeled focusable table viewport.

- [ ] **Step 4: Implement the five internal views**

`options_workbench.ts` must build a roving-tab interface with the exact IDs:

```typescript
export type WorkbenchView =
  | "market_pulse"
  | "option_chain"
  | "strategy_agent"
  | "experiment_lab"
  | "promotion_operations";

export const WORKBENCH_VIEWS: readonly WorkbenchView[] = Object.freeze([
  "market_pulse",
  "option_chain",
  "strategy_agent",
  "experiment_lab",
  "promotion_operations",
]);
```

ArrowLeft/ArrowRight, Home, End, Enter, and Space must work; one panel is active; inactive panels use `hidden`; focus remains on the selected tab. Leg selection updates the deterministic scenario panel but never mutates snapshot data.

- [ ] **Step 5: Compose it from `renderDerivatives`**

Render in this order: safety contract strip, `renderOptionsWorkbench(...)`, then the existing generic `renderWorkspace(...)` evidence surface. This preserves current derivative projections while the canonical provider bindings are still unavailable.

- [ ] **Step 6: Run tests and frontend gates**

```bash
bun test tests/options_workbench_render.test.ts tests/derivatives_workspace.test.ts tests/e2e/workstation_shell.test.ts
bun run typecheck
bun run lint
bun run build
```

Expected: tests, typecheck, lint, and production build pass.

- [ ] **Step 7: Commit the product surface**

```bash
git add dashboard/src/workspaces/options_chain_table.ts dashboard/src/workspaces/options_workbench.ts dashboard/src/workspaces/derivatives.ts dashboard/tests/options_workbench_render.test.ts
git commit -m "feat(dashboard): render options research workbench"
```

### Task 8: Add real-browser Workbench QA and close the foundation slice

**Files:**
- Create: `dashboard/scripts/run-options-workbench-qa.ts`
- Modify: `dashboard/package.json`
- Evidence: `.omo/evidence/options-workbench-foundation/after/`

- [ ] **Step 1: Add a bounded Playwright Chrome QA script**

The script creates `createApp(new MemorySnapshotStore(), qaIngestToken, qaOperatorToken)`, starts it with `Bun.serve({ port: 0 })`, publishes the typed fixture through `/api/ingest`, opens `#derivatives`, checks three viewports, drives all five tabs, selects one indicative research leg, opens and closes Evidence Trace, verifies focus return, runs axe at each viewport, checks page overflow, captures screenshots, and writes one JSON report. It must close the browser context and stop the server in `finally`, and fail on console errors, axe violations/incomplete results, missing DOM markers, overflow, or leaked listener state. The literal QA tokens stay in process memory and are never written to the report.

Add:

```json
"qa:options-workbench": "bun scripts/run-options-workbench-qa.ts"
```

to `dashboard/package.json`.

- [ ] **Step 2: Manually verify the QA command boundary**

Run:

```bash
cd dashboard
bun run qa:options-workbench -- --help
bun run qa:options-workbench -- --output /dev/null --widths 0
```

Expected: `--help` exits 0 and documents `--output` plus bounded `--widths`; width 0 exits non-zero before starting a server or browser. Record both transcripts.

- [ ] **Step 3: Run the QA script against the new production build**

```bash
cd dashboard
bun run build
bun run qa:options-workbench -- \
  --output ../.omo/evidence/options-workbench-foundation/after/report.json \
  --widths 375,768,1280
```

Expected: PASS with screenshots for 375, 768, and 1280 pixels; axe violations 0; axe incomplete 0; page overflow 0; five views driven; Evidence Trace focus returned. The JSON cleanup receipt proves the ephemeral server is stopped.

- [ ] **Step 4: Run the changed-surface regression gates**

```bash
uv run pytest -q tests/test_dashboard_options_workbench_models.py tests/test_dashboard_options_workbench_projection.py tests/test_dashboard_snapshot_v2.py tests/test_dashboard_projection_derivatives.py tests/test_dashboard_authoritative_derivatives_v2.py tests/test_alpaca_paper_config.py tests/test_alpaca_paper_client.py::test_client_rejects_live_base_url_before_request
uv run ruff check trading_agent/dashboard_options_workbench_models.py trading_agent/dashboard_options_workbench_projection.py trading_agent/dashboard_models_v2.py trading_agent/dashboard_snapshot_v2.py tests/test_dashboard_options_workbench_models.py tests/test_dashboard_options_workbench_projection.py tests/test_dashboard_snapshot_v2.py
uv run basedpyright trading_agent/dashboard_options_workbench_models.py trading_agent/dashboard_options_workbench_projection.py trading_agent/dashboard_models_v2.py trading_agent/dashboard_snapshot_v2.py tests/test_dashboard_options_workbench_models.py tests/test_dashboard_options_workbench_projection.py tests/test_dashboard_snapshot_v2.py
cd dashboard
bun run check
```

Expected: all targeted Python tests, Paper endpoint guard test, Ruff, basedpyright, TypeScript, Biome, and Bun tests pass.

- [ ] **Step 5: Run mandatory visual and performance QA**

Use `omo:visual-qa` against the production build at 375, 768, and 1280 pixels, including tabs, selected legs, blocked state, table overflow, Evidence Trace, and reduced motion. Run real Playwright Chrome Lighthouse mobile and desktop audits 3 times each and record medians. Do not weaken or hide Workbench content to improve scores.

Expected: the visual-qa dual oracle approves the Workbench, and the repository frontend quality gate is met or any measured environmental blocker is recorded without a false pass.

- [ ] **Step 6: Commit the QA harness**

```bash
git add dashboard/scripts/run-options-workbench-qa.ts dashboard/package.json
git commit -m "test(dashboard): verify options workbench browser flow"
```

- [ ] **Step 7: Freeze and review the exact tree**

Run code review and manual QA in parallel on the frozen full SHA/tree. Then run the final gate review using both report artifacts. Any fix invalidates the freeze and requires only affected evidence plus delta reviews to rerun.

Expected final evidence:

- before/after screenshots
- browser action and axe report
- targeted Python/TypeScript/static transcripts
- exact Paper endpoint guard result
- code-review, manual-QA, and gate-review reports bound to the same SHA/tree
- cleanup receipt proving no server, port, browser context, worker, container, or temporary worktree remains

## Plan completion boundary

This plan is complete when the Workbench foundation is visible and interactive from
`#derivatives`, both Python and TypeScript parse the same strict contract, the production projector
truthfully remains unavailable without a canonical provider chain, fixture-backed strategy math is
deterministic, and fresh real-browser evidence passes the frozen-tree gate. It does not claim that
Alpaca, KIS, or LS production option stores are connected; those are the next dependency-ordered
plans.
