# 다중 시장 트레이딩 에이전트 Research OS 통합 설계

- 상태: 승인
- 작성일: 2026-07-15
- 제품 경계: 연구, 실시간 추천, shadow 검증, Alpaca Paper 전진검증
- 실제 자금 거래: 금지
- 첫 프로그램 범위: 공통 식별·신호 계약부터 KR Theme Opportunity + Day Shadow 수직 경로까지
- 첫 코드 마일스톤: 공통 식별·신호 계약만 추가하며 DB·실행 동작은 변경하지 않음

> 상위 제품·데이터·상시 운영 아키텍처는 [2026-07-17 기관형 다중 시장 Quant Research OS 설계](2026-07-17-institutional-multi-market-quant-research-os-design.md)가 확장한다. 이 문서는 이미 구현된 `MarketId`, `AgentFamily`, `StrategyLaneRef`, Opportunity·TradeSignal과 composite experiment 계약의 권위를 계속 가진다.

## 1. 제품 정의

이 제품은 하나의 만능 트레이딩 봇이 아니다. 서로 다른 연구 임무를 가진 복수 에이전트가 전략을 독립적으로 만들고, 같은 검증 커널에서 실험하며, 증거에 따라 승격·강등되는 다중 시장 Trading Research OS다.

사용자가 보는 핵심 결과는 다음 두 가지다.

1. 현재 시점에 관찰 가능한 데이터만 사용한 종목 후보와 진입 조건
2. 그 추천을 만든 전략 버전의 forward 성과와 현재 lifecycle 상태

Paper 주문은 추천의 체결 가능성을 검증하는 하류 기능이다. 종목 발굴과 신호 품질이 제품의 중심이며, Paper 체결 자체가 제품 목표는 아니다.

## 2. 채택한 구조와 대안

### 2.1 기각: 전략마다 독립 제품·저장소

시장과 전략마다 별도 제품을 만들면 데이터 인과성, 실험 원장, Reviewer, 승격 기준이 중복된다. 전략 간 비교 통화도 달라져 전체 시도 수와 다중검정을 정직하게 계산하기 어렵다.

### 2.2 기각: 하나의 거대 자율 에이전트

뉴스 분류, 종목 선정, 진입, 비중, 주문, 평가를 한 에이전트가 모두 결정하면 어느 단계가 성과를 만들었는지 분리할 수 없다. LLM의 비결정적 판단도 replay와 반증을 훼손한다.

### 2.3 채택: 모듈러 모놀리스 + 공통 검증 커널

한 저장소와 하나의 전역 실험 계보를 유지하되 시장 도메인과 에이전트 패밀리를 계약으로 분리한다. 배포·부하·소유권이 실제로 독립되어야 할 때만 프로세스나 서비스 경계를 추가한다.

이 방식은 다른 제품을 딥리서치할 때도 적용한다. 외부 연구는 바로 기능이 되는 것이 아니라 `출처가 있는 근거 → 반증 가능한 가설 → 고정 계약 → 수직 실험 → forward 증거` 순서로 제품에 들어온다.

## 3. 상위 좌표계

모든 연구 대상을 다음 계층으로 식별한다.

```text
Market Domain
└── Agent Family
    └── Strategy Lane
        └── Strategy Version
            └── Experiment Trial
```

### 3.1 Market Domain

- `us_equities`: 미국 주식·ETF
- `kr_equities`: 한국 주식·ETF

시장 도메인은 달력, 종목 식별자, 데이터 공급자, 비용, 세금, 체결 제약, 위험 게이트를 소유한다. 서로 다른 시장의 원시 데이터와 실행 원장은 공유하지 않는다.

### 3.2 Agent Family

- `opportunity_manager`: 뉴스·테마·수급·기술 조건으로 후보 종목과 근거를 발굴한다.
- `day_trading`: 당일 진입·청산 조건을 연구한다.
- `swing_trading`: 수일·수주 상태와 오버나이트 위험을 연구한다.
- `systematic_quant`: 논문 재현, 팩터, 로테이션, 평균회귀, 추세, 변동성 타기팅과 레버리지 전략을 연구한다.
- `market_context`: 시장 국면을 관찰하고 다른 전략이 사용할 시점 고정 context를 발행한다.
- `allocation_manager`: 승격된 복수 전략의 확정 일일 snapshot만 읽어 다음 세션 위험예산을 배분한다.

