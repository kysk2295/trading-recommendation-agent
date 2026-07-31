# 6개 Persistent Research Agent 설계

- 상태: 사용자 구조 승인
- 작성일: 2026-07-31
- 상위 권위:
  - `2026-07-17-institutional-multi-market-quant-research-os-design.md`
  - `2026-07-22-professional-multi-market-agent-os-seed.yaml`
- 목표: 이름만 agent인 launcher/job 구성을 실제로 관찰·판단·연구·기억하는 6개
  전문 연구 에이전트로 전환한다.

## 1. 제품 판단 기준

이 작업의 성공은 scheduler, schema, receipt 또는 안전성 검사의 수로 판단하지 않는다.
다음 동작이 실제로 반복되는지로 판단한다.

1. 각 에이전트가 자기 역할에 맞는 새로운 시장·연구 evidence를 읽는다.
2. 이전 판단과 열린 연구 과제를 기억한다.
3. 지금 할 연구 행동을 스스로 선택한다.
4. 허용된 기존 도구를 실행해 후보, 가설, 실험, 추천 또는 명시적 무행동 결과를 만든다.
5. 결과와 실패를 저장하고 다음 행동을 예약한다.
6. Hermes가 여섯 에이전트의 결과를 서로 섞지 않고 전달한다.

프로세스가 살아 있거나 launchd label이 존재하는 것만으로는 agent가 동작했다고 보지
않는다. 실제 evidence 소비, 판단, 행동, 결과와 다음 상태가 있어야 한 cycle로 센다.
여기서 실제 evidence는 production store에 원출처, 관측시각과 content identity가 남은
시장·뉴스·공시·연구 record를 뜻한다. fixture, synthetic payload와 수동으로 만든 trigger는
실제 연구 활동으로 세지 않는다.

## 2. 채택 구조

한 개의 경량 `Research Agent Runtime` 안에서 6개의 독립 persistent actor를 실행한다.
actor들은 실행 코드는 공유하지만 다음 상태는 공유하지 않는다.

- identity와 mission
- inbox cursor
- 열린 연구 과제
- 최근 결정과 결과
- 장기 연구 기억
- 다음 wake 조건과 시각
- agent별 사용 가능한 도구

launcher는 runtime 프로세스를 시작하고 죽으면 다시 시작하는 역할만 한다. 어떤 source를
읽을지, 무엇을 연구할지, 어떤 결과를 만들지는 결정하지 않는다.

```text
existing market/research stores
        ↓
bounded evidence inbox
        ↓
six independent agent actors
        ↓
structured decision
        ↓
existing deterministic tools / sandboxed research worker
        ↓
experiment ledger / recommendation outbox / agent cycle journal
        ↓
Hermes + next wake
```

각 actor를 별도 OS 프로세스로 만들지 않는다. 실제 격리 필요성이 관측되기 전까지는 하나의
runtime에서 순차적으로 실행한다. 이 프로젝트의 heavy-work lease는 기존대로 한 번에 하나만
허용한다.

## 3. 6개 에이전트

### 3.1 Opportunity Manager

- 실제 시장·뉴스·공시·랭킹·이상현상에서 조사할 후보를 찾는다.
- 후보의 근거, 관측시각, 중복 여부와 조사할 이유를 남긴다.
- provenance가 충분하면 hypothesis proposal을 만든다.
- 주문 권한은 없다.

### 3.2 Market Context Agent

- breadth, volatility, liquidity, macro와 risk regime evidence를 읽는다.
- 현재 상태, 변화, 데이터 부족과 전략별 주의점을 context로 발행한다.
- 다른 agent의 verdict를 대신 만들거나 trade 의견을 혼합하지 않는다.

### 3.3 Day Agent

- 현재 세션 Opportunity와 Context, 최신 quote와 기존 day lane 결과를 읽는다.
- 기존 deterministic day strategy를 도구로 사용해 연구·추천·무추천 행동을 선택한다.
- 미국 Paper mutation은 기존 owner arm과 sole writer를 통해서만 가능하다.
- 한국 시장은 shadow-only다.

