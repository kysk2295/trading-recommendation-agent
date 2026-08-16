# Evidence-First Multi-Market Trading Research OS

> 미국·한국 시장 데이터를 수집하고, 자율 리서처가 가설과 Python 전략을 생성하며,
> 모든 판단을 재현 가능한 evidence와 append-only history로 남기는 Quant Research OS

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![Paper Only](https://img.shields.io/badge/Execution-Alpaca%20Paper%20Only-2E8B57)](https://docs.alpaca.markets/docs/paper-trading/)
[![Fail Closed](https://img.shields.io/badge/Safety-Fail--Closed-C0392B)](#안전-경계)

이 시스템은 종목과 `매수` 문구만 출력하는 봇이 아니다. 외부 데이터 수신부터 후보 발굴,
가설 생성, 격리된 전략 실험, 추천, Alpaca Paper 검증, 독립 Reviewer까지의 전체 경로를
typed contract와 immutable ledger로 연결한다.

- **범위:** US·KR equities, macro, options·futures research context
- **실행:** Alpaca Paper Trading 전용, 실제 자금 거래는 영구적으로 제외
- **다른 공급자:** KIS·LS·OpenDART·SEC 등은 read-only
- **현재 단계:** research prototype, 실제 정규장 Paper mutation `0건`

![Evidence-first Derivatives dashboard](docs/assets/operations-dashboard-derivatives.png)

_현재 구현된 read-only Derivatives workspace. source authority, freshness와 Evidence Trace를
표시한다. 아래의 통합 Options Workbench 5-view UI는 아직 설계·계획 단계다._

## 구현 범위

| 구성 요소 | 구현 내용 | 현재 상태 |
| --- | --- | --- |
| Data plane | Alpaca·KIS·LS·OpenDART·SEC·macro adapter, raw-first receipt, capability gate | 구현 |
| US Day | scanner, ORB·VWAP·HOD·Gap-and-Go, recommendation state machine | 구현, 실제 Paper mutation 미실행 |
| KR Theme Day | 공시·뉴스·랭킹 cycle, theme leader Opportunity, shadow lifecycle | fixture E2E, 연속 실제 세션 미완료 |
| Swing·Systematic | new-high/RVOL, ETF regime rotation, historical·shadow trial | 구현, forward sample 수집 중 |
| Autonomous Researcher | LLM 가설·Python 생성, macOS sandbox, walk-forward, Reviewer feedback | 실제 로컬 cycle QA 완료 |
| Research governance | hypothesis·version·trial preregistration, lifecycle, independent review | 구현 |
| Paper execution | endpoint guard, risk kernel, order/OCO/fill/position reconciliation | 구현, 자연 setup 실증 미완료 |
| Product surface | Hermes delivery ledger, Bun·Hono operations dashboard | 구현, 연속 session acceptance 중 |
| Options Workbench | Alpaca indicative chain·surface·skew 기반 | 데이터 기반 구현, 통합 UI 구현 전 |

`구현`은 코드와 테스트가 존재한다는 뜻이다. 실제 시장 acceptance나 수익성을 의미하지
않는다. 최신 운영 gate는 [제품 마일스톤](docs/milestones_status_ko.md)에 기록한다.

## 시스템 아키텍처

```mermaid
flowchart LR
    subgraph Data["Data Plane"]
        SRC["Alpaca · KIS · LS · OpenDART · SEC · Macro"]
        RAW["Raw Receipt Stores"]
        EVT["Canonical Events"]
        CAP["Capability / Freshness Gate"]
        SRC --> RAW --> EVT --> CAP
    end

    subgraph Research["Research Control Plane"]
        RT["6-Family Runtime"]
        AG["Market Agents"]
        AR["Researcher + Critic"]
        SB["Generated Python Sandbox"]
        EXP["Experiment Ledger"]
        REV["Independent Reviewer"]
        RT --> AG
        RT --> AR --> SB --> EXP --> REV --> RT
    end

    subgraph Signal["Signal Plane"]
        OPP["OpportunitySnapshot"]
        SIG["TradeSignalEnvelope"]
        CARD["Recommendation Card"]
        OPP --> SIG --> CARD
    end

    subgraph Execution["Execution Plane"]
        RISK["Endpoint Guard + Risk Kernel"]
        PAPER["Alpaca Paper Only"]
        REC["Order · Fill · Position Reconciliation"]
        RISK --> PAPER --> REC
    end

    subgraph Product["Product Projection"]
        H["Hermes / Telegram"]
        D["Operations Dashboard"]
        AUDIT["Immutable Outcome History"]
    end

    CAP --> RT
    CAP --> OPP
    AG --> SIG
    CARD --> EXP
    CARD --> RISK
    CARD --> H
    REV --> AUDIT
    REC --> AUDIT
    AUDIT --> D
```

### 계층별 책임

| 계층 | 주요 모듈 | 입력 → 출력 | 불변 조건 |
| --- | --- | --- | --- |
| Provider adapter | `alpaca_*`, `kis_*`, `ls_*`, `opendart_*` | wire bytes → private receipt | exact endpoint, no redirect, bounded response |
| Evidence | `raw_*`, `canonical_*`, `data_capability_*` | receipt → canonical event·dataset | parse 전 원문 확정, point-in-time lineage |
| Agent runtime | `research_agent_*` | evidence cursor → terminal actor cycle | actor isolation, terminal 뒤 cursor 전진 |
| Generated runtime | `generated_strategy_*` | source artifact + bar → signal stream | sandbox-only import, no future frame, deterministic replay |
| Experiment | `experiment_ledger_*`, `lifecycle_*` | hypothesis → trial·review·transition | preregistration, previous-key chain |
| Signal | `signal_contract_models.py`, `engine.py` | opportunity·bar·quote → recommendation | current session, completed bar, valid risk geometry |
| Execution | `paper_*`, `alpaca_paper_*`, `execution_*` | approved intent → reconciled Paper state | exact Paper URL, single writer, ambiguous no-retry |
| Projection | `hermes_*`, `dashboard_*` | terminal record → safe user view | query-only, redacted, no provider mutation |

의존 방향은 `Provider → Evidence → Research/Signal → Experiment/Execution → Product`로
고정한다. Reviewer와 generated code가 provider credential이나 broker client를 직접 호출하는
역방향 의존은 허용하지 않는다.

## 자율 리서처

### 6-family persistent runtime

하나의 macOS LaunchAgent 안에서 여섯 actor가 독립 cursor, open work, wake policy, cooldown을
가진다. 30초 tick은 새 evidence나 due wake만 확인하며 변화가 없으면 LLM을 호출하지 않는다.

| Actor | Wake 조건 | 결과 |
| --- | --- | --- |
| Opportunity Manager | 뉴스·공시·랭킹·이상현상 | 후보, 근거, hypothesis 또는 no-action |
| Market Context | 장전·장중 경계·장마감, regime 변화 | breadth·liquidity·regime context |
| Day Trading | 현재 setup·open recommendation review | entry·stop·targets 또는 no-action |
| Swing Trading | 장마감·catalyst·multi-session review | conditional research·invalidation |
| Systematic Quant | 새 research source·trial·Reviewer feedback | generated Python experiment |
| Derivatives Research | 새 IV·skew·term·futures context | 연구 결과 또는 blocked-by-data |

### Generated Python experiment

```text
evidence / previous failure / Reviewer feedback
→ Researcher: falsifiable hypothesis + Python source
→ Critic: provenance, duplicate, contract review
→ immutable strategy artifact
→ macOS sandbox-exec
→ deterministic replay + host walk-forward evaluator
→ Independent Reviewer
→ immutable feedback for the next cycle
```

- cycle당 proposal 최대 3회, heavy trial artifact 최대 1개
- generated source는 coordinator가 import하지 않고 별도 sandbox process에서만 실행
- 완료된 bar를 하나씩 전달하므로 subprocess에 미래 dataset이 존재하지 않음
- network, socket, credential, home, repository, broker module 접근 차단
- CPU, memory, file, output, wall-clock 제한
- PnL·비용·stop/target 충돌은 generated code가 아니라 host evaluator가 계산
- 동일 입력의 signal hash가 다르면 `non_deterministic_strategy`로 실패
- historical 결과만으로 Paper·promotion·allocation authority를 만들지 않음

실제 fixture-backed CLI 결과:

```text
result: complete
strategy artifacts: 1
experiments: 1
reviews: 1
reviewer decision: hold
lifecycle / allocation / order authority: false
trading mutation: 0
```

관련 진입점은 `run_researcher_propose.py`, `run_autonomous_research_cycle.py`,
`run_research_agent_runtime.py`다. 상세 신뢰 경계는
[자율 Python 전략 루프 설계](docs/superpowers/specs/2026-08-02-autonomous-unrestricted-python-strategy-loop-design.md)에 있다.

## 핵심 데이터 흐름

### US intraday

```text
현재 NYSE session
→ 상승률·거래량 scanner
→ halt·spread·freshness·completed-bar gate
→ ORB / VWAP / HOD / Gap-and-Go
→ entry·stop·1R·2R risk calculation
→ RecommendationCard
→ Shadow 또는 승인된 Alpaca Paper
→ fill·protective OCO·EOD flat reconciliation
→ immutable outcome + Reviewer
```

### KR theme day

```text
OpenDART 공시 + LS NWS + KIS 랭킹
→ raw receipt 선확정
→ exact four-source coverage cycle
→ theme freshness·breadth·거래대금
→ theme leader Opportunity
→ KRX session·quote·VI·가격제한 gate
→ VWAP reclaim shadow signal
→ terminal outcome + Reviewer
```

### 추천 계약

추천은 `timestamp`, `strategy version`, `entry`, `stop`, `targets`, `valid_until`,
`invalidation_rule`, `rationale`, `evidence_refs`를 필수로 가진다. `conditional` 신호와 현재
호가까지 검증된 `current_quote_validated` 신호를 구분하며, outcome history를 덮어쓰지 않는다.

## 저장소와 동시성

상태를 하나의 범용 DB에 합치지 않고 writer 권한과 실패 범위에 따라 분리한다.

| 저장소 | 소유 상태 | 검증 |
| --- | --- | --- |
| Provider receipt stores | HTTP·WebSocket 원문과 source run | raw SHA, schema, lineage |
| Canonical Parquet dataset | typed event와 sidecar | path·inode·schema·SHA 후 DuckDB replay |
| Research cycle journal | trigger, cursor, action, terminal, next wake | interrupted recovery, append-only chain |
| Experiment ledger | hypothesis, version, trial, lifecycle | parent·previous-key, time monotonicity |
| Recommendation store | recommendation과 outcome event | state-machine replay |
| Execution ledger | intent, mutation, fill, safety action | REST·WSS·position reconciliation |
| Hermes delivery store | event, attempt, ACK, dead-letter | exactly-once identity |

- ledger별 non-blocking single-writer lease
- heavy empirical process는 전체에서 한 번에 하나, RSS 10 GiB 이전 중단
- terminal cycle과 next wake가 commit된 뒤 actor cursor 전진
- content-derived identity로 replay는 기존 artifact 재사용, payload conflict는 차단
- WebSocket reconnect는 새 connection epoch로 시작하고 REST 대사 전 readiness 재사용 금지
- Paper mutation은 `ATTEMPTED`를 먼저 commit한 뒤 broker 호출
- ACK가 모호하면 targeted GET으로 확인하며 부재가 입증되지 않으면 자동 재전송 금지

## 안전 경계

- trading base URL은 정확히 `https://paper-api.alpaca.markets`만 허용
- Alpaca live endpoint와 실제 계좌 credential 경로 미지원
- KIS·LS·OpenDART 등 비-Alpaca 공급자는 account·balance·order mutation 금지
- 최신 완료 봉, current session, current date, fresh quote, spread가 없으면 신규 추천 차단
- 동일 봉의 stop/target 충돌은 stop으로 판정
- raw response, credential, token, account identifier를 report·exception에 노출하지 않음
- fixture, replay, backtest, shadow, Paper, actual session 결과를 서로 구분
- lifecycle state만으로 주문 권한을 만들지 않음

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| Backend | Python 3.12, Pydantic v2, AnyIO, HTTPX2, WebSockets |
| Storage | SQLite, PyArrow·Parquet, DuckDB, PostgreSQL(dashboard) |
| CLI·runtime | Typer, Rich, macOS LaunchAgent, `sandbox-exec` |
| Dashboard | Bun, TypeScript, Hono, Zod, WebSocket |
| Quality | pytest, Ruff, basedpyright, Bun test, Biome, Playwright, axe-core |

## 코드 구조

```text
trading-recommendation-agent/
├── trading_agent/
│   ├── research_agent_*.py       # actor runtime, wake, journal, operations
│   ├── generated_strategy_*.py   # artifact, sandbox, protocol, evaluator
│   ├── experiment_ledger_*.py    # hypothesis, trial, lifecycle
│   ├── signal_contract_models.py # Opportunity·TradeSignal contract
│   ├── paper_*.py                # risk, mutation, recovery, reconciliation
│   └── dashboard_*.py            # redacted snapshot projection
├── dashboard/                    # Bun + Hono operations workstation
├── integrations/                 # Hermes product integration
├── examples/                     # credential-free fixtures
├── tests/                        # unit, integration, CLI, E2E
├── docs/                         # architecture, checkpoints, runbooks
├── run_*.py                      # explicit CLI boundaries
├── pyproject.toml
└── uv.lock
```

루트 CLI는 dependency wiring과 입력 parsing만 담당한다. 시장·원장·리스크 로직은
`trading_agent/`에 있고 provider-specific wire model은 canonical domain model과 분리한다.

## 로컬 실행

요구 사항은 Python `3.12+`와 [uv](https://docs.astral.sh/uv/)다. generated Python sandbox는
`/usr/bin/sandbox-exec`가 있는 macOS에서만 실행한다.

```bash
git clone https://github.com/kysk2295/trading-recommendation-agent.git
cd trading-recommendation-agent
uv sync --frozen --group dev
```

Credential 없는 ORB replay:

```bash
DEMO_ROOT="$(mktemp -d)"
uv run python run_trading_agent_replay.py \
  examples/example_intraday.csv \
  --output-dir "$DEMO_ROOT/replay"
find "$DEMO_ROOT/replay" -maxdepth 2 -type f -print
```

이 실행은 `paper_recommendations.sqlite3`, 한국어 report와 alert projection을 만들며 network와
broker mutation을 사용하지 않는다.

대표 CLI:

```bash
uv run python run_trading_agent_replay.py --help
uv run python run_autonomous_research_cycle.py --help
uv run python run_research_agent_runtime.py --help
uv run python run_alpaca_paper_preflight.py --help
```

검증:

```bash
uv run pytest -q
uv run ruff check trading_agent tests run_*.py
uv run basedpyright

cd dashboard
bun install --frozen-lockfile
bun run check
```

## 현재 진행 중

### Autonomous Researcher 운영 안정화

6-family runtime과 sandbox loop는 구현됐다. 현재 source inspection·supply, Systematic input
activation, monitoring, backup·restore, soak evidence와 장기 OOS·shadow 표본을 보강하고 있다.

### Integrated Options Research Workbench

Alpaca option contract·indicative chain·surface·skew·term structure 기반은 구현됐다. 다음 단계는
`#derivatives` 안에 Market Pulse, Unified Chain, Strategy & Agent, Experiment Lab, Promotion &
Operations의 5개 view를 연결하는 것이다. 현재 HEAD에는 설계와 foundation plan만 있으며 전용
model·snapshot·UI는 아직 구현되지 않았다. KIS·LS option adapter도 완료로 표시하지 않는다.

### 실제 시장 acceptance

Hermes 전달, US Paper, KR shadow, Swing lifecycle을 연속 실제 세션에서 관찰 중이다. 자연
setup이나 current data가 없으면 추천·주문을 만들지 않고 `no_recommendation`, `censored`,
`blocked`로 보존한다.

## 현재 한계

- 실제 정규장 Alpaca Paper mutation 0건
- US·KR 연속 session product acceptance 미완료
- generated strategy의 충분한 OOS·shadow sample 미확보
- Options Workbench 통합 UI 구현 전
- 수익 보장, 검증된 투자 성과 또는 production readiness를 주장하지 않음
- LLM의 재량 주문, 자동 risk 변경, 자동 promotion 미지원
- 별도 오픈소스 라이선스 미선언

## 상세 문서

| 문서 | 내용 |
| --- | --- |
| [상세 아키텍처](docs/architecture_ko.md) | scanner, ledger, Paper execution 경계 |
| [제품 마일스톤](docs/milestones_status_ko.md) | 실제 session acceptance와 blocker |
| [Persistent Runtime](docs/checkpoints/2026-08-02-persistent-research-runtime-ko.md) | 6-family runtime checkpoint |
| [자율 Python 전략 설계](docs/superpowers/specs/2026-08-02-autonomous-unrestricted-python-strategy-loop-design.md) | sandbox·artifact·Reviewer 계약 |
| [Options Workbench 설계](docs/superpowers/specs/2026-08-03-integrated-options-research-workbench-design.md) | 통합 옵션 연구 화면 계약 |
| [Alpaca Paper smoke 이력](docs/checkpoints/2026-08-14-paper-closed-session-smoke-skip-ko.md) | 2026-07-15~08-14 Stage 1 실행·안전 중단 체크포인트 |
| [전체 구현 히스토리](IMPLEMENTATION_HISTORY_KO.md) | 기능별 상세 기록과 검증 이력 |

이 저장소의 출력은 교육·연구·Paper Trading을 위한 결과이며 투자 자문이나 수익 보장이 아니다.
