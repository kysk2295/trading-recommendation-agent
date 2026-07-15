# Multi-Market Agent Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add immutable market, agent, strategy-lane, composite-experiment, opportunity, and trade-signal contracts while preserving every existing lane, database, scanner, and Paper execution behavior.

**Architecture:** New Pydantic models form a research identity and signal layer above the existing execution-oriented `LaneId`. An explicit adapter maps only approved US strategy families to legacy execution lanes, while KR contracts remain shadow-only. A pure projection converts current intraday `Recommendation` records into the new conditional signal envelope without changing the current outbox or execution path.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, Ruff, basedpyright, uv

---

## File Map

- Create `trading_agent/research_identity_models.py`: market, agent, strategy lane, manifest, and legacy lane binding contracts.
- Create `trading_agent/composite_experiment_models.py`: immutable strategy-version references and preregistered composite experiment specification.
- Create `trading_agent/signal_contract_models.py`: evidence, coverage, opportunity, quote validation, target, and trade-signal contracts.
- Create `trading_agent/recommendation_signal_projection.py`: pure existing-recommendation to signal-envelope adapter.
- Create `tests/test_research_identity_models.py`: identity and binding contract tests.
- Create `tests/test_composite_experiment_models.py`: component ordering, market isolation, and preregistration tests.
- Create `tests/test_signal_contract_models.py`: causal opportunity and actionable signal tests.
- Create `tests/test_recommendation_signal_projection.py`: legacy projection parity and rejection tests.
- Create `docs/checkpoints/2026-07-15-multi-market-agent-contracts-ko.md`: completed milestone evidence and remaining scope.
- Modify `README.md`: expose the implemented contract checkpoint without claiming that KR collection or new agents are running.

### Task 1: Research Identity And Legacy Binding

**Files:**
- Create: `tests/test_research_identity_models.py`
- Create: `trading_agent/research_identity_models.py`

- [ ] **Step 1: Write the failing identity tests**

Create tests that express the public API:

```python
from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.lane_policy_models import LaneId
from trading_agent.research_identity_models import (
    AgentFamily,
    AgentManifest,
    AgentOperatingMode,
    AgentOutputKind,
    LegacyExecutionLaneBinding,
    MarketId,
    StrategyLaneRef,
)

REGISTERED_AT = dt.datetime(2026, 7, 15, 1, tzinfo=dt.UTC)


def test_strategy_lane_has_a_stable_market_agent_coordinate() -> None:
    lane = StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id="orb",
    )
    assert lane.canonical_id == "us_equities/day_trading/orb"


def test_manifest_rejects_mixed_agent_lanes_and_kr_paper_mode() -> None:
    us_orb = StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id="orb",
    )
    kr_theme = StrategyLaneRef(
        market_id=MarketId.KR_EQUITIES,
        agent_family=AgentFamily.OPPORTUNITY_MANAGER,
        strategy_id="theme_momentum",
    )
    with pytest.raises(ValidationError):
        AgentManifest(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            manifest_version="1.0.0",
            registered_at=REGISTERED_AT,
            output_kind=AgentOutputKind.TRADE_SIGNAL,
            operating_mode=AgentOperatingMode.CONTRACT_ONLY,
            strategy_lanes=(kr_theme, us_orb),
        )
    with pytest.raises(ValidationError):
        AgentManifest(
            market_id=MarketId.KR_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            manifest_version="1.0.0",
            registered_at=REGISTERED_AT,
            output_kind=AgentOutputKind.OPPORTUNITY,
            operating_mode=AgentOperatingMode.ALPACA_PAPER,
            strategy_lanes=(kr_theme,),
        )
    with pytest.raises(ValidationError):
        AgentManifest(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            manifest_version="1.0.0",
            registered_at=REGISTERED_AT,
            output_kind=AgentOutputKind.OPPORTUNITY,
            operating_mode=AgentOperatingMode.CONTRACT_ONLY,
            strategy_lanes=(us_orb,),
        )


def test_legacy_binding_is_an_explicit_us_execution_adapter() -> None:
    us_orb = StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id="orb",
    )
    binding = LegacyExecutionLaneBinding(
        strategy_lane=us_orb,
        legacy_lane_id=LaneId.INTRADAY_MOMENTUM,
    )
    assert binding.legacy_lane_id is LaneId.INTRADAY_MOMENTUM

    kr_theme = StrategyLaneRef(
        market_id=MarketId.KR_EQUITIES,
        agent_family=AgentFamily.OPPORTUNITY_MANAGER,
        strategy_id="theme_momentum",
    )
    with pytest.raises(ValidationError):
        LegacyExecutionLaneBinding(
            strategy_lane=kr_theme,
            legacy_lane_id=LaneId.INTRADAY_MOMENTUM,
        )
```

