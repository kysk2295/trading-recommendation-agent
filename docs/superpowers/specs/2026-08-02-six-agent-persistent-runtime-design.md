# 6개 에이전트별 Persistent Research Runtime 설계

- 상태: 사용자 설계 승인, 구현 전 명세 검토
- 승인일: 2026-08-02
- 상위 권위:
  - `2026-07-31-six-persistent-research-agents-design.md`
  - `2026-07-17-institutional-multi-market-quant-research-os-design.md`
  - `2026-08-02-autonomous-unrestricted-python-strategy-loop-design.md`
- 제품 경계: 연구, 추천, shadow와 Alpaca Paper 전진검증 전용
- 실제 자금 거래: 영구 금지

## 1. 결정 요약

여섯 연구 에이전트에 하나의 공통 실행 주기를 적용하지 않는다. 하나의 경량
`Research Agent Runtime`은 `launchd KeepAlive` 아래에서 상시 실행하되, Opportunity,
Market Context, Day, Swing, Systematic Quant와 Derivatives actor는 각각 독립된 evidence
cursor, memory, open work, budget, cooldown과 wake 정책을 가진다. Runtime은 새 evidence나
예약된 wake가 있는 actor만 한 cycle 실행하며, 관련 변화가 없으면 LLM을 호출하지 않는다.

현재 구현된 `run_autonomous_research_cycle.py`는 여섯 에이전트 전체가 아니라 Systematic
Quant Agent가 공통 Loop Engineer를 사용해 생성 Python 전략을 실험하는 bounded action이다.
이 one-shot을 Systematic actor의 도구로 연결하고, 다른 actor는 역할에 맞는 기존 결정론
도구만 사용한다. 검증되지 않은 생성 코드를 Day, Swing 또는 주문 경로에 직접 연결하지
않는다.

## 2. 검토한 운영 방식

### 2.1 하나의 전역 주기

모든 agent를 매시간 또는 이전 cycle 직후 함께 실행하는 방식이다. 구현은 단순하지만 뉴스,
시장 context, 장중 상태, 장마감 연구와 장기 실험의 서로 다른 시간축을 잃는다. 새 입력이 없는
agent까지 LLM을 호출하고, 동일 가설과 결과를 반복하기 쉬워 기각한다.

### 2.2 에이전트별 LaunchAgent 여섯 개

각 actor를 별도 OS service로 실행하면 주기는 분리되지만 SQLite writer, heavy empirical
lease, process health, 배포와 재시작을 여섯 군데에서 조정해야 한다. 현재 단일 호스트와
모듈러 모놀리스 경계에는 불필요한 운영 복잡도이므로 기각한다.

### 2.3 하나의 Runtime과 여섯 독립 actor

하나의 service가 저장소 cursor와 due wake를 확인하고 runnable actor를 순차 실행한다.
actor별 상태와 trigger는 분리하면서 전역 실험 원장과 heavy lease는 하나로 유지할 수 있어
채택한다. 이는 새 범용 scheduler framework가 아니라 기존 stores와 calendar를 읽는 작은
runtime loop다.

## 3. 프로세스와 권한 경계

Runtime 프로세스는 다음 권한만 가진다.

- 기존 market, news, filing, research와 experiment store 읽기
- agent cycle journal과 기존 권위 artifact store에 제한된 append
- Hermes CLI를 bounded subprocess로 호출
- agent가 선택한 기존 결정론 도구 또는 sandboxed research worker 호출
- 다음 wake와 terminal result 기록

Runtime 자체에는 Alpaca, KIS 또는 LS 주문 자격증명을 제공하지 않는다. 초기 Persistent
Runtime 활성화의 broker mutation은 0이다. 향후 lifecycle이 승인한 미국 Paper 추천도 기존
sole writer, arm, exact paper endpoint guard와 Risk Kernel을 통해서만 별도 실행될 수 있다.
KIS, LS와 다른 provider는 계속 read-only다.

LLM은 evidence와 memory를 바탕으로 한 개의 구조화된 primary decision만 선택한다. 가격,
백테스트 metric, Reviewer 판정, lifecycle 전이, risk와 order intent는 결정하지 않는다.
계산과 상태 전이는 기존 결정론 코드가 담당한다.

## 4. Runtime 구성 요소

### 4.1 Runtime supervisor

하나의 foreground process가 다음을 반복한다.

