# 한국·미국 자율 데이트레이딩 3중 루프 설계

**작성일:** 2026-08-20
**상태:** 사용자 승인 완료, 세부 구현 계획 확정
**제품 표면:** 사용자에게는 하나의 `Day Trading Agent`로 보인다.
**내부 경계:** `Discovery Lab → Market Forward Shadow → Promotion/Execution` 세 권한으로 분리한다.
**거래 권한:** 이 저장소는 Alpaca Paper 주문만 변경 가능하다. KIS·LS와 그 밖의 provider는 read-only다.

## 1. 결정 요약

Day Trading Agent는 장중에 새로운 가설과 일반 Python 전략 코드를 계속 만들고, 등록 이후의 미래 완료 봉부터
Shadow로 시험한다. 장 마감 뒤에는 실제 Paper 체결 성과, Shadow 연구 성과, 누적 계보, 차단·리스크 사건과
다음 거래일 정책을 보고한다. 다음 거래일 정책은 탐색 자원과 이미 승인된 실행 집합을 선택하지만 당일 생성된
가설을 곧바로 주문 자격으로 바꾸지 않는다.

가설의 표현력과 시스템 권한은 분리한다.

- AI는 정해진 전략 템플릿 밖의 아이디어, 지표, 상태, 진입·청산 논리와 코드를 만들 수 있다.
- 생성 코드는 기존 격리 runner 안에서 계산만 수행하며 provider, credentials, Risk Kernel 또는 broker에 접근하지 않는다.
- 신규 가설은 사전등록과 sandbox preflight 뒤 당일 Forward Probe Shadow를 시작할 수 있다.
- 주문은 장 시작 전에 `PAPER_TRIAL_APPROVED` 또는 `PAPER_CHAMPION` 권한으로 승인·봉인되어 그 세션에
  유효한 `StrategyCapsule`만 사용할 수 있다.
- AI는 실행 가능한 capsule 중 하나를 선택하거나 `no_trade`를 선택할 수 있지만 가격, 수량, 리스크 한도 또는 broker 요청을 만들지 않는다.
- 미국장과 한국장은 같은 아이디어 계보를 공유할 수 있지만 raw 성과, 통계 검정과 승격 자격은 절대 합치지 않는다.

이 설계는 기존 미국장 전용 계획의 고정 전략 선택 범위와 US-only 전제를 대체한다. 기존 Paper endpoint,
Risk Kernel, arm, OCO, flatten, reconciliation, append-only audit와 시장 시간 안전 규칙은 대체하지 않는다.

## 2. 사용자에게 보이는 결과

사용자는 하나의 Day Trading Agent에서 다음을 본다.

1. **장중 연구:** 지금 생성·검토·시험 중인 가설, 가설이 나온 이유, 현재 단계와 차단 이유
2. **장중 거래:** 사전 승인된 전략의 Paper 주문·체결·보호 주문 상태와 Shadow 거래 상태를 명확히 분리한 표시
3. **장 마감 보고:** 시장별 일일 수익, 누적 성과, 가설별 결과, 실패·검열·데이터 품질과 리스크 사건
4. **다음 거래일 준비:** 다음 세션에 더 탐색할 가설과 실제 Paper 주문이 가능한 capsule 집합
5. **계보:** 어떤 부모 가설에서 무엇을 변경했고 시장별 증거가 어떻게 누적되었는지

`Day Trading Agent`는 하나의 사용자 read model이다. 하나의 LLM 프로세스나 하나의 권한 주체를 뜻하지 않는다.

## 3. 범위와 비범위

### 3.1 이번 설계의 범위

- 미국·한국 intraday 가설의 자유 생성과 중복·누수·구성 가능성 비평
- 생성된 일반 Python 전략의 불변 artifact화와 격리 실행
- 사전등록 이후 미래 봉에서의 시장별 Forward Probe/Shadow
- 모든 연구 시도, 실패와 정책 결정을 포함하는 불변 계보
- 시장별 검증과 고정된 review window에서의 승격 심사
- 미국 Alpaca Paper 실행과 실제 fill 기반 결과 귀속
- 한국 KIS·LS read-only 데이터 기반 Shadow 실행
- 시장별 장 마감 보고와 다음 정규 세션 탐색 정책
- 공통 사용자 화면에서 미국과 한국 최신 상태를 함께 보여주는 projection

### 3.2 명시적 비범위

- Alpaca live endpoint, live credentials 또는 실자금 주문
- KIS·LS의 주문, 계좌, 잔고, 포지션 변경 또는 account WebSocket 등록
- 이 저장소 안의 한국 실계좌 주문 adapter, credential 또는 activation path
- 백테스트·replay·synthetic 결과의 수익성 주장
- 당일 최고 수익 전략의 자동 승격
- 미국·한국 raw 수익률을 합친 통계 검정 또는 한 시장 증거로 다른 시장 주문 자격 부여
- 생성 코드의 package 설치, 외부 네트워크, repository·사용자 홈·credentials 접근
- 전체 주식 universe backtest 또는 두 개 이상의 heavy empirical process 동시 실행
- swing, systematic, derivatives의 실행 의미를 Day 상태 기계로 통합

한국 실주문은 장기 제품 목표가 될 수 있지만 이 저장소의 확장이 아니다. 별도 프로젝트에서 별도 security policy,
credentials, broker reconciliation과 운영 승인을 갖춘 뒤 새로 설계해야 한다. 이 저장소는 해당 경로를 미리 구현하거나
우회용 generic broker interface를 추가하지 않는다.

## 4. 검토한 구조와 채택 이유

### 4.1 시장별 시스템을 완전히 복제

시장 특성을 보존하기 쉽지만 가설 계보, 생성 코드 격리, 다중 검정, 보고 계약이 중복되고 서로 다른 의미로 변한다.
공통 연구 방법론을 두 번 구현해야 하므로 기각한다.