### 3.4 Swing Agent

- post-close Opportunity, Context, 뉴스·공시와 열린 multi-session 상태를 읽는다.
- 신규 조건부 진입 연구, 기존 shadow position 관찰, invalidation 또는 exit 연구를 선택한다.
- Day 상태기계를 재사용하지 않는다.

### 3.5 Systematic Quant Agent

- 논문, source lineage, 실패 trial, regime별 성과와 데이터 capability를 읽는다.
- falsifiable hypothesis, baseline과 bounded experiment를 제안한다.
- historical 또는 walk-forward 도구를 실행할 수 있지만 heavy lease는 하나만 사용한다.
- 결과가 나쁘거나 무신호여도 terminal research result로 보존한다.

### 3.6 Derivatives Research Agent

- 옵션 IV·skew·term structure와 선물 basis·curve·roll evidence를 읽는다.
- entitlement가 있는 데이터만 사용해 read-only 또는 shadow 연구를 수행한다.
- capability가 없으면 새 인프라를 만드는 대신 어떤 연구가 막혔는지 명시한다.

## 4. Agent Cycle

모든 agent는 동일한 여섯 단계 contract를 사용한다.

### 4.1 Observe

기존 store에서 agent 역할과 관련된 새 evidence만 bounded하게 읽는다. cursor 이후 새 evidence가
없더라도 예약된 review가 있으면 wake할 수 있다.

### 4.2 Recall

최근 cycle, 열린 hypothesis/trial, 이전 실패, 다음 확인 예정 항목을 읽는다. 별도 vector DB나
embedding memory는 만들지 않는다. 기존 experiment ledger와 최소 agent cycle journal을
memory source로 사용한다.

### 4.3 Decide

Hermes model이 agent mission, 현재 evidence와 memory를 받아 하나의 구조화된 결정을 반환한다.

허용되는 결정은 다음뿐이다.

- `investigate_candidate`
- `propose_hypothesis`
- `run_light_experiment`
- `request_heavy_experiment`
- `publish_context`
- `publish_recommendation`
- `review_open_state`
- `no_action`

한 cycle은 한 개의 primary decision만 가진다. 여러 행동이 필요하면 후속 cycle을 예약한다.

### 4.4 Act

결정에 해당하는 기존 deterministic function 또는 sandboxed research worker를 호출한다.
LLM이 가격 계산, backtest 결과 또는 broker mutation을 직접 만들지 않는다. LLM은 연구 방향과
도구 선택을 담당하고 계산과 상태 전이는 기존 검증 가능한 코드가 담당한다.

### 4.5 Record

결정, 사용한 evidence, 실행 결과, 실패 이유와 생성된 artifact reference를 저장한다.
experiment와 recommendation은 기존 권위 store에 기록하고, agent cycle journal에는 연결 정보만
남긴다.

모델 프로세스가 exit 0이어도 구조화된 결정이 없거나 선언한 행동이 실행되지 않았으면
`completed`가 아니다. `no_action`만 reason과 next wake가 있으면 정상 terminal로 인정한다.

### 4.6 Continue

각 cycle은 다음 중 하나를 남긴다.

- 새 evidence가 도착할 때 wake
- 특정 시장시각에 wake
- trial 또는 Reviewer 결과가 생길 때 wake
- 명시적 terminal idle

runtime이 재시작되면 journal의 마지막 terminal cycle과 inbox cursor부터 이어간다.

## 5. 최소 Persistence

새 persistence는 agent cycle을 진짜로 이어가기 위해 필요한 최소 정보만 저장한다.

`agent_cycles`:

- cycle ID
- agent family
- trigger와 inbox cursor
- 관측시각
- primary decision
- evidence references
- action result references
- terminal status와 reason
- next wake condition

`agent_open_work`:

- agent family
- hypothesis, trial, recommendation 또는 open-state reference
- 다음 확인 조건
- 마지막 확인 cycle
- open 또는 terminal

기존 experiment ledger, market stores, shadow/Paper stores와 Hermes delivery store를 복제하지
않는다. agent별 별도 데이터베이스 여섯 개도 만들지 않는다.

