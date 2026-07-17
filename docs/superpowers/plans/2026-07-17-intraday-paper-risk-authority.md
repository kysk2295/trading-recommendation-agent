# Intraday Paper Risk Authority Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every current intraday Paper safety-planning path use the active lane manifest's USD 100 notional, USD 10 planned risk, one-position, USD 30 daily-loss, and 20bp-per-side contract instead of the generic hard ceilings.

**Architecture:** Keep `PaperRiskConfig` hard ceilings and all operating-session defaults unchanged for compatibility, but make the production `plan_current_paper_safety` entry point explicitly inject `INTRADAY_PILOT_PAPER_RISK_CONFIG`, matching the armed entry and safety-mutation smoke paths. Add one boundary regression test, update operator-facing documentation, and perform no broker mutation, database migration, or endpoint change.

**Tech Stack:** Python 3.12, Pydantic/dataclass domain contracts, pytest, Ruff, basedpyright, append-only SQLite execution ledger.

---

### Task 1: Pin GET-Only Current Safety Planning To The Active Lane Contract

**Files:**
- Modify: `tests/test_paper_operating_session.py`
- Modify: `trading_agent/paper_trade_update_runtime.py`

- [x] **Step 1: Write the failing boundary test**

Add these imports to `tests/test_paper_operating_session.py`:

```python
from typing import cast

from trading_agent.lane_defaults import INTRADAY_PILOT_PAPER_RISK_CONFIG
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG, PaperRiskConfig
from trading_agent.paper_safety_models import BlockedPaperSafetyPlan
```

Add a test that replaces only the public operating-session opener and captures the config passed by the production helper:

```python
def test_current_safety_helper_uses_active_intraday_lane_risk_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    captured: list[PaperRiskConfig] = []

    class SafetySession:
        def plan_safety_actions(
            self,
            config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
        ) -> BlockedPaperSafetyPlan:
            captured.append(config)
            return BlockedPaperSafetyPlan(("fixture_block",))

    @contextmanager
    def opener(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> Iterator[PaperOperatingSession]:
        yield cast(PaperOperatingSession, SafetySession())

    monkeypatch.setattr(
        trade_update_runtime,
        "open_paper_operating_session",
        opener,
    )

    decision = trade_update_runtime.plan_current_paper_safety(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
    )

    assert isinstance(decision, BlockedPaperSafetyPlan)
    assert captured == [INTRADAY_PILOT_PAPER_RISK_CONFIG]
```

- [x] **Step 2: Run the test and verify the current default fails**

Run:

```bash
uv run pytest -q tests/test_paper_operating_session.py::test_current_safety_helper_uses_active_intraday_lane_risk_contract
```

Expected: FAIL because `plan_current_paper_safety` currently calls `session.plan_safety_actions()` with no config, so the captured value is `DEFAULT_PAPER_RISK_CONFIG` rather than the active lane contract.

- [x] **Step 3: Inject the active lane contract at the production boundary**

Add the import in `trading_agent/paper_trade_update_runtime.py`:

```python
from trading_agent.lane_defaults import INTRADAY_PILOT_PAPER_RISK_CONFIG
```

Change only `plan_current_paper_safety`:

```python
def plan_current_paper_safety(
    credentials: AlpacaPaperCredentials,
    store: ExecutionStore,
) -> PaperSafetyPlanDecision:
    with open_paper_operating_session(credentials, store) as session:
        return session.plan_safety_actions(INTRADAY_PILOT_PAPER_RISK_CONFIG)
```

Do not change `DEFAULT_PAPER_RISK_CONFIG`, `PaperOperatingSession.plan_safety_actions`, risk hard ceilings, mutation adapters, credential loading, or broker URLs in this checkpoint.

- [x] **Step 4: Run focused tests and static checks**

Run:

```bash
uv run pytest -q tests/test_paper_operating_session.py tests/test_alpaca_paper_safety_cli.py tests/test_alpaca_paper_safety_mutation_smoke.py tests/test_alpaca_paper_entry_smoke.py
uv run ruff check trading_agent/paper_trade_update_runtime.py tests/test_paper_operating_session.py
uv run basedpyright trading_agent/paper_trade_update_runtime.py tests/test_paper_operating_session.py
```

Expected: all commands exit 0. Existing armed smoke tests must continue proving that entry and safety mutation use the same `INTRADAY_PILOT_PAPER_RISK_CONFIG` object.

- [x] **Step 5: Commit the code and regression test**

```bash
git add trading_agent/paper_trade_update_runtime.py tests/test_paper_operating_session.py
git commit -m "fix: bind safety planning to intraday risk"
```

### Task 2: Record The Risk Authority Checkpoint

**Files:**
- Create: `docs/checkpoints/2026-07-17-intraday-paper-risk-authority-ko.md`
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`

- [x] **Step 1: Create the checkpoint document**

Write `docs/checkpoints/2026-07-17-intraday-paper-risk-authority-ko.md` with this structure and factual content:

```markdown
# Intraday Paper Risk Authority 체크포인트