### 4.2 하나의 범용 Day Agent가 연구와 주문을 모두 소유

사용자 개념은 단순하지만 가설을 만든 주체가 검증과 승격, 주문까지 자기 승인하게 된다. 시간대, 거래 비용,
한국 VI·동시호가·가격제한과 미국 Paper reconciliation도 한 상태 기계에 섞인다. 권한과 장애 반경이 과도해 기각한다.

### 4.3 공통 Discovery Lab + 시장별 Shadow + 독립 실행 권한

가설 생성과 연구 기억은 공유하고, 시계·데이터·비용·시장 규칙·실행 권한은 시장 adapter에 둔다. 자유로운 연구와
실행 안전을 동시에 만족하며 기존 코드의 science kernel, generated-code sandbox, 미국 Paper controller와 한국 Shadow
lifecycle을 재사용할 수 있으므로 채택한다.

## 5. 상위 아키텍처

```text
Market evidence (US/KR, point-in-time)
    ↓
Shared Discovery Lab
    ├─ Hypothesis Generator
    ├─ Research Developer
    └─ Critic / Methodologist
    ↓ immutable hypothesis + generated artifact
Strategy Capsule Builder
    ↓
    ├─ US Forward Probe / Shadow ─┐
    └─ KR Forward Probe / Shadow ─┤
                                  ↓
                       Market-scoped Review Gate
                                  ↓
                       PromotionDecision (no order yet)
                                  ↓
                    Session ExecutionEligibility
                         ├─ US: Alpaca Paper only
                         └─ KR: always non-mutable/read-only
                                  ↓
                    MarketCloseReport / DailyLearningReport
                                  ↓
                    next-session ExplorationPolicy
                                  └──────────────→ Discovery Lab
```

세 루프는 서로 다른 속도로 움직인다.

| 루프 | 시간 단위 | AI 권한 | 결정론적 권한 |
|---|---|---|---|
| Discovery | 새 완료 봉·새 evidence·장 마감 feedback | 가설·코드·실험 제안 | 사전등록, sandbox, resource budget, ledger |
| Forward Learning | 시장별 완료 봉·세션 | 승인된 Shadow 슬롯 선택 | 시계, 데이터, 비용, signal 검증, outcome |
| Execution | 시장별 세션 | 실행 가능 capsule 선택 또는 abstain | risk, sizing, arm, endpoint, broker, OCO, reconcile |

논리적 AI 역할은 별도 상주 프로세스를 요구하지 않는다. 기존 Research OS가 역할별 bounded prompt와 한 cycle의
한 primary action으로 실행할 수 있다. 권한 분리는 prompt 문구가 아니라 입력 계약과 writer 경계로 강제한다.

기존 독립 research identity, cursor와 cadence는 진단·스케줄 ownership으로 유지한다. 이 identity를 허용 전략 목록으로
사용하지 않는다. `created_by=day_discovery`가 자유로운 family/version을 만들고, open `methodology_tags`와 target horizon에
따라 기존 market methodology adapter가 평가를 소유한다. 필요한 point-in-time evidence를 어떤 adapter도 구성할 수 없으면
아이디어 종류 때문에 거부하는 것이 아니라 `source_unconstructible`로 기록한다. 하나의 Day façade는 기존 research
runtime을 폐기하거나 cursor를 합치지 않는다.

## 6. 핵심 불변 계약

### 6.1 `HypothesisFamily`

시장에 종속되지 않는 경제적 메커니즘의 계보다.

- `family_id`
- `parent_family_id`
- canonical question과 economic mechanism
- alternative explanations와 counterfactual baseline
- 생성 actor, 생성 시각과 source lineage

같은 메커니즘의 한국·미국 버전은 같은 family를 가질 수 있다. Family 자체에는 성과, 승격 또는 주문 권한이 없다.

### 6.2 `HypothesisVersion`

특정 시장에서 검증 가능한 정확한 주장이다.

- `hypothesis_version_id`, `family_id`, `parent_version_id`
- `market_id`, universe snapshot, point-in-time source refs
- open `methodology_tags`와 primary evaluation owner/cadence
- predictor, sampling timestamp, target, horizon과 expected direction
- entry, exit, stop, invalidation과 cost/slippage model
- free parameters, search budget와 multiple-testing family
- model, prompt, code, data manifest와 protocol hashes
- `created_at`, `first_shadow_eligible_at`
- `trading_authority=false`, `profitability_claim=false`

predictor, target, threshold, parameter 후보, 비용, 코드 또는 데이터 manifest를 바꾸면 새 version이다. 미국 버전을
한국 규칙에 맞게 번역한 것도 같은 family의 새 version이며 같은 통계 표본이 아니다.

`market_id`는 version 이후의 attempt, capsule, trial, outcome, review seal, promotion, policy, report와 authority event에
필수다. 모든 foreign key는 same-market을 검증한다. KR evidence ref가 US dossier에 들어가거나 반대인 경우 publication
전에 거부한다. `HypothesisFamily`만 market-neutral이며 성과나 authority foreign key의 대상이 아니다.

### 6.3 `ResearchAttempt`

기존 `ResearchAttempt`를 재사용하되 `market_id`, `hypothesis_version_id`와 capsule artifact ref를 결합한다. started, succeeded,
failed, aborted, timed_out, cancelled와 censored를 모두 기록한다. 실패한 시도도 multiple-testing family의 시행 횟수와
resource budget에서 사라지지 않는다.

### 6.4 `StrategyCapsule`

Shadow와 실행 경계가 해석할 수 있는 불변 전략 배포 단위다.