`execution_engine`과 `loop_engineer`는 Agent Family가 아니다. 전자는 승인된 Paper intent를 집행하는 공통 인프라이고, 후자는 실험 lifecycle을 운영하는 control plane이다.

### 3.3 Strategy Lane

Strategy Lane은 독립적인 가설 계보와 평가 단위다. 예시는 다음과 같다.

```text
us_equities/opportunity_manager/news_catalyst
us_equities/day_trading/orb
us_equities/swing_trading/earnings_momentum
us_equities/systematic_quant/leveraged_trend
kr_equities/opportunity_manager/theme_momentum
kr_equities/day_trading/theme_vwap_pullback
```

지표 하나, 데이터 소스 하나, 내부 함수 하나를 lane으로 만들지 않는다. 독립적으로 반증하고 승격할 수 있는 매매 또는 발굴 가설만 lane이 된다.

기존 `LaneId`는 현재 실행·원장 격리 경계를 나타내므로 제거하거나 의미를 바꾸지 않는다. 새 `StrategyLaneRef`는 연구 좌표이며 선택적인 `LegacyExecutionLaneBinding`으로만 기존 실행 lane과 연결한다.

```text
us_equities/day_trading/*       → intraday_momentum
us_equities/swing_trading/*     → swing_momentum
us_equities/market_context/*    → market_regime
kr_equities/*                   → binding 없음
```

binding이 없다는 것은 연구·추천·shadow가 불가능하다는 뜻이 아니라 broker 주문권한이 없다는 뜻이다.

## 4. 시장별 에이전트 구성

### 4.1 US Market Domain

#### US Opportunity Manager

KIS·Alpaca 시세, 가격·거래량 랭킹, 뉴스, 테마, 수급 proxy를 시점 고정 후보로 만든다. 현재 구현의 KIS 상위 랭킹 scanner는 이 에이전트의 첫 데이터 소스이며 미국 전체시장 원시 스트림으로 과장하지 않는다.

#### US Day Trading Agent

현재 ORB, VWAP reclaim, HOD breakout, Gap-and-Go를 소유한다. 최신 완료 1분봉과 현재 quote로 조건부 진입가, 손절, 목표, 유효시간을 산출한다.

#### US Swing Trading Agent

다중 세션 상태기계를 사용한다. 신고가 모멘텀, 실적·뉴스 후 추세, 테마 지속, RVOL 전략을 독립 lane으로 연구한다. intraday의 overnight boolean을 재사용하지 않는다.

#### US Systematic Quant Agent

논문별 재현 trial과 실제 적용 trial을 분리한다. 레버리지는 signal과 별개의 `LeveragePolicy` 계약으로 고정하며 gross/net exposure, 변동성 목표, financing cost, turnover, drawdown과 deleveraging 규칙을 포함한다.

첫 유니버스는 미국 주식·ETF다. 옵션·선물·암호화폐는 별도 시장·체결 계약 없이는 추가하지 않는다.

### 4.2 KR Market Domain

KR 시장은 미국 에이전트의 하위 옵션이 아니라 독립 Market Domain이다. 공통 검증 커널만 재사용하고 데이터·달력·비용·체결 제약은 분리한다.

#### KR Theme Opportunity Manager

기존 [한국 테마주 Shadow 연구 Lane 설계](2026-07-15-kr-theme-lane-design.md)의 1~2층을 소유한다.

- 뉴스·DART 공시·KIS 국내 랭킹·거래량 급증 raw-first 수집
- `published_at`과 `observed_at` 분리
- LLM 테마 분류와 키워드 baseline 병렬 기록
- 테마 신선도·전파도·관련 종목·규칙 기반 대장주 projection

LLM은 분류만 수행하고 종목 진입, 수량, 손절 또는 승격을 결정하지 않는다.

#### KR Day Trading Agent