- 날짜: 2026-07-17
- 범위: GET-only current-epoch safety planning의 active lane risk contract 통일
- broker mutation: 0건
- schema migration: 없음

## 변경

`plan_current_paper_safety`가 generic `DEFAULT_PAPER_RISK_CONFIG`를 암묵적으로 사용하지 않고 `INTRADAY_PILOT_PAPER_RISK_CONFIG`를 operating session에 명시적으로 전달한다.

현재 활성 한도는 다음과 같다.

- reference equity: USD 30,000
- maximum notional: USD 100
- maximum planned risk: USD 10
- maximum open positions: 1
- daily loss limit: USD 30
- minimum cost: 편도 20bp

일반 하드 상한은 코드 유효성의 바깥 경계일 뿐 pilot 운용 승인값이 아니다.

## 변경하지 않은 것

- Alpaca Paper endpoint와 arm 계약
- entry, OCO, cancel, flatten mutation 구현
- execution·lane·experiment schema
- 계좌 binding과 credential loader
- 실제 자금 또는 한국 주문 경로

## 검증

검증 명령과 실제 종료 결과를 최종 실행 뒤 기록한다. 실제 broker POST·PATCH·DELETE는 호출하지 않는다.
```

The final implementation must replace the last verification paragraph with the exact commands and observed pass counts from Task 3. Do not include credentials, account identifiers, broker order IDs, local private database paths, or raw payloads.

- [x] **Step 2: Correct the README operator contract**

In the `run_alpaca_paper_safety.py` section, replace the statement that the GET-only diagnostic uses the generic USD 300 default. State that the production helper now explicitly uses the active intraday lane's USD 30 daily-loss limit and that the command remains WSS + REST GET-only plus local append-only planning.

Keep the separate explanation that `paper_risk.py` hard ceilings are not current pilot approval.

- [x] **Step 3: Update the durable start document**

Add one current-state bullet to `CODEX_START_HERE.md` after the shared smoke risk bullet:

```markdown
- GET-only `run_alpaca_paper_safety.py`도 active intraday lane risk contract를 명시적으로 주입해 entry·armed safety mutation과 같은 USD 100·USD 10·1포지션·USD 30·편도 20bp 권위를 사용
```

Do not reorder or remove the existing live-session priorities.

- [x] **Step 4: Verify documentation consistency**

Run:

```bash
rg -n "USD 300|USD 30|INTRADAY_PILOT_PAPER_RISK_CONFIG|run_alpaca_paper_safety" README.md CODEX_START_HERE.md docs/checkpoints/2026-07-17-intraday-paper-risk-authority-ko.md
git diff --check
```

Expected: README may mention USD 300 only as a generic hard ceiling; every current pilot and production safety-planning statement must identify USD 30 as the active limit. `git diff --check` exits 0.

### Task 3: Full Verification, Manual CLI QA, And Publication

**Files:**
- Verify all changed files and repository regressions.
- Modify: `docs/checkpoints/2026-07-17-intraday-paper-risk-authority-ko.md` with actual verification evidence.

- [x] **Step 1: Run manual CLI help and invalid-path QA**

Run:

```bash
./run_alpaca_paper_safety.py --help
./run_alpaca_paper_safety.py \
  --database /tmp/trading-agent-missing-risk-authority.sqlite3 \
  --output-dir /tmp/trading-agent-risk-authority-invalid
```

Expected: `--help` exits 0 without credential loading. The missing-ledger invocation exits 1 before credential loading, does not create the database, and writes only a sanitized blocked report. Neither command calls broker POST, PATCH, or DELETE.

- [x] **Step 2: Run the fixture-backed happy path**

Run:

```bash
uv run pytest -q tests/test_alpaca_paper_safety_cli.py::test_safety_cli_writes_sanitized_get_only_plan_evidence
```

Expected: PASS, with the fixture report proving GET-only labeling and redaction.

- [x] **Step 3: Run complete verification**

Run one heavy process at a time:

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
git diff --check
```

Expected: all commands exit 0 with no pytest failures, Ruff violations, type errors, warnings, or whitespace errors.

- [x] **Step 4: Replace checkpoint verification prose with observed evidence**

Record the exact pytest pass count, Ruff result, basedpyright result, CLI exit codes, and `broker mutation: 0건` in the checkpoint. Do not claim any real-session Paper POST or strategy profitability.

- [x] **Step 5: Commit the documentation checkpoint**

```bash
git add README.md CODEX_START_HERE.md docs/checkpoints/2026-07-17-intraday-paper-risk-authority-ko.md
git commit -m "docs: record intraday risk authority"
```

- [x] **Step 6: Confirm remote freshness and push main**

```bash
git fetch origin main
git rev-list --left-right --count main...origin/main
git push origin main
git status --short --branch
```

Expected: no unexpected remote divergence before push; after push local `main` and `origin/main` match and the worktree is clean.