- capsule ID와 exact hypothesis version
- builtin 또는 generated artifact identity
- source, runtime, sandbox profile, protocol과 evaluator hashes
- 허용 market, bar cadence와 필요한 evidence schema
- entry/exit/stop/target projection 규칙
- cost/slippage declaration과 maximum resource limits
- authority ceiling(`RESEARCH_ONLY | US_ALPACA_PAPER_CAPABLE`)과 risk-policy reference
- deterministic replay digest
- publication timestamp와 `trading_authority=false`

생성 코드가 내보내는 것은 signal 후보뿐이다. Host가 timestamp, symbol, finite prices, completed-bar lineage, entry/stop
관계와 signal schema를 검증하고 target, 비용, 충돌과 outcome을 계산한다.

### 6.5 `ForwardTrial`

한 capsule을 한 시장의 미래 데이터에서 시험하는 append-only trial이다.

- trial ID, capsule ID, market ID와 execution lane
- preregistered time, first eligible completed bar와 session
- source/evidence hashes와 cost model
- signal, entry, exit, no-signal, blocked와 censored events
- terminal reason과 immutable outcome refs

`first_eligible_completed_bar`는 가설 등록에 사용한 봉보다 반드시 뒤다. Historical warm-up은 허용하지만 과거 봉에서
새 recommendation이나 수익을 만들 수 없다.

### 6.6 `PromotionDecision`

연구 증거가 market-scoped 후보 자격을 만족하는지 판단하는 불변 심사다.

- market, capsule, fixed review window와 evidence seal
- historical/holdout, forward, cost와 data-quality evidence refs
- trial count, attempted variants와 selection-adjusted statistics
- integrity blockers와 reviewer version
- promotion policy version과 preregistered power/CI sufficiency 결과
- `REJECTED | INSUFFICIENT | SHADOW_CANDIDATE | PAPER_TRIAL_CANDIDATE | PAPER_CHAMPION_CANDIDATE`
- owner approval receipt가 필요한 상태와 effective-after session

`PromotionDecision`은 주문 권한이 아니다. 미국 `PAPER_TRIAL_CANDIDATE`는 제한된 broker-Paper evidence 수집을 위한
후보이고, `PAPER_CHAMPION_CANDIDATE`는 그 Paper evidence까지 포함한 최종 후보다. 둘 다 별도의 immutable owner
approval과 authority event가 필요하다. 한국은 이 저장소에서 `SHADOW_CANDIDATE`까지만 가능하다.

### 6.7 `ExecutionEligibility`

세션 시작 전에 만들어지는 별도 권한 artifact다.

- market, session, capsule와 promotion decision IDs
- exact strategy version, clean commit와 risk contract hash
- execution lane, authority class(`PAPER_TRIAL_APPROVED | PAPER_CHAMPION`), authority event와 expiry
- `ELIGIBLE | BLOCKED | SUSPENDED | EXPIRED`
- deterministic reason codes

미국 `ELIGIBLE`은 Alpaca Paper에만 적용된다. 한국 artifact는 provider read-only 경계를 명시하며 항상 broker mutation에
대해 `BLOCKED`다. Eligibility가 있어도 현재 bar, quote, spread, portfolio risk와 one-use arm gate는 주문마다 다시 검사한다.

Capsule과 과거 approval은 불변이지만 현재 권한은 append-only projection이다. risk-policy/commit/account binding 변경,
drift, integrity failure, suspension 또는 revocation event가 발생하면 다음 entry부터 즉시 `BLOCKED`다. Cancellation,
protective OCO, reconciliation과 same-day flatten 권한은 신규 entry suspension과 별도로 유지한다.

### 6.8 `OrderIntent`

공통 이름은 연구·Shadow·Paper attribution을 위한 envelope다. 생성 코드는 만들지 않는다.

- capsule, hypothesis version, trial과 promotion/eligibility refs
- market, lane, symbol, side, timestamp, validity window
- entry, stop, targets, rationale와 evidence refs
- risk-declared maximum, admission proof ref와 immutable intent ID

미국에서만 검증된 `OrderIntent`를 기존 `PaperOrderIntent`/`PaperOrderAdmissionRequest`로 투영한다. 한국에서는 같은
정보를 Shadow fill model에만 사용하며 provider request로 변환하는 adapter가 존재하지 않는다. Current-bar/risk gate
결정은 intent를 수정하지 않고 별도 append-only decision event로 기록한다.

### 6.9 `ExplorationPolicy`, `MarketCloseReport`와 `DailyLearningReport`

`ExplorationPolicy`는 시장별 다음 정규 세션에 유효하며 active Forward Shadow capsule, 대기 queue 순서, 슬롯 budget,
중단 목록과 `no_trade`를 담는다. Risk parameter, strategy source, promotion 또는 execution eligibility를 바꿀 수 없다.

권위 `MarketCloseReport`는 market/session을 identity로 하고 실제 execution, Shadow research, cumulative lineage,
risk events, 다음-session policy와 eligibility snapshot을 별도 section으로 가진다. `DailyLearningReport`는 하나의
사용자 façade를 위한 query-only projection으로, 각 시장의 최신 verified `MarketCloseReport` ID와 Discovery summary를
연결한다. 서로 다른 시장·lane의 수익을 하나의 수익률로 계산하지 않으며 policy나 promotion의 입력이 될 수 없다.

## 7. Discovery Loop

### 7.1 생성 trigger

다음 사건이 새 cycle을 열 수 있다.

- 시장별 새 latest completed bar와 fresh quote/spread
- point-in-time 뉴스·공시·ranking·context evidence
- 열린 trial의 terminal outcome 또는 integrity failure
- fixed review window 종료와 장 마감 feedback
- 다음-session ExplorationPolicy의 due item