첫 구현은 테마 첫 눌림, 대장주 2파, 후발주 순환을 shadow로 평가한다. 상하한가, VI, 단일가, 거래정지와 투자경고 상태를 확인할 수 없으면 신호와 shadow 체결을 차단한다.

#### KR Swing/Systematic Quant Agent

상위 골격에는 등록하지만 첫 구현 범위에는 포함하지 않는다. forward KR 원장이 쌓인 뒤 별도 가설·스펙으로 시작한다.

KIS 국내 주문·잔고·계좌 mutation은 금지한다. 첫 KR vertical은 추천과 shadow 결과만 만든다.

## 5. 에이전트 간 계약

### 5.1 Opportunity Snapshot

Opportunity Manager는 주문이나 최종 매매 결정을 만들지 않는다. 다음 immutable snapshot을 발행한다.

```text
market_id
opportunity_id
observed_at
valid_until
symbols
rank
evidence_refs
feature_values
data_freshness
source_coverage
producer_strategy_version
```

`evidence_refs`는 append-only 원장의 canonical key만 포함한다. 원문 뉴스, 자격증명, 계좌 식별자는 외부 카드에 포함하지 않는다.

### 5.2 Trade Signal

Day, Swing, Systematic Agent는 다음 공통 envelope를 발행한다.

```text
market_id
agent_family
strategy_lane_id
strategy_version
signal_id
symbol
observed_at
entry_type
entry_price_or_trigger
quote_observed_at
stop_price
targets
valid_until
invalidation_rule
evidence_refs
opportunity_id (optional)
```

신호는 추천일 뿐 주문권한이 아니다. `quote_observed_at`, spread와 허용 slippage가 유효하지 않으면 “현재 진입 가능”으로 표시할 수 없다.

### 5.3 Composite Experiment

에이전트 조합은 사후 혼합하지 않는다. Opportunity Manager의 후보를 Trading Agent가 소비하면 두 고정 버전과 결합 규칙을 개장 전에 새 실험으로 등록한다.

예시:

```text
KR theme classifier v2
+ KR theme leader projection v1
+ KR theme VWAP pullback v1
= composite experiment KR-THEME-DAY-001
```

동시에 각 구성 요소의 coverage, ranking quality와 entry quality를 별도로 기록해 성과 기여도를 진단한다.

## 6. Loop Engineer

Loop Engineer는 코드를 스스로 덮어쓰는 무제한 자가수정 봇이 아니다. 불변 전략 버전과 사전등록 trial을 만드는 통제된 연구 control plane이다.

```text
근거 수집
→ 가설 등록
→ Strategy Version 고정
→ Historical/Replication Trial
→ Shadow Forward Trial
→ Broker Paper Forward Trial (권한 있는 US lane만)
→ Independent Reviewer
→ Lifecycle Controller
→ 다음 세션 승격·유지·중단·강등
→ 실패 원인에서 다음 가설 등록
```

### 6.1 lifecycle

현재 intraday v1 상태와 저장 schema는 그대로 유지한다.

```text
IDEA
→ HISTORICAL
→ EXPERIMENTAL_SHADOW
→ EXPERIMENTAL_PAPER
→ CHALLENGER
→ PAPER_CHAMPION
↔ SUSPENDED
→ REJECTED
```

다만 신호·shadow 전용 agent를 `PAPER_CHAMPION`이라고 부를 수 없으므로 lifecycle v2에 `SHADOW_CHAMPION`을 추가한다.

```text
EXPERIMENTAL_SHADOW → CHALLENGER → SHADOW_CHAMPION
EXPERIMENTAL_SHADOW → EXPERIMENTAL_PAPER → CHALLENGER → PAPER_CHAMPION
```

v2 schema와 전이 정책이 구현되기 전까지 KR 전략은 `CHALLENGER`까지만 갈 수 있고 `allocation_eligible=false`를 유지한다. 상태 이름만으로 주문권한이 생기지 않으며, 기존 intraday chain을 소급 재작성하지 않는다.

### 6.2 승격 증거

- point-in-time 데이터와 데이터 lineage
- 거래비용·세금·slippage·financing cost
- OOS 또는 walk-forward 결과
- forward shadow 결과
- 가능한 lane의 broker Paper와 conservative shadow 동시 결과
- 표본 수와 coverage
- block bootstrap, DSR/PBO, parameter plateau
- 시장 국면·종목 cohort별 붕괴 여부
- 운영 incident와 censored 표본