1. 시작 시 service lease를 획득한다.
2. 이전에 terminal이 되지 못한 cycle을 `interrupted`로 닫는다.
3. 여섯 actor의 cursor, open work, cooldown과 due wake를 읽는다.
4. 기존 store에서 cursor 이후의 bounded evidence envelope를 만든다.
5. runnable actor를 선택해 정확히 한 cycle 실행한다.
6. terminal record와 다음 wake를 같은 journal transaction에 확정한다.
7. 즉시 실행할 다른 actor가 없으면 다음 30초 tick 또는 가장 가까운 wake까지 기다린다.

30초 tick은 local store metadata와 cursor만 확인한다. 새 관련 evidence, due wake 또는 open
work가 없으면 Hermes나 다른 LLM subprocess를 시작하지 않는다.

### 4.2 Actor registry

registry는 여섯 고정 actor identity와 다음 계약을 연결한다.

- mission과 허용된 structured decision
- 읽을 수 있는 evidence kind와 store adapter
- 역할별 wake policy
- 허용된 deterministic tool
- model call과 light action budget
- 사용자-facing result renderer

동적 plugin, workflow DSL과 임의 actor 추가는 이번 범위가 아니다.

### 4.3 Agent cycle journal

공유 SQLite에 최소한 다음 append-only 사실을 기록한다.

`agent_cycles`:

- cycle ID와 actor identity
- trigger kind와 trigger identity
- inbox cursor 전후 값
- observed evidence references와 input digest
- recalled open-work references
- primary decision과 action request identity
- action result와 artifact references
- started, interrupted와 terminal status
- terminal reason, model receipt와 다음 wake

`agent_open_work`:

- actor identity
- hypothesis, trial, recommendation 또는 관찰 상태 reference
- next review condition
- 마지막 확인 cycle
- open 또는 terminal 상태

기존 experiment, market, recommendation, shadow/Paper와 Hermes store의 payload를 복제하지
않고 canonical reference만 남긴다. actor별 별도 database는 만들지 않는다.

### 4.4 Evidence inbox

각 adapter는 원본 store record를 다음 bounded envelope로 투영한다.

- evidence kind와 canonical key
- provider/source와 observed-at
- point-in-time as-of와 version/hash
- actor relevance
- freshness와 capability state

cursor는 terminal cycle이 journal에 확정된 뒤에만 전진한다. 같은 trigger identity와 actor로
재실행된 action은 동일 action request identity를 사용한다. downstream publication도 이
identity로 멱등 처리해, action 완료 직후 process가 죽어도 중복 hypothesis, trial, result 또는
Hermes delivery를 만들지 않는다.

## 5. 에이전트별 wake 정책

### 5.1 Opportunity Manager

- 새 뉴스, 공시, 시장 랭킹 또는 이상현상 record가 들어오면 runnable이 된다.
- 연속 유입은 첫 record 관측 뒤 2분 동안 묶어 한 bounded inbox로 처리한다.
- source provenance, freshness와 중복 검사를 통과한 후보만 조사한다.
- 결과는 후보 table, evidence, 중복 판단과 다음 hypothesis 또는 명시적 no-action이다.

### 5.2 Market Context Agent

- 시장별 calendar의 장전, 정규장 30분 경계와 장마감에 wake한다.
- 새 breadth, volatility 또는 liquidity evidence가 미리 정한 deterministic discontinuity를
  넘으면 다음 정기 wake를 기다리지 않고 실행한다.
- 데이터가 바뀌지 않은 정기 wake에서는 기존 context를 재발행하지 않고 no-change terminal을
  기록한다.
- 결과는 regime narrative와 근거 table/chart, 데이터 부족과 전략별 주의점이다.

### 5.3 Day Agent

- 현재 세션의 latest completed bar만 사용한다.
- 새 완료봉 자체로 LLM을 무조건 호출하지 않는다. deterministic prefilter가 유효 Opportunity,
  Context 변화, 열린 recommendation review 또는 setup 후보를 찾은 경우에만 actor가 실행된다.
- session closed, stale feed, missing spread, missing current-date data는 추천을 차단한다.
- 결과는 timestamp, entry, stop, targets, rationale와 immutable outcome history reference를 가진
  recommendation 또는 근거가 있는 no-action이다.

### 5.4 Swing Agent

- 시장 장마감, 새 catalyst와 열린 multi-session 상태의 review condition에 wake한다.
- Day 상태기계를 재사용하지 않고 thesis, catalyst timeline, invalidation과 exit 관찰을 독립
  open work로 유지한다.
- 결과는 conditional entry research, open-state review, invalidation 또는 no-action이다.