- [ ] **Step 2: Run the identity tests and verify RED**

Run: `uv run pytest tests/test_research_identity_models.py -q`

Expected: collection fails with `ModuleNotFoundError: trading_agent.research_identity_models`.

- [ ] **Step 3: Implement the minimal identity contracts**

Create frozen, `extra="forbid"` Pydantic models with these exact public fields:

```python
class MarketId(StrEnum):
    US_EQUITIES = "us_equities"
    KR_EQUITIES = "kr_equities"


class AgentFamily(StrEnum):
    OPPORTUNITY_MANAGER = "opportunity_manager"
    DAY_TRADING = "day_trading"
    SWING_TRADING = "swing_trading"
    SYSTEMATIC_QUANT = "systematic_quant"
    MARKET_CONTEXT = "market_context"
    ALLOCATION_MANAGER = "allocation_manager"


class AgentOutputKind(StrEnum):
    OPPORTUNITY = "opportunity"
    TRADE_SIGNAL = "trade_signal"
    MARKET_CONTEXT = "market_context"
    ALLOCATION = "allocation"


class AgentOperatingMode(StrEnum):
    CONTRACT_ONLY = "contract_only"
    SHADOW = "shadow"
    ALPACA_PAPER = "alpaca_paper"


class StrategyLaneRef(BaseModel):
    schema_version: Literal[1] = 1
    market_id: MarketId
    agent_family: AgentFamily
    strategy_id: str

    @property
    def canonical_id(self) -> str:
        return f"{self.market_id}/{self.agent_family}/{self.strategy_id}"


class AgentManifest(BaseModel):
    schema_version: Literal[1] = 1
    market_id: MarketId
    agent_family: AgentFamily
    manifest_version: str
    registered_at: dt.datetime
    output_kind: AgentOutputKind
    operating_mode: AgentOperatingMode
    strategy_lanes: tuple[StrategyLaneRef, ...]


class LegacyExecutionLaneBinding(BaseModel):
    schema_version: Literal[1] = 1
    strategy_lane: StrategyLaneRef
    legacy_lane_id: LaneId
```

Validation rules:

- identifiers use `^[a-z0-9][a-z0-9_]{0,63}$`; versions use the existing safe identifier shape.
- manifest time is timezone-aware; lanes are non-empty, canonical-order sorted, unique, and match manifest market/family.
- output mapping is fixed: opportunity manager → opportunity; day/swing/systematic → trade signal; context → market context; allocation manager → allocation.
- Alpaca Paper mode is valid only for `us_equities` day or swing trading.
- legacy binding accepts only US day → `intraday_momentum`, US swing → `swing_momentum`, and US context → `market_regime`.

- [ ] **Step 4: Run identity tests and verify GREEN**

Run: `uv run pytest tests/test_research_identity_models.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add trading_agent/research_identity_models.py tests/test_research_identity_models.py
git commit -m "feat: add multi-market agent identities"
```

### Task 2: Composite Experiment Preregistration

**Files:**
- Create: `tests/test_composite_experiment_models.py`
- Create: `trading_agent/composite_experiment_models.py`

- [ ] **Step 1: Write the failing composite experiment tests**

Tests must construct a KR theme manager + KR day strategy experiment and reject post-hoc or cross-market combinations:

```python
def test_kr_theme_and_day_versions_form_a_preregistered_composite() -> None:
    registered_at = dt.datetime(2026, 7, 15, 1, tzinfo=dt.UTC)
    effective_at = dt.datetime(2026, 7, 16, 9, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    day = _version(AgentFamily.DAY_TRADING, "theme_vwap_pullback", "kr-theme-day-v1")
    manager = _version(AgentFamily.OPPORTUNITY_MANAGER, "theme_momentum", "kr-theme-manager-v1")
    spec = CompositeExperimentSpec(
        experiment_id="KR-THEME-DAY-001",
        primary_lane=day.strategy_lane,
        component_versions=tuple(sorted((day, manager), key=lambda item: item.canonical_id)),
        combination_rule="Use the frozen theme ranking as the only candidate universe for the day rule.",
        registered_at=registered_at,
        effective_at=effective_at,
    )
    assert spec.primary_lane.market_id is MarketId.KR_EQUITIES


def test_composite_rejects_post_hoc_or_cross_market_components() -> None:
    registered_at = dt.datetime(2026, 7, 15, 1, tzinfo=dt.UTC)
    day = _version(AgentFamily.DAY_TRADING, "theme_vwap_pullback", "kr-theme-day-v1")
    manager = _version(AgentFamily.OPPORTUNITY_MANAGER, "theme_momentum", "kr-theme-manager-v1")
    components = tuple(sorted((day, manager), key=lambda item: item.canonical_id))

    with pytest.raises(ValidationError):
        CompositeExperimentSpec(
            experiment_id="KR-THEME-DAY-POSTHOC",
            primary_lane=day.strategy_lane,
            component_versions=components,
            combination_rule="Invalid post-hoc combination.",
            registered_at=registered_at,
            effective_at=registered_at,
        )

    us_day = StrategyVersionRef(
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        strategy_version="orb-v1",
    )
    mixed = tuple(sorted((day, us_day), key=lambda item: item.canonical_id))
    with pytest.raises(ValidationError):
        CompositeExperimentSpec(
            experiment_id="KR-US-MIXED-001",
            primary_lane=day.strategy_lane,
            component_versions=mixed,
            combination_rule="Invalid cross-market combination.",
            registered_at=registered_at,
            effective_at=registered_at + dt.timedelta(days=1),
        )

    with pytest.raises(ValidationError):
        CompositeExperimentSpec(
            experiment_id="KR-DUPLICATE-001",
            primary_lane=day.strategy_lane,
            component_versions=(day, day),
            combination_rule="Invalid duplicate combination.",
            registered_at=registered_at,
            effective_at=registered_at + dt.timedelta(days=1),
        )


def _version(
    family: AgentFamily,
    strategy_id: str,
    version: str,
) -> StrategyVersionRef:
    return StrategyVersionRef(
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.KR_EQUITIES,
            agent_family=family,
            strategy_id=strategy_id,
        ),
        strategy_version=version,
    )
```

- [ ] **Step 2: Run composite tests and verify RED**

Run: `uv run pytest tests/test_composite_experiment_models.py -q`

Expected: collection fails because `trading_agent.composite_experiment_models` does not exist.

- [ ] **Step 3: Implement immutable composite models**

Create:

```python
class StrategyVersionRef(BaseModel):
    schema_version: Literal[1] = 1
    strategy_lane: StrategyLaneRef
    strategy_version: str

    @property
    def canonical_id(self) -> str:
        return f"{self.strategy_lane.canonical_id}@{self.strategy_version}"


class CompositeExperimentSpec(BaseModel):
    schema_version: Literal[1] = 1
    experiment_id: str
    primary_lane: StrategyLaneRef
    component_versions: tuple[StrategyVersionRef, ...]
    combination_rule: str
    registered_at: dt.datetime
    effective_at: dt.datetime
```

Require aware times, `effective_at > registered_at`, at least two distinct canonical component versions in canonical order, at least two distinct strategy lanes, all components in the primary market, the primary lane among components, and canonical non-empty identifiers/text.

- [ ] **Step 4: Run composite tests and verify GREEN**

Run: `uv run pytest tests/test_composite_experiment_models.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add trading_agent/composite_experiment_models.py tests/test_composite_experiment_models.py
git commit -m "feat: add composite strategy experiment contract"
```

### Task 3: Opportunity And Trade Signal Contracts

**Files:**
- Create: `tests/test_signal_contract_models.py`
- Create: `trading_agent/signal_contract_models.py`

- [ ] **Step 1: Write failing opportunity contract tests**

Cover a complete US opportunity snapshot and three fail-closed cases:

```python
OBSERVED_AT = dt.datetime(2026, 7, 15, 14, 31, tzinfo=dt.UTC)


def test_opportunity_snapshot_requires_causal_complete_sources() -> None:
    snapshot = _opportunity_snapshot()
    assert snapshot.candidates[0].symbol == "ABCD"


def test_opportunity_snapshot_rejects_incomplete_or_future_sources() -> None:
    valid = _opportunity_snapshot()
    with pytest.raises(ValidationError):
        OpportunitySnapshot.model_validate(
            {
                **valid.model_dump(),
                "source_coverage": (
                    SourceCoverage(
                        source_id="kis_us_rankings",
                        observed_at=OBSERVED_AT,
                        record_count=0,
                        complete=False,
                        failure_reason="provider_timeout",
                    ),
                ),
            }
        )
    with pytest.raises(ValidationError):
        OpportunitySnapshot.model_validate(
            {
                **valid.model_dump(),
                "evidence_refs": (_evidence("future-ranking", OBSERVED_AT + dt.timedelta(seconds=1)),),
            }
        )


def test_kr_opportunity_requires_a_six_digit_symbol() -> None:
    valid = _opportunity_snapshot()
    with pytest.raises(ValidationError):
        OpportunitySnapshot.model_validate(
            {
                **valid.model_dump(),
                "strategy_lane": _lane(
                    AgentFamily.OPPORTUNITY_MANAGER,
                    "theme_momentum",
                    market_id=MarketId.KR_EQUITIES,
                ),
            }
        )


def _opportunity_snapshot() -> OpportunitySnapshot:
    return OpportunitySnapshot(
        opportunity_id="US-RANKING-20260715T143100Z",
        strategy_lane=_lane(AgentFamily.OPPORTUNITY_MANAGER, "ranking_momentum"),
        producer_strategy_version="ranking-momentum-v1",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + dt.timedelta(minutes=1),
        candidates=(
            OpportunityCandidate(
                symbol="ABCD",
                rank=1,
                score=Decimal("9.5"),
                features=(FeatureValue(name="relative_volume", value="4.2"),),
            ),
        ),
        evidence_refs=(_evidence("ranking", OBSERVED_AT),),
        source_coverage=(
            SourceCoverage(
                source_id="kis_us_rankings",
                observed_at=OBSERVED_AT,
                record_count=25,
                complete=True,
            ),
        ),
    )


def _lane(
    family: AgentFamily,
    strategy_id: str,
    *,
    market_id: MarketId = MarketId.US_EQUITIES,
) -> StrategyLaneRef:
    return StrategyLaneRef(
        market_id=market_id,
        agent_family=family,
        strategy_id=strategy_id,
    )


def _evidence(record_id: str, observed_at: dt.datetime) -> EvidenceRef:
    return EvidenceRef(namespace="candidate_inputs", record_id=record_id, observed_at=observed_at)
```

- [ ] **Step 2: Run opportunity tests and verify RED**

Run: `uv run pytest tests/test_signal_contract_models.py -q`

Expected: collection fails because `trading_agent.signal_contract_models` does not exist.

- [ ] **Step 3: Implement evidence, feature, coverage, and opportunity models**

Create frozen models:

```python
class EvidenceRef(BaseModel):
    namespace: str
    record_id: str
    observed_at: dt.datetime

class FeatureValue(BaseModel):
    name: str
    value: str

class SourceCoverage(BaseModel):
    source_id: str
    observed_at: dt.datetime
    record_count: int
    complete: bool
    failure_reason: str | None = None

class OpportunityCandidate(BaseModel):
    symbol: str
    rank: int
    score: Decimal
    features: tuple[FeatureValue, ...]

class OpportunitySnapshot(BaseModel):
    schema_version: Literal[1] = 1
    opportunity_id: str
    strategy_lane: StrategyLaneRef
    producer_strategy_version: str
    observed_at: dt.datetime
    valid_until: dt.datetime
    candidates: tuple[OpportunityCandidate, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    source_coverage: tuple[SourceCoverage, ...]
```

Require opportunity-manager family, aware causal times, `valid_until > observed_at`, non-empty contiguous candidate ranks, unique market-valid symbols, sorted unique features/evidence/sources, evidence/source times no later than `observed_at`, and every source coverage row complete with no failure reason.

- [ ] **Step 4: Write failing trade-signal tests**

Add tests for a conditional long signal, current-quote validation, and invalid price geometry:

```python
def test_conditional_trade_signal_has_prices_expiry_and_provenance() -> None:
    signal = TradeSignalEnvelope(
        signal_id="signal-1",
        strategy_lane=_lane(AgentFamily.DAY_TRADING, "orb"),
        producer_strategy_version="orb-v1",
        symbol="ABCD",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + dt.timedelta(minutes=2),
        side=SignalSide.LONG,
        entry_type=SignalEntryType.STOP_TRIGGER,
        entry_price=Decimal("10.10"),
        stop_price=Decimal("9.90"),
        targets=(TradeTarget(label="1r", price=Decimal("10.30")),),
        actionability=SignalActionability.CONDITIONAL,
        invalidation_rule="Invalidate below the stop before entry or on stale data.",
        rationale="Opening range breakout with confirmed relative volume.",
        evidence_refs=(_evidence("recommendations", OBSERVED_AT),),
    )
    assert signal.quote_validation is None


def test_current_quote_actionability_requires_a_fresh_acceptable_quote() -> None:
    conditional = _conditional_signal()
    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate(
            {
                **conditional.model_dump(),
                "actionability": SignalActionability.CURRENT_QUOTE_VALIDATED,
            }
        )

    quote = QuoteValidation(
        bid=Decimal("10.08"),
        ask=Decimal("10.10"),
        observed_at=OBSERVED_AT - dt.timedelta(seconds=1),
        valid_until=OBSERVED_AT + dt.timedelta(seconds=5),
        spread_bps=Decimal("19.82"),
        max_slippage_bps=Decimal("25"),
    )
    actionable = TradeSignalEnvelope.model_validate(
        {
            **conditional.model_dump(),
            "actionability": SignalActionability.CURRENT_QUOTE_VALIDATED,
            "quote_validation": quote,
        }
    )
    assert actionable.quote_validation == quote

    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate(
            {
                **actionable.model_dump(),
                "quote_validation": quote.model_copy(update={"spread_bps": Decimal("26")}),
            }
        )


def test_trade_signal_rejects_invalid_long_price_geometry() -> None:
    valid = _conditional_signal()
    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate({**valid.model_dump(), "stop_price": Decimal("10.10")})
    with pytest.raises(ValidationError):
        TradeSignalEnvelope.model_validate(
            {
                **valid.model_dump(),
                "targets": (TradeTarget(label="1r", price=Decimal("10.10")),),
            }
        )


def _conditional_signal() -> TradeSignalEnvelope:
    return TradeSignalEnvelope(
        signal_id="signal-1",
        strategy_lane=_lane(AgentFamily.DAY_TRADING, "orb"),
        producer_strategy_version="orb-v1",
        symbol="ABCD",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + dt.timedelta(minutes=2),
        side=SignalSide.LONG,
        entry_type=SignalEntryType.STOP_TRIGGER,
        entry_price=Decimal("10.10"),
        stop_price=Decimal("9.90"),
        targets=(TradeTarget(label="1r", price=Decimal("10.30")),),
        actionability=SignalActionability.CONDITIONAL,
        invalidation_rule="Invalidate below the stop before entry or on stale data.",
        rationale="Opening range breakout with confirmed relative volume.",
        evidence_refs=(_evidence("recommendations", OBSERVED_AT),),
    )
```

- [ ] **Step 5: Run trade-signal tests and verify RED**

Run: `uv run pytest tests/test_signal_contract_models.py -q`

Expected: opportunity tests pass but imports or construction of trade-signal classes fail.

- [ ] **Step 6: Implement trade-signal models**

Add `SignalSide`, `SignalEntryType`, `SignalActionability`, `TradeTarget`, `QuoteValidation`, and `TradeSignalEnvelope`. Day, swing, and systematic-quant families may emit trade signals. Enforce aware causal times, market-valid symbols, finite positive prices, directional stop/target geometry, non-empty causal evidence, and these actionability rules:

```python
if self.actionability is SignalActionability.CONDITIONAL:
    if self.quote_validation is not None:
        raise ValueError("conditional signal cannot claim quote validation")
elif self.quote_validation is None:
    raise ValueError("current actionability requires quote validation")
elif (
    self.quote_validation.observed_at > self.observed_at
    or self.observed_at > self.quote_validation.valid_until
    or self.quote_validation.spread_bps > self.quote_validation.max_slippage_bps
):
    raise ValueError("quote validation is stale or exceeds allowed slippage")
```

- [ ] **Step 7: Run signal tests and verify GREEN**

Run: `uv run pytest tests/test_signal_contract_models.py -q`

Expected: all tests pass.

- [ ] **Step 8: Commit Task 3**

```bash
git add trading_agent/signal_contract_models.py tests/test_signal_contract_models.py
git commit -m "feat: add opportunity and trade signal contracts"
```

### Task 4: Existing Intraday Recommendation Projection