자동 승격은 정책이 완성되기 전까지 닫아 둔다. 자동 조기중단과 강등도 정확한 정책 버전과 증거 key가 있을 때만 다음 세션부터 적용한다.

## 7. Systematic Quant 논문 연구 계약

논문을 읽었다는 사실과 전략이 검증됐다는 사실을 분리한다.

1. `ResearchSource`: DOI/URL, 발표일, 데이터 기간, 주장과 한계를 기록한다.
2. `PaperReplicationTrial`: 논문 조건을 최대한 재현한다.
3. `ApplicabilityTrial`: 현재 point-in-time 데이터와 보수적 비용으로 다시 검증한다.
4. `ForwardTrial`: 고정된 버전을 shadow로 전진검증한다.
5. 레버리지 전략은 무레버리지 baseline과 같은 신호·같은 기간으로 비교한다.

논문 내 성과, synthetic leverage 또는 과거 재분류된 LLM 결과는 Paper Champion 증거가 될 수 없다.

## 8. Allocation Manager

Allocation Manager는 종목 발굴 Manager와 다르다. 최소 두 개의 독립 champion이 생긴 뒤 구현한다.

- 확정된 전일 `LaneDailySnapshot`만 읽는다.
- 다음 세션의 lane별 위험예산을 출력한다.
- 종목 신호를 만들거나 주문을 제출하지 않는다.
- allocation 방법도 equal-risk, volatility targeting, regime-aware 같은 독립 Strategy Lane과 trial로 검증한다.
- 동일 종목·테마·팩터 노출 상관을 보수적으로 계산하고 입력이 불완전하면 배분하지 않는다.

## 9. 저장과 프로세스 경계

### 9.1 공유

- global append-only experiment ledger
- strategy version과 trial lineage
- independent Reviewer 계약
- lifecycle transition 정책
- artifact hashing과 redacted reporting

### 9.2 시장·실행별 분리

- raw market/news/catalyst ledger
- candidate와 signal ledger namespace
- 거래 달력과 session clock
- execution/shadow fill ledger
- account binding과 Writer lease
- 비용·세금·위험 계약

SQLite는 현재 단일 호스트 append-only 원장과 query-only Reviewer에 적합하므로 유지한다. 원격 다중 writer나 고가용성이 실제 요구가 되기 전에는 분산 데이터베이스로 옮기지 않는다.

## 10. 오류와 불완전 데이터

- 입력 source 하나의 실패를 성공 cycle로 축약하지 않는다.
- 데이터 freshness, quote, 시장 특수 상태를 확인할 수 없으면 해당 signal을 발행하지 않는다.
- 중도절단 결과는 수익 0이 아니라 `censored`다.
- 동일 evidence key의 immutable 내용이 다르면 충돌로 차단한다.
- LLM 분류 실패는 규칙 전략이 추정으로 보간하지 않는다.
- 한 lane 실패가 다른 시장 원장이나 champion 상태를 변경하지 않는다.

## 11. 테스트 전략

### 11.1 계약 테스트

- Market/Agent/Lane 조합과 canonical ID
- US·KR 시장 계약 혼용 거부
- Opportunity Snapshot과 Trade Signal의 시각·가격·근거 검증
- composite experiment 사전등록과 version binding
- Paper 권한 없는 lane의 execution binding 거부

### 11.2 replay/live parity

같은 저장된 입력, 같은 전략 버전, 같은 시장 계약은 replay와 live projection에서 같은 신호를 만들어야 한다. LLM은 live 분류 결과를 저장한 뒤 replay에서는 호출하지 않는다.

### 11.3 수직 E2E

- US KIS 후보 → ORB signal → local alert → shadow outcome
- KR catalyst → stored theme classification → opportunity → day shadow signal
- trial registration → terminal evidence → Reviewer → lifecycle projection

### 11.4 필수 검증