### 5.5 Systematic Quant Agent

- 새 논문/repository/source, terminal experiment, Reviewer feedback 또는 예약된 hypothesis
  review가 생기면 wake한다.
- 현재 generated Python one-shot을 Loop Engineer action으로 호출한다.
- 한 cycle의 proposal attempt는 기본 2회, 최대 3회이며 승인 artifact는 최대 하나만 heavy
  trial에 진입한다.
- Reviewer terminal evidence가 생기면 그 feedback을 다음 context에 넣어 새 cycle을 만든다.
- historical 또는 replay 결과만으로 Paper나 수익성을 주장하지 않는다.

### 5.6 Derivatives Research Agent

- entitlement가 있는 새로운 15분 IV, skew, term structure, futures basis/curve snapshot과 시장
  세션 경계에 wake한다.
- 필요한 capability가 없으면 데이터를 추정하거나 새 provider를 임의로 붙이지 않는다.
- 결과는 table/chart, entitlement, 해석, 한계와 후속 연구 또는 `blocked_by_data`다.

## 6. 공통 worker와 실행 순서

Loop Engineer와 Independent Reviewer는 trading agent가 아니다.

- Loop Engineer는 provenance-bound experiment request가 있고 global heavy lease를 획득한 경우에만
  challenger 하나를 실행한다.
- Independent Reviewer는 experiment가 terminal이 된 뒤 기존 결정론 evidence를 읽어 판정한다.
- Reviewer 결과는 해당 actor inbox에 immutable evidence로 돌아간다.
- Hermes는 새 terminal research result만 agent family별 projection으로 전달한다.

동시에 여러 actor가 runnable이면 다음 우선순위를 적용한다.

1. 현재 세션의 recommendation/open-state deadline
2. terminal trial과 Reviewer feedback
3. source 또는 market evidence event
4. 정기 context와 post-close wake
5. retry와 maintenance wake

한 actor가 primary decision 하나를 terminal로 닫으면 다시 runnable queue 끝으로 이동한다.
light action은 순차 실행하고 heavy empirical process는 전체에서 하나만 실행한다.

## 7. 실패와 재시작 의미론

- 모델, provider, parser, tool, data와 publication 실패를 서로 다른 reason code로 기록한다.
- LLM이 exit 0이어도 structured decision이 없거나 선택한 action이 실행되지 않았으면
  `completed`가 아니다.
- 데이터 부족, 시장 휴장과 entitlement 부재는 성공으로 숨기지 않고 `no_action` 또는
  `blocked_by_data` terminal result로 남긴다.
- 동일 evidence 실패의 자동 retry는 15분, 1시간, 4시간으로 늘리고 이후에는 새 evidence나
  operator intervention까지 기다린다.
- 새 evidence가 들어오면 이전 cooldown과 다른 trigger identity로 평가할 수 있다.
- 한 actor의 실패는 다른 actor cursor, open work 또는 lifecycle을 바꾸지 않는다.
- process 재시작 시 미완료 cycle은 `interrupted`로 닫고 같은 cursor에서 새 cycle을 만든다.
- 같은 action request의 이미 발행된 downstream artifact는 재사용하고 다시 만들지 않는다.

## 8. launchd 배치

하나의 LaunchAgent만 설치한다.

- label: `ai.trading-agent.research-agent-runtime`
- `KeepAlive`: true
- `RunAtLoad`: true
- `ProcessType`: Background
- `Umask`: `077`
- stdout/stderr: `/dev/null`
- program arguments: absolute `uv`, runtime CLI와 mode-600 config path만 포함

plist와 config에는 credential value, token, account identifier, model prompt 또는 source payload를
넣지 않는다. config는 repository 밖 current-user-owned regular file에 mode `600`으로 만든다.
runtime report와 journal도 private path만 사용한다.

Provision과 activation은 main checkout만 허용한다. repository root가 `.worktrees` 아래거나,
branch가 `main`이 아니거나, tracked worktree가 dirty이거나, `HEAD != origin/main`이면 launchd
bootstrap 전에 fail closed 한다. 개발 branch에서는 plist 생성·계약 검증과 임시 foreground
manual QA까지만 수행한다.

## 9. 사용자-facing 결과

각 terminal actor cycle은 health receipt가 아니라 다음 research result envelope를 남긴다.