한 cycle은 한 bounded evidence view에서 최대 3개 draft를 비평하고 최대 한 개의 primary proposal만 artifact화한다.
실패한 draft도 attempt budget에 남는다. Proposal queue 크기는 전략 종류로 제한하지 않지만 cycle, CPU와 동시 Shadow
슬롯은 제한한다.

### 7.2 생성·비평·artifact화

```text
evidence → propose → critic → preregister → artifactize → sandbox preflight
         → deterministic replay check → Forward Probe admission 또는 rejection
```

Critic은 다음을 검사한다.

- target과 predictor가 현재 source로 실제 구성 가능한가
- 미래 정보, revised data, survivor bias 또는 target leakage가 없는가
- 기존 family/version과 의미상 중복인가
- 실패 판정과 baseline이 사전에 고정되었는가
- search/multiple-testing/resource budget이 남아 있는가
- 코드가 sandbox와 frame protocol 안에서 결정론적으로 실행되는가

비평 실패, 컴파일 오류, sandbox 오류와 비결정성도 terminal attempt다. 같은 source를 조용히 고쳐 덮어쓰지 않는다.

### 7.3 당일 Forward Probe

사전등록, artifact publication, sandbox preflight와 deterministic replay가 성공하면 historical support 전에도 bounded
`FORWARD_PROBE` 슬롯에 들어갈 수 있다. 이는 사용자가 원하는 장중 활발한 가설 시험을 위한 연구 전용 단계다.

- 등록에 사용한 봉 다음의 최초 완료 봉부터만 관측한다.
- probe는 Shadow outcome만 만들며 promotion evidence의 forward portion으로 표시한다.
- historical/holdout 결과가 refuted면 probe를 닫고 이미 발생한 결과는 삭제하지 않는다.
- code/parameter 변경은 열린 probe를 수정하지 않고 새 version과 새 trial을 만든다.
- 초기 운영 budget은 시장별 active probe/shadow capsule 최대 3개다. Queue의 가설 수를 제한하는 값이 아니다.
- generated heavy historical evaluation은 전역 lease 하나만 사용하고 10 GiB RSS에서 중단한다.
- 한 generated observe 기본 한도는 wall 2초, CPU 2초, RSS 1 GiB, open files 32개와 output 1 MiB이며 변경 시
  capsule version을 바꾼다. Protocol frame은 64 KiB를 넘을 수 없다.
- 동일 attempt의 자동 재시도는 transient host failure에 한해 최대 2회이고, code/protocol/integrity failure는 새
  attempt/version 없이 재시도하지 않는다.

### 7.4 Generated-code trust boundary

- Coordinator, evaluator, report와 execution process는 generated source를 import, compile, eval 또는 실행하지 않는다.
- Generated source를 import하는 유일한 곳은 deny-by-default `sandbox-exec` runner다.
- Runner는 network와 Unix socket, 사용자 홈, repository, `~/.config/trading-agent`, `~/.cache/trading-agent`, process
  spawn과 추가 executable 실행 권한을 갖지 않는다.
- `HOME`, `TMPDIR`와 working directory는 해당 trial의 mode `0700` root로 바꾸고 credential/proxy/Python startup/
  dynamic-loader 환경 변수를 상속하지 않는다.
- Host는 완료된 bar를 하나씩 보내며 응답 전에는 다음 frame을 제공하지 않는다. 전체 미래 데이터는 subprocess에
  존재하지 않는다.
- Generated output의 PnL, target, sizing, promotion과 order request는 무시한다. Host가 signal schema, targets, 비용,
  stop-first collision, outcome과 risk를 계산한다.
- Adversarial acceptance는 credential/file read, network/socket, child process, oversized frame/output, timeout/OOM,
  protocol pollution과 nondeterministic replay가 모두 fail closed함을 실제 sandboxed process로 증명한다.

## 8. 연구 진실성과 승격 심사

자유로운 생성은 시험 횟수를 숨기지 않을 때만 의미가 있다. 승격 심사는 다음 네 층을 모두 요구한다.

### 8.1 모든 시행 원장

같은 family에서 생성·수정·재실행한 성공과 실패를 모두 `multiple_testing_family`에 기록한다. 화면에 노출된 최고
성과만으로 Sharpe, 승률 또는 confidence를 계산하지 않는다.

### 8.2 Historical 검증

- train/validation 기간과 sealed holdout을 사전등록한다.
- 시계열 split은 purge/embargo를 적용하고 시장 비용을 포함한다.
- holdout은 lineage당 한 번만 공개하고 exact 값은 generator feedback에서 제거한다.
- candidate family의 선택 편향은 Deflated Sharpe Ratio와 PBO/CSCV로 진단한다.
- synthetic/replay는 wiring 증거이고 promotion evidence가 아니다.

### 8.3 Forward 검증

- outcome window와 review date를 trial 시작 전에 고정한다.
- chronological block 또는 session block으로 confidence를 계산한다.
- 미체결, unresolved와 censored를 0% 수익 거래로 바꾸지 않는다.
- online 관측 중 성과가 좋아 보인다는 이유로 review date를 앞당기지 않는다.
- online e-value/FDR은 시장 시계열에 유효한 e-value construction이 별도 검증된 evaluator version에서만 사용한다.

### 8.4 승격 의미

시장별 versioned promotion policy는 `(운영 minimum) AND (사전등록한 power/CI sufficiency) AND (integrity pass)`를
요구한다. 운영 minimum은 통계적 수익성 증명이 아니다.