**Files:**
- Create: `tests/test_recommendation_signal_projection.py`
- Create: `trading_agent/recommendation_signal_projection.py`

- [ ] **Step 1: Write failing projection tests**

Create a `Recommendation` in `SETUP` and verify every user-facing signal field survives projection:

```python
def test_projects_existing_orb_setup_to_conditional_signal() -> None:
    recommendation = Recommendation(
        recommendation_id="2026-07-15T14:31:00+00:00:ABCD:orb",
        symbol="ABCD",
        strategy="orb",
        created_at=OBSERVED_AT,
        entry=10.10,
        stop=9.90,
        target_1r=10.30,
        target_2r=10.50,
        state=RecommendationState.SETUP,
        rationale="Opening range breakout with confirmed relative volume.",
    )
    signal = project_intraday_recommendation(
        recommendation,
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        strategy_version="orb-v1",
        valid_until=OBSERVED_AT + dt.timedelta(minutes=2),
        evidence_refs=(
            EvidenceRef(
                namespace="recommendations",
                record_id=recommendation.recommendation_id,
                observed_at=OBSERVED_AT,
            ),
        ),
    )
    assert signal.signal_id == recommendation.recommendation_id
    assert signal.entry_price == Decimal("10.1")
    assert tuple(target.price for target in signal.targets) == (Decimal("10.3"), Decimal("10.5"))
    assert signal.rationale == recommendation.rationale
    assert signal.actionability is SignalActionability.CONDITIONAL


def test_projection_rejects_mismatched_market_family_or_strategy() -> None:
    recommendation = _recommendation()
    invalid_lanes = (
        StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="vwap_reclaim",
        ),
        StrategyLaneRef(
            market_id=MarketId.KR_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.SWING_TRADING,
            strategy_id="orb",
        ),
    )
    for lane in invalid_lanes:
        with pytest.raises(InvalidRecommendationSignalProjectionError):
            _project(recommendation, lane)


@pytest.mark.parametrize(
    "state",
    tuple(state for state in RecommendationState if state is not RecommendationState.SETUP),
)
def test_projection_rejects_every_non_setup_state(state: RecommendationState) -> None:
    with pytest.raises(InvalidRecommendationSignalProjectionError):
        _project(replace(_recommendation(), state=state), _orb_lane())


def _recommendation() -> Recommendation:
    return Recommendation(
        recommendation_id="2026-07-15T14:31:00+00:00:ABCD:orb",
        symbol="ABCD",
        strategy="orb",
        created_at=OBSERVED_AT,
        entry=10.10,
        stop=9.90,
        target_1r=10.30,
        target_2r=10.50,
        state=RecommendationState.SETUP,
        rationale="Opening range breakout with confirmed relative volume.",
    )


def _orb_lane() -> StrategyLaneRef:
    return StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id="orb",
    )


def _project(
    recommendation: Recommendation,
    lane: StrategyLaneRef,
) -> TradeSignalEnvelope:
    return project_intraday_recommendation(
        recommendation,
        strategy_lane=lane,
        strategy_version="orb-v1",
        valid_until=OBSERVED_AT + dt.timedelta(minutes=2),
        evidence_refs=(
            EvidenceRef(
                namespace="recommendations",
                record_id=recommendation.recommendation_id,
                observed_at=OBSERVED_AT,
            ),
        ),
    )
```

- [ ] **Step 2: Run projection tests and verify RED**

Run: `uv run pytest tests/test_recommendation_signal_projection.py -q`

Expected: collection fails because `trading_agent.recommendation_signal_projection` does not exist.

- [ ] **Step 3: Implement the pure projection**

Create:

```python
def project_intraday_recommendation(
    recommendation: Recommendation,
    *,
    strategy_lane: StrategyLaneRef,
    strategy_version: str,
    valid_until: dt.datetime,
    evidence_refs: tuple[EvidenceRef, ...],
    opportunity_id: str | None = None,
) -> TradeSignalEnvelope:
    if (
        recommendation.state is not RecommendationState.SETUP
        or recommendation.created_at.tzinfo is None
        or recommendation.created_at.utcoffset() is None
        or strategy_lane.market_id is not MarketId.US_EQUITIES
        or strategy_lane.agent_family is not AgentFamily.DAY_TRADING
        or strategy_lane.strategy_id != recommendation.strategy
    ):
        raise InvalidRecommendationSignalProjectionError
    return TradeSignalEnvelope(
        signal_id=recommendation.recommendation_id,
        strategy_lane=strategy_lane,
        producer_strategy_version=strategy_version,
        symbol=recommendation.symbol,
        observed_at=recommendation.created_at,
        valid_until=valid_until,
        side=SignalSide.LONG,
        entry_type=SignalEntryType.STOP_TRIGGER,
        entry_price=Decimal(str(recommendation.entry)),
        stop_price=Decimal(str(recommendation.stop)),
        targets=(
            TradeTarget(label="1r", price=Decimal(str(recommendation.target_1r))),
            TradeTarget(label="2r", price=Decimal(str(recommendation.target_2r))),
        ),
        actionability=SignalActionability.CONDITIONAL,
        invalidation_rule=(
            f"Invalidate below {recommendation.stop:g} before entry or when market/data gates fail."
        ),
        rationale=recommendation.rationale,
        evidence_refs=evidence_refs,
        opportunity_id=opportunity_id,
    )
```

Fail before model construction unless the source is a timezone-aware `SETUP` recommendation, the lane is US day trading, and `strategy_id == recommendation.strategy`. Convert floats through `Decimal(str(value))`, emit `STOP_TRIGGER`, long side, `1r`/`2r` targets, no quote validation, and a deterministic invalidation rule referencing the source stop.

- [ ] **Step 4: Run projection tests and related legacy tests**

Run: `uv run pytest tests/test_recommendation_signal_projection.py tests/test_alert_outbox.py tests/test_trading_agent.py -q`

Expected: all tests pass and existing outbox behavior is unchanged.

- [ ] **Step 5: Commit Task 4**

```bash
git add trading_agent/recommendation_signal_projection.py tests/test_recommendation_signal_projection.py
git commit -m "feat: project intraday recommendations to signals"
```

### Task 5: Documentation, Verification, And Main Integration

**Files:**
- Create: `docs/checkpoints/2026-07-15-multi-market-agent-contracts-ko.md`
- Modify: `README.md`

- [ ] **Step 1: Run focused quality checks before documenting**

Run:

```bash
uv run pytest \
  tests/test_research_identity_models.py \
  tests/test_composite_experiment_models.py \
  tests/test_signal_contract_models.py \
  tests/test_recommendation_signal_projection.py -q
uv run ruff check \
  trading_agent/research_identity_models.py \
  trading_agent/composite_experiment_models.py \
  trading_agent/signal_contract_models.py \
  trading_agent/recommendation_signal_projection.py \
  tests/test_research_identity_models.py \
  tests/test_composite_experiment_models.py \
  tests/test_signal_contract_models.py \
  tests/test_recommendation_signal_projection.py
uv run basedpyright
```

Expected: zero failures, errors, or warnings.

- [ ] **Step 2: Write the checkpoint and README status**

The checkpoint must state exactly what is implemented, the RED/GREEN commands, the final verification results, that existing DB/execution behavior did not change, and that KR collection, live alerts, swing engines, quant replication, lifecycle v2, and Allocation Manager remain future milestones. Add the checkpoint link under README 문서 and one concise current-state sentence about the new contracts.

- [ ] **Step 3: Run full verification fresh**

Run each command separately and retain its exit status:

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
git diff --check
```

Expected: all tests pass, Ruff passes, basedpyright reports zero errors/warnings, and `git diff --check` emits no output.

- [ ] **Step 4: Perform manual CLI regression QA**

Run `uv run python run_trading_agent_replay.py --help`; expect exit 0 and the input/output/range options.

Run `uv run python run_trading_agent_replay.py /tmp/does-not-exist.csv`; expect nonzero exit and a redacted missing-input error.

Create a fresh temporary directory with `mktemp -d`, then run `uv run python run_trading_agent_replay.py examples/example_intraday.csv <temp-dir>/replay`; expect exit 0 and generated recommendation report, outbox, and SQLite files.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/checkpoints/2026-07-15-multi-market-agent-contracts-ko.md
git commit -m "docs: record multi-market contract milestone"
```

- [ ] **Step 6: Verify branch state, merge to main, reverify, and push**

Because the user explicitly selected continuous execution and `main` delivery, merge the isolated feature branch into local `main` with a normal fast-forward or merge commit, rerun `uv run pytest -q`, and push `main` to `origin`. Do not force-push. Confirm `git status --short --branch` is clean and synchronized.