- actor identity, cycle, market/session과 timestamp
- 질문 또는 falsifiable hypothesis
- evidence reference, observed-at, as-of/version과 input hash
- 선택한 action, tool/config/code version과 실행 상태
- narrative, machine-readable rows와 필요한 metric/chart
- feedback, limitations와 accept/reject/inconclusive
- recommendation이 있으면 entry, stop, targets, rationale와 outcome history
- 다음 질문, wake condition 또는 terminal reason

Hermes는 family별 result를 별도 카드로 전달한다. 여섯 의견을 하나의 매수·매도 verdict로
합치지 않고, 재시작이나 projection 재생성으로 같은 result를 다시 전달하지 않는다.

## 10. 검증과 완료 기준

### 10.1 자동 검증

- actor별 evidence relevance, cursor와 wake policy
- 새 evidence가 없을 때 model call 0회
- due wake와 event wake의 deduplication
- action request identity와 crash/restart 멱등성
- actor failure isolation과 15분/1시간/4시간 backoff
- global heavy lease 직렬화
- Systematic generated-strategy one-shot과 Reviewer feedback 연결
- Day market-time safety와 recommendation 필수 필드
- Derivatives entitlement fail-closed
- Hermes family isolation과 duplicate delivery 차단
- launchd plist/config의 mode, absolute path, no-secret와 main-binding 검증
- broker boundary의 non-paper URL pre-HTTP 거부와 Persistent Runtime mutation 0

변경 Python 파일에 targeted pytest, Ruff와 basedpyright를 실행하고 전체 pytest의 새 regression이
없는지 확인한다. CLI는 `--help`, 오입력과 relevant happy path를 직접 실행한다.

### 10.2 Manual QA Gate

개발 branch에서는 실제 Runtime CLI를 foreground로 실행해 다음을 관찰한다.

1. 서로 다른 두 actor가 서로 다른 evidence와 cursor로 terminal cycle을 만든다.
2. evidence가 없는 두 tick 동안 Hermes model process가 시작되지 않는다.
3. Systematic이 생성 artifact, sandbox trial, Reviewer와 next-context lineage를 한 번 닫는다.
4. Runtime을 중단·재시작해 cursor 재개와 downstream 중복 0을 확인한다.
5. agent별 Hermes projection이 섞이지 않고 broker mutation이 0임을 확인한다.

fixture나 synthetic evidence는 contract QA일 뿐 실제 상시 연구 활동으로 세지 않는다. 실제
activation 뒤에는 production store의 원출처 evidence로 두 agent cycle이 닫힌 것을 별도 운영
acceptance로 기록한다. 자연시장 또는 entitlement가 아직 없으면 그 항목은 완료로 꾸미지 않고
waiting 상태와 정확한 blocker를 보고한다.

### 10.3 구현 완료와 운영 활성화의 분리

구현 완료는 자동 검증과 개발 branch Manual QA Gate가 통과한 상태다. 운영 활성화는 해당
commit이 `origin/main`에 반영되고 clean main preflight를 통과한 뒤 LaunchAgent를 bootstrap하여
다음이 관찰된 상태다.

- launchd service running과 재시작 복구
- real evidence 기반 두 actor terminal result
- idle tick의 model call 0
- Systematic feedback continuation 또는 정확한 waiting reason
- duplicate result/delivery 0과 broker mutation 0

main 반영 전에는 상시 운영이 활성화됐다고 주장하지 않는다.

## 11. 구현 순서

1. 공유 journal, cursor, wake와 action identity 계약을 추가한다.
2. 고정 actor registry와 Runtime foreground loop를 구현한다.
3. Systematic actor에 기존 autonomous generated-strategy cycle을 연결한다.
4. Opportunity와 Market Context의 실제 store adapter와 result를 연결한다.
5. Day, Swing과 Derivatives adapter, prefilter와 open-work continuation을 연결한다.
6. Hermes family projection과 중복 차단을 연결한다.
7. private config, launchd provision/verify와 main activation preflight를 추가한다.
8. 자동 검증, foreground Manual QA와 clean-main 운영 activation을 순서대로 수행한다.

## 12. 비목표

- 에이전트별 OS process, database 또는 LaunchAgent 여섯 개
- Kafka, Redis, 외부 queue, workflow DSL 또는 범용 scheduler framework
- vector database와 embedding memory
- 새 provider나 자격증명 자동 설정
- 생성 전략의 자동 package 설치
- LLM Reviewer, LLM lifecycle controller 또는 LLM order decision
- historical/backtest 결과의 수익성 주장
- Alpaca live endpoint, KIS/LS mutation 또는 실자금 경로
- 현재 lifecycle 증거를 우회한 generated strategy의 Paper 자동 활성화