- 미국 `PAPER_TRIAL_CANDIDATE`는 충분한 Shadow evidence와 owner approval 뒤 기존 one-use arm 아래 제한된 Paper
  evidence 수집만 허용한다. `us_day_paper_trial_policy_v1`은 최소 20 eligible forward sessions와 30 completed
  Shadow trades, historical `SUPPORTED`, 사전등록한 power/CI gate, cost-adjusted 결과, DSR/PBO, data-quality와
  integrity pass를 동시에 요구한다. `PAPER_CHAMPION_CANDIDATE`는 기존 promotion contract의 최소 60 forward
  sessions와 100 completed trades, broker-ledger, overfit, plateau와 SIP blocker를 그대로 요구한다.
- 한국 current reviewer의 20 completed sessions와 30 completed trades는 `COMPARISON_READY` minimum일 뿐 주문 자격이 아니다.
- integrity failure는 표본 수와 무관하게 suspension/rejection을 만들 수 있다.
- daily P&L 또는 한 개의 우수 session은 promotion event를 만들 수 없다.
- owner는 capsule 집합과 risk policy를 승인한다. 승인 뒤에는 개별 주문을 매번 승인하지 않아도 된다.

### 8.5 기존 lifecycle과의 대응

`FORWARD_PROBE`는 역사적 지지를 선언하는 lifecycle state가 아니라 preregistered research observation이다. 따라서
기존 `SUPPORTED → FORWARD_SHADOW` 규칙을 우회해 승격하지 않는다.

```text
DRAFTED → PREREGISTERED → SANDBOX_VALIDATED
                         ├─ FORWARD_PROBE (research-only observation)
                         └─ HISTORICAL / HOLDOUT evaluation

SUPPORTED + sufficient forward evidence
→ EXPERIMENTAL_SHADOW
→ owner-approved EXPERIMENTAL_PAPER (PAPER_TRIAL_APPROVED)
→ CHALLENGER
→ owner-approved PAPER_CHAMPION
```

Historical result만으로 `EXPERIMENTAL_PAPER`에 갈 수 없고, Forward Probe만으로도 갈 수 없다. 두 evidence plane과
market-local promotion policy가 모두 필요하다.

## 9. 시장별 Forward/Execution Controller

### 9.1 미국장

- XNYS regular session과 New York session date를 사용한다.
- calendar snapshot은 공식 holiday와 early close를 포함해 hash로 고정하며 policy/report의 `next_session`은 이
  snapshot으로 계산한다. weekday 또는 고정 시각 fallback을 사용하지 않는다.
- 새 recommendation은 current session의 latest completed bar만 사용한다.
- closed session, stale feed, missing spread, non-current data 또는 future timestamp는 차단한다.
- active Shadow capsule은 각 완료 봉에서 순차적으로 평가한다.
- Paper lane은 session-effective `PAPER_TRIAL_APPROVED` 또는 `PAPER_CHAMPION` `ExecutionEligibility`와 정확히
  일치하는 capsule만 받는다.
- 기존 one-use Paper arm을 유지하므로 첫 release의 신규 Paper entry는 세션당 최대 1개다.
- 주문마다 기존 portfolio/risk gate를 통과하고 정확히 `https://paper-api.alpaca.markets`만 사용한다.
- entry 뒤 OCO, cancel, safety flatten과 reconciliation을 기존 단일 writer가 소유한다.
- 실제 성과는 modeled signal이 아니라 reconciled fill/account activity에서 capsule과 trial로 귀속한다.
- signal→intent 사이에 content-addressed admission proof를 만들고, broker client를 열기 직전에 capsule, 현재 authority
  event, signal, intent, session과 risk-policy hash를 다시 검증한다.

### 9.2 한국장

- XKRX calendar와 KST session date를 사용한다.
- calendar snapshot은 공식 holiday, 임시 휴장과 session phase를 hash로 고정하며 next-session activation에 weekday
  fallback을 사용하지 않는다.
- KIS·LS·OpenDART의 allowlisted read-only market/news evidence만 사용한다.
- session, static/dynamic VI, continuous/call auction, halt, designation, upper/lower limit와 quote gate를 보존한다.
- entry와 exit는 완료된 KIS bar, market snapshot과 비용/slippage model로 Shadow에서만 계산한다.
- 같은 봉 stop/target 충돌은 stop이며 contiguous bar가 없으면 유리한 결과를 추정하지 않는다.
- KIS·LS account, balance, position, order endpoint와 account WebSocket registration은 호출하지 않는다.
- 한국 결과는 cumulative review와 next-XKRX-session policy까지만 만든다.

### 9.3 교차시장 규칙

- 같은 family라도 `market_id`, version, capsule, trial, outcome, cost와 review seal은 별개다.
- 미국 결과는 한국 generator의 bounded prior/context가 될 수 있고 반대도 가능하다.
- 교차시장 context에는 summary와 lineage ref만 포함하며 raw P&L을 합쳐 승격 evidence로 사용하지 않는다.
- 한 시장의 data/provider failure가 다른 시장 cursor, open trial 또는 report를 막지 않는다.

## 10. 장중 주문과 다음날 적용

장중에는 strategy code, parameter set, risk contract, execution eligibility와 active Paper trial/champion을 고정한다.
단, 새 entry를 막는 emergency suspension, revocation과 risk kill switch는 즉시 효력이 생기며 기존 position의 보호·청산
권한은 유지한다.

```text
new completed bar
→ active capsule evaluation
→ host-validated TradeSignalEnvelope
→ exact session eligibility lookup
→ current quote/spread/risk/arm gate
→ PaperOrderAdmissionRequest
→ Alpaca Paper submit
→ OCO / updates / reconcile / flatten
```

AI가 할 수 있는 일:

- 신규 hypothesis/version 제안
- sandboxed strategy source 제안
- bounded Shadow 슬롯과 기존 eligible capsule 중 하나 선택
- `no_trade` 또는 연구 중단 제안
- 결과 원인과 다음 실험 방향 요약

AI가 할 수 없는 일:

- 숫자 가격, 수량, leverage 또는 portfolio risk 한도를 prompt text로 주입
- generated code에서 broker/provider 호출
- 현재 세션의 strategy source, risk 또는 eligibility 변경
- promotion approval, arm minting, endpoint 선택, submit, cancel, flatten 또는 reconciliation
- Shadow 결과를 실제 fill로 표시

장 마감 결과는 다음 거래일에 두 경로로 적용한다.

1. `ExplorationPolicy`: 어떤 family/version을 더 만들고 어떤 capsule을 Shadow 슬롯에 둘지
2. `ExecutionEligibility`: 이미 승격·승인된 capsule 중 다음 세션에 어떤 capsule이 Paper 후보인지

첫 번째 경로도 AI text가 직접 활성화하지 않는다. AI 제안에서 결정론적 후보 검증과 immutable policy publication을
거쳐야 한다. 두 번째는 기존 manual promotion/authority와 session risk binding을 요구한다.

## 11. 보고 계약

권위 report identity는 KST 달력 날짜가 아니라 `(market_id, official_session_date)`다. 한국장과 미국장은 각 장 마감 뒤
독립 `MarketCloseReport`를 만들고, 하나의 Day Trading Agent 화면은 두 시장의 최신 verified report를 나란히 보여준다.

Final report는 calendar close만으로 발행하지 않는다. 시장별 finalization watermark가 due trials, open Shadow outcomes,
Paper reconciliation과 lifecycle projection의 terminal/explicitly-censored 상태를 증명해야 한다. 늦은 broker activity가
발견되면 기존 report를 수정하지 않고 `previous_report_id`를 가진 immutable revision을 발행한다. ExplorationPolicy는
최신 final revision만 입력으로 사용한다.

### 11.1 Execution section

- reconciled Paper orders/fills와 closed/open/unmatched quantity
- realized/unrealized PnL, daily return과 account cumulative return
- strategy/capsule-attributed return, fees/slippage와 safety actions
- 주문 차단, partial fill, OCO, forced flatten와 reconciliation anomalies

이 section은 미국 Alpaca Paper에서만 실제 execution 값을 가진다. 한국은 `provider_read_only`로 표시한다.

### 11.2 Research section

- 생성, critic rejection, compile/runtime failure와 active/terminal trials
- no-signal, blocked, unfilled, closed, unresolved와 censored counts
- capsule별 cost-adjusted return, win rate, mean R, PF와 MDD
- historical/forward evidence 상태와 다음 fixed review date
- 전체 시행 수와 selection-bias diagnostics

### 11.3 Cumulative lineage section

- family → market version → capsule → trial 계보
- exact version 성과와 family 연구 history를 분리
- Paper, US Shadow와 KR Shadow를 별도 slice로 표시
- strategy 결과와 account 결과를 분리

### 11.4 Next-session section

- next official session date
- active Shadow capsules, waiting queue와 suspended list
- unchanged/expired/new execution eligibility와 reason
- `keep | rotate_exploration | suspend_shadow | no_trade`
- data/market/risk prerequisites

보고 text에는 Shadow/Paper 연구 결과이며 미래 수익을 증명하지 않는다고 명시한다. Verified trace가 없으면 Dashboard는
성과 또는 다음 세션 활성 전략을 추정해 표시하지 않는다.

## 12. Feedback Firewall

마감 report 전체를 그대로 generator prompt에 넣지 않는다. 다음 cycle에는 다음만 제공한다.

- family/version identity와 안전한 outcome classification
- 사전등록된 metric의 bounded summary
- integrity/data-quality/runtime failure reason
- duplicate/novelty 정보와 남은 research budget
- 다음 fixed review date와 exploration priority

sealed holdout의 exact metric, symbol contribution, unpublished account identifier, credential, provider raw response와 broker
authentication 정보는 feedback에서 제거한다. 성과 feedback은 다음 version을 제안할 수 있지만 기존 artifact를 수정하지 않는다.

## 13. 실패·복구 의미

- **source stale/missing:** 신규 entry와 hypothesis observation을 차단한다. 열린 trade는 contiguous completed evidence가
  없으면 종료를 추정하지 않고 unresolved/censored로 남긴다.
- **generated code failure:** 해당 attempt와 capsule evaluation만 terminal failure로 기록한다. 기존 eligible champion으로
  자동 fallback 주문하지 않는다.
- **ledger/artifact publication failure:** downstream signal, trial, policy 또는 order binding을 만들지 않는다.
- **duplicate replay:** 동일 canonical input은 기존 artifact를 반환한다. 동일 identity의 다른 payload는 conflict다.
- **broker ambiguity:** 새 mutation 전에 open orders/positions/activity를 reconcile한다. 확인되지 않은 수량은 수익에 포함하지 않는다.
- **market-specific failure:** US/KR cursor와 report를 독립 유지한다.
- **process restart:** open work lease와 immutable event에서 재개하며 같은 bar, trial 또는 order intent를 중복 생성하지 않는다.
- **resource breach:** CPU/time/RSS limit 위반은 실패 attempt로 남기고 heavy lease를 회수한다.
- **report delivery failure:** report artifact는 보존하고 exactly-once delivery를 재시도한다. 거래 상태를 되돌리지 않는다.

## 14. 기존 코드와의 결합점

공통 연구는 다음을 확장한다.

- `strategy_research_models.py`: 현재 `ImmutableHypothesis`
- `strategy_research_results.py`: 현재 `ResearchAttempt`
- `experiment_ledger_models.py` / `experiment_ledger_store.py`: hypothesis, version, trial과 lifecycle append-only ledger
- `generated_strategy_artifact.py`, `generated_strategy_session.py`, `generated_strategy_protocol.py`: 생성 코드 artifact와 sandbox protocol
- `strategy_research_science_kernel.py`, `strategy_research_policy.py`: deterministic evaluation과 feedback firewall