## 6. 실제 Data Flow

1. runtime이 시작되면 6개 actor의 마지막 cursor와 open work를 읽는다.
2. 실제 store의 새 record 또는 예약시각으로 runnable actor를 고른다.
3. runnable actor 하나가 Observe→Recall→Decide를 실행한다.
4. light action은 즉시 실행한다.
5. heavy action은 기존 single heavy lease가 비어 있을 때만 실행하고, 아니면 대기 상태와 다음
   wake를 남긴다.
6. 생성된 hypothesis, trial, context, recommendation 또는 no-action 결과를 기존 store와
   Hermes outbox에 기록한다.
7. journal을 terminal로 닫고 다음 runnable actor로 이동한다.

agent 간 직접 호출은 하지 않는다. Context와 Opportunity 결과도 immutable evidence로 발행되고,
Day·Swing 등은 다음 cycle에서 이를 읽는다. 이 경계가 의견 혼합과 숨은 결합을 막는다.

## 7. Loop Engineer와의 관계

Loop Engineer는 일곱 번째 trading agent가 아니다. agent가 제안한 provenance-bound challenger를
실행하는 공통 연구 worker다.

- agent가 hypothesis와 evaluation request를 만든다.
- Loop Engineer가 한 번에 challenger 하나를 preregister하고 sandbox에서 구현·테스트한다.
- historical, walk-forward와 shadow 단계가 같은 strategy version을 사용한다.
- Independent Reviewer 결과가 agent inbox로 돌아간다.
- agent는 Reviewer 결과를 기억하고 다음 연구를 선택한다.

첫 제품 증거는 promotion이 아니라 source에서 Reviewer decision까지 자동으로 닫힌 한 개의
실제 challenger다.

## 8. Hermes 사용자 표면

Hermes의 여섯 family query는 저장된 delivery projection만 읽는 현재 구조에서 실제 agent state를
읽는 구조로 바꾼다.

각 agent 응답은 다음을 보여준다.

- 마지막으로 읽은 실제 evidence와 시각
- 현재 판단
- 진행 중인 연구
- 최근 실행한 도구와 결과
- 다음 행동 또는 wake 조건
- recommendation, context, research result 또는 no-action reason

여섯 의견을 하나의 매수·매도 verdict로 합치지 않는다. 자동 메시지는 새로운 결과가 생겼을 때만
발행하며 재시작으로 같은 결과를 다시 보내지 않는다.

## 9. 운영과 실패 처리

운영 코드는 단순하게 유지한다.

- runtime 프로세스 한 개
- actor cycle은 기본적으로 순차 실행
- light action과 model call은 agent별 budget 안에서 실행
- heavy action은 전체 한 개
- 실패 cycle은 기록하고 같은 evidence를 무한 재시도하지 않는다.
- runtime 재시작은 미완료 cycle을 `interrupted`로 닫고 같은 inbox cursor에서 새 cycle을 만든다.

provider 장애, 데이터 부족, 시장 휴장은 실패를 숨기지 않고 해당 agent의 `no_action` 또는
`blocked_by_data` 연구 결과가 된다. 단순 model text에 “blocked”가 있는데 runtime status가
completed인 현재 오류는 허용하지 않는다.

## 10. 구현하지 않는 것

다음은 이 전환의 범위가 아니며 실제 병목 증거 없이 추가하지 않는다.

- 새 scheduler framework
- workflow DSL 또는 범용 graph engine
- Kafka, Redis, 외부 queue 또는 microservice 분리
- vector database와 embedding memory
- agent별 launchd job 여섯 개
- 별도 agent database 여섯 개
- 새 dashboard 또는 dashboard 재작성
- 추가 provider
- 새로운 safety framework
- 기존 안전 계약의 일반화·재작성
- fixture와 schema만을 위한 새 abstraction

기존 Paper endpoint guard, fixed risk, KIS·LS read-only와 secret 규칙은 그대로 재사용한다.
이를 확장하는 작업은 실제 agent 행동이 기존 계약 때문에 잘못 막힌 증거가 있을 때만 한다.