변경 범위의 pytest, 전체 pytest, Ruff, basedpyright, CLI `--help`, 오입력, fixture happy path를 실행한다. 무거운 데이터 작업은 한 번에 하나만 실행하고 RSS 10 GiB 전에 중단한다.

## 12. 점진적 구현 순서

### Milestone 1 — 공통 제품 계약

- `MarketId`, `AgentFamily`, `StrategyLaneRef`, `AgentManifest`
- `OpportunitySnapshot`, `TradeSignalEnvelope`
- legacy `LaneId`를 보존하는 `LegacyExecutionLaneBinding`
- 네트워크·DB migration·실행 변경 없음

### Milestone 2 — US 실시간 Opportunity vertical

- 현재 KIS scanner 출력을 Opportunity Snapshot으로 투영
- 현재 네 intraday 전략 신호를 공통 Trade Signal로 투영
- quote freshness와 알림 시점 체결 가능성 표시
- local outbox에서 외부 전달 adapter까지 연결

### Milestone 3 — KR Theme T0–T1

- KR raw catalyst append-only ledger와 coverage
- 저장형 LLM/keyword 분류 계약과 theme projection
- KR Opportunity Snapshot 발행
- 주문 없음

### Milestone 4 — KR Theme Day Shadow

- KR 위험 게이트와 보수적 shadow fill
- theme pullback/second-wave/rotation 전략
- composite experiment와 일일 Reviewer 연결

### Milestone 5 — US Swing vertical

- 실제 다중 세션 signal engine과 상태 원장
- Opportunity 입력, alert와 shadow outcome
- 별도 Paper 계좌는 shadow 검증 후 별도 승인으로만 연다.

### Milestone 6 — Systematic Quant Research vertical

- ResearchSource와 논문 replication/applicability trial
- 첫 무레버리지 baseline과 leveraged trend challenger
- financing·turnover·deleveraging 평가

### Milestone 7 — Loop Engineer 확장

- shadow-only 전략을 위한 lifecycle v2와 `SHADOW_CHAMPION`
- comparison-ready, promotion review와 recovery 정책
- 새 가설 queue와 실패 원인 feedback
- 자동 코드 생성은 review 가능한 patch와 새 immutable version까지만 허용

### Milestone 8 — Allocation Manager

- 최소 두 lane champion 이후에만 시작
- 확정 snapshot 기반 next-session allocation

## 13. 첫 구현의 완료 기준

1. 기존 실행 schema와 `LaneId` 동작을 바꾸지 않는다.
2. 미국·한국 시장과 agent family를 하나의 canonical reference로 표현한다.
3. 잘못된 시장/agent/lane 조합은 모델 생성 시 거부된다.
4. Opportunity와 Trade Signal은 observed time, expiry, provenance와 producer version 없이는 생성되지 않는다.
5. 기존 intraday recommendation을 손실 없이 공통 signal envelope로 투영할 수 있다.
6. KR Theme 스펙을 첫 KR Opportunity/Day composite experiment로 등록할 수 있다.
7. 공통 계약은 broker, credential, HTTP 또는 execution mutation 모듈을 import하지 않는다.
8. 기존 전체 테스트, Ruff와 basedpyright가 통과한다.

## 14. 비목표

- 기존 코드를 한 번에 `core/`로 이동하는 big-bang refactor
- agent마다 별도 서비스·데이터베이스를 즉시 배포
- LLM의 재량 매매 또는 승격 결정
- 한국 계좌·주문 경로
- 실제 자금 거래
- 최소 두 champion 이전 Allocation Manager 구현
- 백테스트 결과를 확정수익 또는 제품 신뢰도로 표현

## 15. 설계 원칙 요약

- 에이전트는 연구 임무로 나누고 내부 처리 단계마다 만들지 않는다.
- 시장은 실행 현실이 다르면 분리하고 검증 언어만 공유한다.
- 에이전트 조합은 새로운 사전등록 실험이다.
- 딥리서치는 가설의 출처이지 성과 증거가 아니다.
- 추천 품질과 실행 검증을 분리하되 lineage로 연결한다.
- 전략 버전은 불변이고 승격·강등은 증거 event로만 발생한다.
- 현재 구현을 보존하며 수직 경로 하나씩 확장한다.