시장 경계는 다음을 재사용한다.

- `signal_contract_models.py`: `TradeSignalEnvelope`
- `paper_execution_models.py`: 미국 `PaperOrderIntent`
- `paper_order_gate.py`, `us_day_operating_coordinator.py`, `us_day_operating_driver.py`: 미국 risk/arm/OCO/reconcile 경로
- `alpaca_paper_mutation_client.py`: exact Paper URL pre-network guard
- `kr_intraday_market_gate.py`: 한국 session/VI/auction/halt/designation/limit/quote gate
- `kr_theme_day_*`: 한국 trial, Shadow entry/exit, review, next-session lifecycle와 delivery
- `daily_research_models.py`, `strategy_research_close_report.py`: report projection 기반

새 공통 wrapper가 기존 권위 ledger를 복제해서는 안 된다. 가능한 경우 기존 identity를 참조하고, 새로운 의미가
필요한 `HypothesisFamily`, `HypothesisVersion`, `StrategyCapsule`, `PromotionDecision`, `ExecutionEligibility`,
`ExplorationPolicy`, `MarketCloseReport`와 `DailyLearningReport`만 append-only 계약으로 추가한다.

권위 writer는 다음처럼 하나만 둔다.

| 사실 | 권위 writer |
|---|---|
| family/version/capsule/trial/promotion/authority event | 기존 experiment ledger의 schema extension |
| generated source/runtime receipt | 기존 private immutable generated artifact store |
| US raw Paper orders/fills/positions/activity | 기존 execution/reconciliation ledger |
| US/KR raw Shadow entry/exit | 시장별 Shadow store |
| report, Dashboard와 Day-agent façade | 위 store를 읽는 query-only projection |

기존 US close report가 `market_id="us_equities"`로 고정된 채 KR observation을 포함할 수 있는 경로는 재사용하지 않는다.
새 report source ID, artifact root, cumulative window와 promotion input은 market/session으로 partition한다. 합쳐진 façade는
두 verified report ID를 링크하는 비권위 projection이다.

## 15. 기존 전략 이관과 rollback

- 현재 ORB 또는 기존 Paper champion이 새 capsule 계약의 exact strategy version, source/parameter hash, risk policy,
  clean commit, authority와 current-session evidence로 재봉인되면 `grandfathered_capsule`로 읽을 수 있다.
- 재봉인하지 못한 legacy champion, ORB-only loader 또는 `--day-loop-root` 누락 fallback은 capsule-era 신규 entry를
  만들 수 없다. Legacy row는 audit/read-only로 보존한다.
- `PaperOrderAdmissionRequest`의 기존 필드만으로 capsule proof를 우회할 수 없으며 capsule admission source는 optional이 아니다.
- migration은 기존 ledger row를 update하지 않고 mapping event와 새 capsule을 append한다.
- capsule admission을 운영 중 비활성화하면 신규 entry만 fail closed한다. 이미 열린 Paper position의 cancel, protective
  OCO, reconciliation과 same-day flatten은 계속 동작한다.
- rollback은 기존 schema를 삭제하지 않고 capsule admission feature state를 `BLOCKED`로 append한다.

## 16. 구현 분해와 순서

이 설계는 세 개의 독립 구현 계획으로 나눈다.

1. **Shared Day Research/Capsule Foundation**
   - family/version/capsule, Forward Probe admission, attempt/trial binding
   - generated signal → `TradeSignalEnvelope` bridge
   - multiple-testing ledger, fixed review와 공통 report/policy 계약
2. **US Day Learning + Alpaca Paper**
   - XNYS Shadow controller와 actual fill attribution
   - promotion/eligibility adapter와 기존 Paper single-writer 연결
   - 미국 close report, next-session policy와 Dashboard
3. **KR Day Learning Shadow**
   - XKRX/KST adapter와 기존 KR gate/Shadow lifecycle 일반화
   - 한국 close report, cumulative review와 next-session policy
   - provider read-only/real-order absence 증명

Foundation 완료 뒤 US와 KR 계획은 각자 테스트 가능한 vertical로 구현할 수 있다. 기존
`2026-08-19-autonomous-day-trading-daily-learning-loop.md`는 안전 test 아이디어를 참고하되 고정 전략, US-only와
generated-code 배제 범위를 그대로 실행하지 않고 위 세 계획으로 대체한다.

## 17. 단계별 활성화

1. **Contract-only:** 새로운 identity, append-only stores와 projection을 기존 실행과 분리해 검증
2. **Generated Forward Probe:** 실제 주문 없이 US/KR 다음 완료 봉 Shadow를 자연 세션에서 관찰
3. **Daily Learning:** 각 시장 close report와 next-session exploration policy를 최소 5개 자연 세션에서 검증
4. **US Paper Observer:** eligible capsule의 intent를 기존 gate 직전까지 생성하되 mutation은 arm 없이 차단
5. **US Experimental Paper:** owner-approved `PAPER_TRIAL_APPROVED` capsule을 기존 arm 아래 세션당 최대 한 entry로
   smoke하고 OCO/flatten/reconcile 확인
6. **US Paper Champion:** broker-Paper를 포함한 full promotion dossier와 별도 owner approval 뒤 champion authority 발행
7. **Steady State:** fixed review window와 promotion authority를 운영하며 active Shadow 슬롯을 bounded 유지

한국 실주문 단계는 이 목록에 포함되지 않는다.

## 18. Acceptance Criteria

### 18.1 제품 행동