## 11. 구현 순서

### Slice 1: Opportunity 실제 폐루프

공통 runtime과 최소 journal을 만들고 Opportunity Manager가 실제 저장 evidence에서 한 cycle을
완주하게 한다. 성공 증거는 실제 evidence→판단→후보 또는 hypothesis/no-action→Hermes
result→next wake다.

### Slice 2: 나머지 5개 actor 연결

같은 runtime에 Context, Day, Swing, Systematic, Derivatives의 mission, inbox와 tool set을 연결한다.
각 agent가 최소 한 번 실제 evidence로 terminal cycle을 만들기 전에는 “6-agent runtime
완료”라고 부르지 않는다.

### Slice 3: Challenger 폐루프

Opportunity 또는 Systematic agent가 만든 한 hypothesis를 기존 Loop Engineer 경로에 연결해
source→preregistration→sandbox implementation/test→historical/walk-forward→shadow
registration→Reviewer decision→agent memory→Hermes summary를 닫는다.

### Slice 4: 시장 운영 vertical

Day와 Swing actor의 결정이 기존 US/KR deterministic vertical을 호출하게 한다. 기존 목표의 실제
세션, Paper/shadow lifecycle과 연속 5거래일 acceptance는 이 단계에서 자연시장 증거로 닫는다.

## 12. 검증

검증 우선순위는 실제 행동이다.

### 12.1 필수 실행 증거

- 6개 agent 각각 실제 evidence 기반 terminal cycle 1개 이상
- agent별 서로 다른 mission, inbox, tool decision과 memory cursor
- 실제 source-bound hypothesis 1개
- 실제 bounded experiment terminal 1개
- 실패 또는 no-action cycle 1개가 성공으로 오인되지 않고 보존
- runtime 재시작 뒤 마지막 cursor와 open work를 이어서 처리
- Hermes에서 6개 agent의 최근 판단과 다음 행동을 개별 조회

### 12.2 연구 활동 지표

매일 다음 값을 agent별로 계산한다.

- terminal cycles
- research-active cycles
- model decisions와 실제 tool actions completed
- evidence items consumed
- candidates investigated
- unique hypotheses proposed
- duplicate hypotheses rejected
- experiments started와 terminal
- recommendations, contexts와 no-action results
- open work와 overdue follow-up

이 지표는 목표치 조작용 점수가 아니라 “실제로 연구했는가”를 보여주는 운영 관측값이다.
`research-active cycle`은 `no_action`이 아닌 decision이 실제 도구 결과까지 만든 경우만 센다.
`no_action`, `blocked`, fixture와 QA cycle은 별도 집계하며 연구량에 합산하지 않는다.

### 12.3 테스트 범위

- 구조화된 decision과 action-result contract
- cursor/restart idempotency
- family별 tool boundary
- model text blocker를 completed로 세지 않는 회귀
- Opportunity actual-evidence vertical
- 6-agent manual CLI/Hermes surface

기존 전체 안전성 테스트를 반복 확장하지 않는다. 변경한 agent 행동과 기존 broker/provider 경계의
직접 회귀만 실행한다.

## 13. 완료 정의

이 설계의 구현은 다음 모두를 관측해야 완료다.

1. runtime 한 개가 6개의 독립 agent identity와 state를 복구한다.
2. 각 agent가 실제 evidence에서 최소 한 개 terminal cycle을 만든다.
3. launcher/job 이름이 아니라 agent journal과 결과가 상태의 권위다.
4. 한 개의 provenance-bound hypothesis가 실제 research action으로 이어진다.
5. Hermes가 여섯 agent의 판단, 진행 연구와 다음 행동을 개별 표시한다.
6. 실패·blocked·no-action이 completed research로 잘못 집계되지 않는다.

이 완료는 상위 Professional Multi-Market Research OS 전체 완료가 아니다. 전체 완료는 이후 실제
US·KR 세션, Day Paper/shadow, Swing multi-session, challenger Reviewer와 5거래일 acceptance가
원래 기획대로 모두 닫혀야 한다.