- AI가 기존 고정 enum에 없는 가설과 Python 전략을 생성·artifact화할 수 있다.
- 새 version은 등록에 사용한 봉 다음의 완료 봉에서만 Forward Probe를 시작한다.
- 미국과 한국에서 시장별 active Shadow capsule과 queue를 유지한다.
- 각 시장 close 뒤 일일·누적, execution/research, next-session initial final report가 watermark당 정확히 한 번 생성된다.
- 늦은 정정은 previous-report chain의 새 revision으로만 발행되고 façade는 정확히 한 current final revision을 선택한다.
- 다음 세션 ExplorationPolicy만 다음 official session에 효력이 생긴다.
- 한 사용자 Day Agent surface에서 US/KR 상태를 보되 lane과 시장 성과를 합치지 않는다.

### 18.2 연구 진실성

- 성공·실패·중단·timeout·censored attempt가 모두 trial budget에 남는다.
- same family의 시도 수를 숨긴 raw best Sharpe가 promotion 근거가 되지 않는다.
- sealed holdout은 한 번만 읽고 exact result는 generator feedback에 나타나지 않는다.
- review window와 metric은 trial 전에 고정되고 mid-window 우수 성과로 앞당겨지지 않는다.
- 미국·한국 evidence가 각각 독립 review seal과 PromotionDecision을 갖는다.
- KR evidence를 포함한 US dossier, US evidence를 포함한 KR dossier와 mixed-market confidence/return build는 거부된다.

### 18.3 주문 안전

- generated code와 Shadow path는 provider/broker mutation client를 import하거나 호출할 수 없다.
- 미국 Paper signal은 exact capsule, session eligibility, current completed bar, fresh spread, risk와 one-use arm을 모두 통과한다.
- missing/tampered/future/stale/suspended capsule과 재봉인되지 않은 legacy ORB 입력은 broker client가 열리기 전에 거부된다.
- `https://paper-api.alpaca.markets`가 아닌 trading URL은 HTTP 전에 거부된다.
- KIS·LS order/account/balance/position mutation과 account WebSocket registration path가 존재하지 않는다.
- KR capsule에서 `PaperOrderAdmissionRequest`를 구성할 수 없다.
- 같은 봉 stop/target 충돌은 stop으로 계산한다.
- 실제 Paper 결과는 reconciled fills에서만 계산하며 Shadow와 합치지 않는다.

### 18.4 복구·감사

- 동일 입력 replay는 idempotent하고 identity conflict는 fail closed한다.
- 실패 recommendation과 모든 lifecycle/status transition이 audit store에 남는다.
- restart 뒤 open trials, policy, eligibility와 Paper reconciliation이 중복 없이 재개된다.
- 한 시장 failure가 다른 시장의 cycle과 report를 막지 않는다.
- credential, token, header, account identifier와 raw auth response가 artifact/report/log에 나타나지 않는다.

### 18.5 검증 표면

- 변경 Python 파일의 focused pytest, Ruff와 basedpyright가 통과한다.
- 관련 CLI help, malformed input과 local happy path를 직접 실행한다.
- Paper 변경은 live URL pre-network rejection과 open orders/positions reconciliation을 증명한다.
- 한국 변경은 read-only provider contract와 mutation symbol/endpoint 부재를 증명한다.
- synthetic/replay 결과를 자연 세션 성과 또는 profitability evidence로 보고하지 않는다.

## 19. 근거와 적용 범위

- [The AI Scientist](https://arxiv.org/abs/2408.06292): idea → code → experiment → review의 반복 구조를 Discovery에 적용한다. 금융 실행 안전의 근거로 사용하지 않는다.
- [R&D-Agent-Quant](https://arxiv.org/abs/2505.15155)와 [SHA-pinned RD-Agent](https://github.com/microsoft/RD-Agent/blob/6762f84f9bc0f5c6486c50a00e128a57ac6c3683/README.md): Researcher/Developer 분리와 feedback loop를 적용한다. backtest 성과를 주문 권한으로 해석하지 않는다.
- [AutoML-Zero](https://proceedings.mlr.press/v119/real20a.html): 제한된 전략 이름 대신 넓은 program search를 허용하는 근거다. sandbox와 resource budget을 제거하는 근거가 아니다.
- [Online multiple testing with e-values](https://proceedings.mlr.press/v238/xu24a.html): 계속 추가되는 가설의 false discovery accounting 방향을 제공한다. 유효한 market-specific e-value 없이 알고리즘을 바로 적용하지 않는다.
- [Backtest Overfitting in Financial Markets](https://escholarship.org/uc/item/4hn4t174), [Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551): 전체 시행 기록, PBO와 selection-adjusted 성과를 승격 심사에 반영한다.
- [SHA-pinned Qlib](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/README.md): 연구 구성요소의 loose coupling을 적용한다.
- [SHA-pinned FinRL-X](https://github.com/AI4Finance-Foundation/FinRL-Trading/blob/e65d6f0483ead7d2ef4a5fc940cdf960392a25c1/README.md): strategy output과 downstream risk/execution 사이의 고정 계약을 적용한다.
- [SHA-pinned TradingAgents](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/README.md): analyst/research/trader/risk 역할 분리를 참고한다. simulated exchange 결과를 production evidence로 사용하지 않는다.

## 20. 설계 우선순위

충돌 시 다음 순서를 적용한다.

1. `AGENTS.md`의 product, market-time, secrets, concurrency와 verification 규칙
2. 이 문서의 Day 3중 루프, 시장 분리와 다음-session 의미
3. `2026-08-19-six-independent-strategy-research-agents-design.md`의 science kernel과 all-attempt/holdout 규칙
4. `2026-08-02-autonomous-unrestricted-python-strategy-loop-design.md`의 generated-code sandbox 규칙
5. 기존 시장별 recommendation, Paper와 KR Shadow 계약

이 문서의 어떤 표현도 live trading, 한국 broker mutation 또는 수익성 보장으로 해석하지 않는다.
