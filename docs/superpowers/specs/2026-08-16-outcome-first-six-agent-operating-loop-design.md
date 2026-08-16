# Outcome-First 6-Agent Research Loop 설계

- 상태: 사용자 방향 승인, Grok 읽기 전용 설계 검토 반영, written-spec 사용자 검토 대기
- 작성일: 2026-08-16
- 상위 제품 권위: `README.md`
- 기반 설계:
  - `2026-07-31-six-persistent-research-agents-design.md`
  - `2026-08-02-autonomous-unrestricted-python-strategy-loop-design.md`
- 목표: 기존 단일 6-family runtime을 실제 evidence, 실제 도구 결과, 사용자 표면, outcome feedback으로
  연결한다.

이 문서는 기존 제품 경계와 두 기반 설계를 대체하지 않는다. 대신 2026-07-31 설계의 구현 순서를
사용자 결과 우선으로 구체화하고, 최근 scheduler/coordinator 중심 작업을 제품 경로 밖으로 내린다.

## 1. 제품 결과와 판단 기준

이 작업의 결과는 프로세스 생존, scheduler 상태, schema 수 또는 테스트 수가 아니다. 다음 흐름이
실제 데이터에서 반복되어야 한다.

```text
실제 evidence
→ 역할별 bounded evidence view
→ agent의 한 개 결정
→ 기존 deterministic tool의 실제 실행
→ family별 권위 artifact 또는 적법한 no-action
→ Hermes / Dashboard 표시
→ Day·Swing outcome 또는 Systematic experiment result
→ 다음 agent cycle의 feedback evidence
```

첫 번째 성공은 여섯 family의 현재 상태와 실제 결과를 사용자가 한 화면에서 보는 것이다. 두 번째
성공은 이전 추천·실험 결과가 다음 판단을 바꾸는 것이다. Paper 주문과 통계적 우위는 그 이후의
검증 대상이며 초기 제품 결과를 막지 않는다.

## 2. 현재 코드에서 확인한 단절

Grok의 읽기 전용 검토 결과를 독립적으로 코드와 대조했고 다음을 확인했다.

### 2.1 실제 evidence 본문 유실

`research_agent_source_common.py`의 `ResearchAgentEvidenceMaterial`은 `canonical_payload`를
받지만 `ResearchAgentEvidenceV1`을 만들 때 SHA-256과 reference만 남긴다.
`research_agent_decision.py`의 prompt도 source key, timestamp, market과 hash만 전달한다. 후보,
가격, 완료 봉, 추천 상태, experiment metric과 Reviewer 이유는 Decide에 도달하지 않는다.

### 2.2 모델 문장만으로 completed 처리

`research_agent_actions.py`의 `result_from_decision()`은 Systematic heavy experiment를 제외한
대부분의 action을 실제 도구 호출 없이 `COMPLETED`로 만든다. 이때 `artifact_refs`는 prompt와
response hash뿐이다. 따라서 현재 구조에서는 LLM JSON 성공이 연구 행동 성공으로 오인될 수 있다.

### 2.3 Day 추천 경로 단절

`research_agent_service_runtime.py`는 `verified_trade_signal_refs=frozenset()`으로 action
executor를 만든다. 운영 runtime의 `publish_recommendation`은 실제 Day engine이 만든 signal과
연결되지 않아 항상 검증 reference를 얻지 못한다.

### 2.4 Systematic이 빠른 runtime을 점유

`research_agent_systematic.py`는 generated strategy cycle을 `subprocess.run()`으로 실행하고 최대
3,600초 기다린다. 이 동안 단일 runtime의 다른 family tick이 진행되지 않는다.

### 2.5 Critic과 사용자 표면의 부족

`critic_agent.py`는 rejected hypothesis 중복과 free parameter 수만 검사한다. 정의된
source-fidelity와 mechanism objection은 실행되지 않는다. `research_agent_hermes.py`는 family,
summary와 status 한 줄만 렌더링하고 dashboard의 agent runtime 표면은 process state 위주다.

이 다섯 단절이 이 설계의 변경 범위다. 새 인프라는 해결책이 아니다.

## 3. 채택 아키텍처

```text
기존 권위 store
  ↓
6개 family source adapter
  ↓
bounded evidence payload + provenance
  ↓
기존 ResearchAgentRuntime / cycle journal / wake policy
  ↓
Hermes structured decision: 한 cycle에 한 primary action
  ↓
family action adapter가 기존 deterministic tool 호출
  ↓
기존 권위 store에 artifact append
  ↓
ResearchAgentResult는 artifact identity만 참조
  ↓
Research Board read model
  ├─ Hermes family card
  └─ 기존 Dashboard 6-family workspace
  ↓
terminal outcome / experiment / Reviewer feedback
  ↓
다음 family evidence
```

### 3.1 유지하는 구성

- `ResearchAgentRuntime`: runnable actor 선택, 순차 tick, restart recovery
- `ResearchAgentCycleStore`: evidence, cursor, cycle, result, open work
- `research_agent_wake_policy.py`: event-driven wake, debounce와 failure backoff
- 기존 Opportunity, Context, Day, Swing, Systematic, Derivatives source adapter
- 기존 recommendation, shadow, experiment, generated strategy, review store
- 기존 Hermes delivery store와 Dashboard workspace
- 기존 single-heavy-work lease

### 3.2 추가하는 최소 구성

1. 기존 evidence envelope 안의 bounded canonical payload
2. action 실행에 필요한 `ResearchAgentActionContext`
3. 두 개의 family action adapter 모듈
   - primary: Opportunity, Context, Day
   - research: Swing, Systematic, Derivatives
4. 권위 artifact를 읽어 만드는 `ResearchBoardItemV1` read model과 projection
5. Day·Swing terminal outcome과 experiment/review를 family evidence로 되돌리는 adapter 연결

새 scheduler, coordinator, framework, database, queue, provider 또는 agent process는 추가하지 않는다.

## 4. 권위 경계

| 사실 | 권위 writer | agent result의 역할 |
|---|---|---|
| cycle, cursor, open work, next wake | `ResearchAgentCycleStore` | cycle/result 연결 |
| Opportunity 후보 | 기존 Opportunity snapshot/outbox | snapshot identity 참조 |
| Market Context | 기존 context artifact | context identity 참조 |
| Day recommendation·outcome | 기존 recommendation engine/store | recommendation/event identity 참조 |
| Swing thesis·shadow outcome | 기존 Swing shadow/review store | signal/event/review identity 참조 |
| hypothesis·version·trial·lifecycle | experiment ledger | hypothesis/trial identity 참조 |
| generated source | generated strategy artifact store | artifact identity 참조 |
| Reviewer decision | 기존 review stores | review identity 참조 |
| Hermes delivery | Hermes delivery store | 사용자 projection |
| Alpaca Paper mutation | 기존 sole writer + Risk Kernel | 직접 권한 없음 |

LLM의 question, summary와 reason은 설명이며 가격, 성과, lifecycle 또는 주문 권위가 아니다.
Research Board도 권위 store가 아니며 기존 artifact를 읽어 만든 read-only projection이다.

## 5. 공통 계약

### 5.1 Bounded evidence payload

`ResearchAgentEvidenceV1`은 현재 hash-only envelope에서 다음 계약을 가진 envelope로 확장한다.

- 기존 provenance, timestamp, source key와 `payload_sha256`
- family adapter가 만든 canonical `bounded_payload_json`
- payload가 잘렸는지 나타내는 `payload_truncated`

규칙은 다음과 같다.

- payload는 family가 판단에 필요한 allowlisted market/research 값만 포함한다.
- raw provider response, credential, account identifier와 authentication 값은 포함하지 않는다.
- payload SHA는 canonical payload bytes와 일치해야 한다.
- 한 decision prompt의 전체 bounded payload는 48 KiB, table row는 family별 32개로 제한한다.
- 제한을 넘으면 오래된 행부터 제외하고 `payload_truncated=true`를 남긴다.
- 원본 권위 store는 자르거나 다시 쓰지 않는다.
- payload는 기존 private cycle database의 evidence JSON에 저장한다. 새 table이나 DB는 만들지 않는다.

scheduled, retry, open-work와 source-failure evidence도 작은 typed payload를 가져야 한다. 따라서 모델은
항상 무엇이 예약되었고 무엇이 실패했는지 실제 값으로 읽는다.

### 5.2 Structured decision

기존 여덟 `ResearchAgentDecisionKind`를 유지한다. decision에는 다음 machine-readable 선택을
추가한다.

- `subject_refs`: 이번 action이 사용할 bounded evidence 또는 open-work의 canonical identity
- `primary_decision`: 한 개의 action
- question, summary와 continuation
- next wake

`subject_refs`는 현재 decision request 안에서 resolve되어야 한다. 모델이 존재하지 않는 symbol,
hypothesis, recommendation 또는 trial identity를 만들면 decision validation에서 실패한다.

### 5.3 Action context

runtime은 action executor에 cycle과 decision만 넘기지 않고 다음 `ResearchAgentActionContext`를
넘긴다.

- cycle
- 선택된 evidence와 bounded payload
- 같은 family의 open work
- decision
- observed time

family adapter는 이 context에서 `subject_refs`를 resolve한 뒤 기존 tool을 호출한다. LLM text를
가격, metric 또는 artifact로 변환하지 않는다.

### 5.4 Terminal result

`ResearchAgentResultV1`은 다음 invariant를 가진다.

- `COMPLETED`: prompt/response hash가 아닌 family 권위 artifact reference가 최소 1개 필요하다.
- `NO_ACTION`: reason, continuation과 유효한 next wake가 필요하다. artifact 없는 유일한 정상 terminal이다.
- `FAILED` 또는 `BLOCKED`: machine-readable reason이 필요하다.
- prompt/response hash는 decision provenance에 남기고 result artifact로 세지 않는다.
- artifact reference의 실제 resolve는 Pydantic validator가 아니라 family action boundary에서 수행한다.
- action이 선언한 artifact를 남기지 못하면 `action_not_executed`다.
- terminal result commit 뒤에만 cursor를 전진한다.
- order, lifecycle과 allocation authority는 계속 `False`다.

`result_from_decision()`으로 non-no-action을 completed 처리하는 경로는 제거한다.

## 6. Family별 action과 artifact

### 6.1 Opportunity Manager

- 입력: 실제 Opportunity snapshot, 뉴스·공시·랭킹 evidence, 최근 중복 history
- action: candidate 조사 또는 provenance-bound hypothesis 등록
- completed artifact: 기존 `OpportunitySnapshot` identity와 필요할 때 hypothesis card key
- 사용자 결과: 후보 table, source, 조사 이유, 중복 여부, 다음 가설
- 주문 권한: 없음

### 6.2 Market Context

- 입력: 실제 context snapshot과 직전 context
- action: 기존 breadth, volatility, liquidity와 macro context 발행
- completed artifact: 기존 `MarketContextSnapshot` identity
- 동일 canonical snapshot: `NO_ACTION(context_unchanged)`
- 다른 agent verdict를 대신 만들지 않음

### 6.3 Day Trading

- 입력: Opportunity, Context, 최신 완료 봉, fresh quote/spread, 열린 recommendation과 최근 outcome
- action: 기존 deterministic Day engine 실행 또는 open-state review
- completed artifact: recommendation, `TradeSignalEnvelope` 또는 terminal recommendation event
- no-action: `session_closed`, `stale_feed`, `missing_spread`, `completed_bar_unavailable`, `no_setup`
- LLM은 entry, stop, targets를 계산하지 않음
- 기존 engine이 signal을 쓴 뒤 같은 identity를 action result로 검증함

### 6.4 Swing Trading

- 입력: post-close Opportunity/Context, catalyst, 열린 Swing shadow와 review event
- action: 기존 Swing engine의 신규 conditional thesis 또는 open-state review
- completed artifact: Swing signal, terminal event 또는 Swing review
- Day state machine을 재사용하지 않음

### 6.5 Systematic Quant

- 입력: 실제 research source, 실패 trial, Reviewer feedback, strategy version과 regime별 결과
- light action: falsifiable hypothesis와 generated artifact 등록
- heavy request action: trial STARTED와 open work를 먼저 기록한 뒤 기존 one-shot CLI 기동
- completed artifact: hypothesis card, generated artifact, trial registration 또는 terminal experiment/review
- historical 결과만으로 promotion이나 Paper 권한을 만들지 않음

### 6.6 Derivatives Research

- 입력: entitlement가 확인된 IV, skew, term structure, futures basis/curve projection
- action: 기존 derivatives projection을 research context로 발행
- completed artifact: IV/skew/term/basis table 또는 chart identity
- capability 없음: `NO_ACTION(derivatives.blocked.<reason>)`
- 추정 데이터나 새 provider를 만들지 않음

## 7. Fast operating loop

fast loop는 기존 runtime tick과 actor wake policy를 유지한다. 모든 actor를 매 tick 호출하지 않는다.

```text
idle
→ new bounded evidence | due open work | scheduled wake
→ STARTED
→ deterministic admission
   ├─ invalid current-session input → NO_ACTION + next wake
   └─ admitted
       → Hermes Decide 1회
       → family light action
           ├─ authority artifact → COMPLETED
           ├─ valid no-action → NO_ACTION
           └─ contract/tool failure → FAILED 또는 BLOCKED
→ terminal commit
→ cursor advance
→ Research Board / Hermes projection
```

fast loop 규칙:

- 한 tick은 한 actor와 한 primary decision만 처리한다.
- 새 evidence나 due wake가 없으면 model call은 0이다.
- 한 family 실패는 다른 family cursor와 wake를 막지 않는다.
- Context와 Opportunity 결과는 다른 actor가 다음 cycle evidence로 읽는다. 직접 호출하지 않는다.
- 결과는 Reviewer를 기다리지 않고 `review_pending` 상태로 사용자에게 보인다.
- 기존 market-time, completed-bar, freshness와 spread gate는 Day recommendation에만 적용한다.
  시장이 닫혀도 Context, Systematic과 Derivatives 연구까지 멈추지 않는다.

### 7.1 Heavy experiment 비차단

`REQUEST_HEAVY_EXPERIMENT`는 fast cycle 안에서 장시간 완료를 기다리지 않는다.

1. hypothesis/generated artifact가 이미 immutable해야 한다.
2. experiment ledger에 trial STARTED와 open-work identity를 먼저 append한다.
3. 기존 `systematic_cycle_command()`를 별도 process group의 one-shot process로 기동한다.
4. stdout/stderr와 output root는 기존 cycle run directory를 사용한다.
5. fast cycle은 experiment-request artifact를 남기고 즉시 끝난다.
6. child의 기존 heavy lease가 동시 heavy work를 막는다.
7. collector가 terminal trial 또는 Reviewer event를 발견하면 Systematic을 다시 깨운다.

기동 실패는 trial FAILED와 action failure를 남긴다. runtime restart가 STARTED trial을 다시 발견해도
같은 trial identity를 재등록하거나 무조건 재기동하지 않는다. 기존 ledger와 heavy lease로 먼저
terminal/running 상태를 확인한다.

## 8. Slow validation loop

slow loop는 fast publication을 검열하지 않는다. immutable research/shadow 결과가 쌓인 뒤 기존
experiment와 review store를 사용한다.

```text
preregistered hypothesis + exact strategy version
→ bounded historical / walk-forward
→ 동일 version의 forward shadow
→ host evaluator: realistic cost, baseline, same-bar stop rule
→ Reviewer: accept | reject | inconclusive
→ immutable review/lifecycle event
→ 다음 Systematic 또는 관련 family feedback evidence
```

규칙:

- Critic은 실행 전에 provenance, rejected duplicate, hypothesis/strategy contract와
  executability/sandbox preflight를 검사한다.
- 동일 source와 strategy version을 historical, shadow와 review가 공유한다.
- 전략 변경은 기존 결과 수정이 아니라 새 version이다.
- 표본이 적으면 `inconclusive`이며 fast research/shadow를 막지 않는다.
- 비용·baseline·forward outcome은 모든 promotion review에 필요하다.
- DSR/PBO 같은 다중검정 통제는 충분한 trial 표본이 생긴 뒤 적용한다.
- Reviewer는 기존 결과를 삭제하지 않는다.
- Reviewer는 promotion/Paper 후보를 veto할 수 있지만 promotion이나 주문 권한을 부여하지 않는다.
- 실제 Paper는 기존 owner arm, Risk Kernel, exact Paper endpoint와 reconciliation을 별도로 통과한다.

## 9. Research Board와 사용자 표면

### 9.1 Research Board read model

`ResearchBoardItemV1`은 저장하지 않고 다음 입력에서 결정론적으로 만든다.

- latest terminal `ResearchAgentResultV1`
- result의 family authority artifact
- 최신 feedback/review
- open work와 next wake

공통 출력은 family, market/session, occurred-at, question, status, artifact kind/identity, bounded
summary rows, review status, feedback reference와 next wake다. family별 payload는 다음 union이다.

- Opportunity candidate rows
- Context regime/features
- Day recommendation/outcome
- Swing thesis/open-state/outcome
- Systematic hypothesis/trial/metrics/review
- Derivatives surface/basis rows 또는 blocker

Board는 여섯 칸을 나란히 보여 주지만 투표, 가중 평균 또는 blended buy/sell verdict를 만들지 않는다.
새 결과가 없는 family는 latest result의 freshness와 `not_due`, `carried_forward` 또는
`blocked_missing_evidence` 상태를 보여 준다. 이를 새 cycle이나 delivery로 기록하지 않는다.

### 9.2 Hermes

기존 `HermesDeliveryKind.RESEARCH`와 exactly-once identity를 유지한다. 새 terminal result가 생길
때만 Board item을 다음 형식으로 렌더링한다.

- 실제 question과 evidence time
- family별 핵심 table/card
- action tool과 artifact identity
- recommendation/outcome 또는 no-action reason
- review status
- next action/wake

Hermes query는 enriched delivery projection을 계속 읽는다. delivery text가 권위 값을 포함하므로
query service에 여섯 권위 store reader를 새로 주입하지 않는다. `blended_verdict=None`을 유지한다.

### 9.3 Dashboard

새 dashboard를 만들지 않는다. 기존 6-family workspace에 다음을 추가한다.

- last question/status
- artifact kind와 safe identity
- family별 핵심 rows/card
- feedback/review 상태
- next wake

runtime health는 보조 정보로 남긴다. Options Workbench 5-view는 이 목표의 critical path가 아니다.

## 10. Failure semantics

| reason | 의미 | terminal status |
|---|---|---|
| `bounded_payload_missing` | 실제 판단 payload 없음 | `FAILED` |
| `decision_subject_unresolved` | 모델이 입력에 없는 subject 선택 | `FAILED` |
| `prose_only_result` | 도구 없이 completed 시도 | `FAILED` |
| `action_not_executed` | 권위 artifact가 생성되지 않음 | `FAILED` |
| `authority_artifact_unresolved` | artifact identity가 권위 store에 없음 | `FAILED` |
| `primary_admission.<reason>` | session, bar, stale, spread 조건 불충족 | `NO_ACTION` |
| `context_unchanged` | 동일 context 재발행 방지 | `NO_ACTION` |
| `no_setup` | deterministic setup 없음 | `NO_ACTION` |
| `heavy_lease_busy` | 다른 heavy work 실행 중 | `NO_ACTION` + scheduled wake |
| `capability_unavailable` | 계약상 선택적인 entitlement/capability 없음 | `NO_ACTION` |
| `required_evidence_unavailable` | 선택한 action의 필수 evidence가 외부 사유로 없음 | `BLOCKED` |
| `source_failure.<reason>` | source adapter 실패 | `FAILED` + 기존 backoff |
| `generated_*` failure | 기존 generated strategy 실패 | ledger `FAILED`/`CENSORED` |

`blocked`라는 모델 문장을 completed로 저장하지 않는다. failure/no-action/result status는 deterministic
runtime과 tool outcome만 결정한다. 실패한 artifact와 outcome도 삭제하지 않는다.

## 11. 구현 slices와 실제-data 수용 기준

각 slice는 unit test만으로 완료되지 않는다. 관련 CLI help, bad input과 실제 사용자 surface happy
path를 관찰해야 한다. fixture/replay는 회귀 증거일 뿐 제품 완료 증거가 아니다.

이 문서는 전체 goal의 parent design이다. 하나의 거대한 구현 계획으로 모든 slice를 동시에 열지
않는다. Slice 1부터 순서대로 별도 구현 계획을 작성하고, 각 slice의 수용 증거를 확인한 뒤 다음
slice를 연다. 첫 구현 계획의 범위는 Slice 1뿐이다.

### Slice 1: evidence와 terminal 계약

범위:

- bounded evidence payload 보존과 prompt 전달
- decision subject reference
- action context
- prose-only completed 제거

수용 기준:

1. production source path의 실제 Opportunity 또는 Day evidence 한 건이 후보 또는 완료 봉 값을
   bounded payload에 포함한다.
2. 존재하지 않는 subject reference가 decision 단계에서 거부된다.
3. 모델이 `publish_recommendation` 문장만 반환해도 `prose_only_result`다.
4. reason과 next wake가 있는 no-action만 artifact 없이 정상 terminal이다.
5. 다음 idle tick은 model call 0이다.

### Slice 2: Opportunity와 Context vertical

범위:

- primary family action adapter
- Opportunity snapshot/hypothesis와 Context artifact 연결
- Hermes family renderer

수용 기준:

1. 실제 Opportunity snapshot에서 candidate rows, source와 조사 이유가 Hermes에 표시된다.
2. provenance가 충분한 proposal은 기존 hypothesis card key를 남긴다.
3. 실제 Context artifact가 표시되며 동일 snapshot은 `context_unchanged`다.
4. 주문 mutation은 0이다.

### Slice 3: Day와 Swing outcome feedback

범위:

- Day evidence에 latest completed bar, open recommendation과 terminal event 포함
- Day/Swing deterministic engine action 연결
- terminal outcome을 다음 cycle evidence로 환류

수용 기준:

1. 현재 NY regular session의 실제 latest completed bar에서 recommendation 또는 typed no-action이
   나온다. backdated recommendation은 0이다.
2. recommendation이면 timestamp, entry, stop, targets, rationale와 outcome reference가 모두 있다.
3. terminal event가 다음 Day bounded payload에 나타난다.
4. Swing은 실제 open shadow를 review하거나 구체적인 data blocker를 남긴다.
5. Alpaca Paper mutation은 이 slice에서 0이다.

### Slice 4: Derivatives vertical과 6-family Board

범위:

- existing derivatives projection 연결
- `ResearchBoardItemV1`과 Dashboard/Hermes Board projection

수용 기준:

1. Derivatives는 실제 IV/skew/term/basis artifact 또는 entitlement blocker를 표시한다.
2. 여섯 family가 실제 source-bound terminal result를 최소 한 건씩 가진 뒤 Board 여섯 칸이
   artifact/no-action/blocked와 next wake를 표시한다.
3. 여섯 의견을 합친 verdict는 없다.
4. 재시작 뒤 동일 result delivery 재전송은 0이다.

### Slice 5: Systematic non-blocking experiment와 Critic

범위:

- Systematic request registration과 one-shot non-blocking launch
- Critic provenance/duplicate/contract/executability 검사
- terminal trial/review feedback 환류

수용 기준:

1. 실제 research source에서 hypothesis와 generated artifact가 생긴다.
2. ledger에 없는 source, rejected duplicate, invalid generated entrypoint와 sandbox preflight 실패가
   trial 전에 거부된다.
3. heavy request 후 30초 안에 다른 family의 light/idle tick이 가능하다.
4. terminal trial과 Reviewer decision이 다음 Systematic bounded payload에 나타난다.
5. Reviewer veto 뒤에도 원장과 artifact가 남고 order authority는 false다.

### Slice 6: slow validation과 연속 실제 세션

범위:

- same-version forward/shadow aggregation
- cost/baseline comparison
- weekly or due-wake Reviewer synthesis
- 5거래일 실제 user-surface acceptance

수용 기준:

1. 같은 strategy version의 historical, forward/shadow와 review identity가 연결된다.
2. review는 accept/reject/inconclusive 중 하나이며 근거와 next question을 가진다.
3. Day/Swing outcome closure와 feedback reuse가 Hermes/Dashboard에서 보인다.
4. 5거래일 동안 family별 result/no-action/blocked가 누락 없이 설명된다.
5. 통계 표본이 부족하면 수익성 주장이 아니라 inconclusive로 남는다.

Paper candidate activation은 이 설계의 다음 단계다. fast/slow research product의 완료를 Paper
mutation 유무에 종속시키지 않는다.

## 12. 개발 우선순위와 drift 방지

각 변경은 시작 전에 다음 문장을 채워야 한다.

> 이 변경은 `[source evidence]`를 `[agent family]`의 `[artifact/outcome/feedback]`으로 연결하고
> `[Hermes/Dashboard surface]`에서 관찰 가능하게 만든다.

채우지 못하면 이 goal의 작업이 아니다.

Slice 1부터 5가 닫히기 전에는 다음을 진행하지 않는다.

- Options Workbench UI 확장
- 새 provider와 data adapter
- 새 scheduler/coordinator/authority framework
- 새 database, queue, retry framework
- 안전 계약의 일반화 또는 재작성
- unrelated dashboard redesign

현재 slice를 실제로 막는 재현된 runtime 장애가 있을 때만 최소 infra fix를 허용한다. fix가 끝나면
즉시 제품 slice로 돌아온다. fixture 성공, schema 추가와 daemon 생존만으로 milestone을 닫지 않는다.

## 13. 검증 전략

### 13.1 변경별 자동 검증

- changed Python targeted pytest
- changed Python Ruff
- changed Python basedpyright
- cursor/restart idempotency
- family action boundary와 subject-ref validation
- terminal status와 artifact invariant
- same-bar stop 우선과 recommendation 계약 회귀
- generated strategy sandbox/ledger/Reviewer 회귀
- Hermes exactly-once projection

### 13.2 수동 QA

- 관련 CLI `--help`
- invalid/missing subject 또는 payload bad input
- 실제 source-bound happy path
- Hermes family card
- Dashboard 6-family Board
- terminal outcome이 다음 cycle prompt에 포함되는 장면
- heavy trial 동안 다른 family tick 진행

### 13.3 연구 유의성

시스템이 유의미하다는 주장은 다음 순서로만 한다.

1. operational: 여섯 역할이 서로 다른 실제 artifact와 feedback을 만든다.
2. decision usefulness: forward shadow에서 비용 포함 baseline 대비 결과가 축적된다.
3. research validity: 충분한 표본에서 regime 안정성, trial 수와 다중검정 영향을 검토한다.
4. Paper readiness: 독립 Reviewer와 기존 Paper gate를 통과한다.

synthetic, replay, historical 또는 짧은 Shadow 결과로 수익성을 주장하지 않는다.

## 14. 완료 정의

이 goal의 설계 구현은 다음을 모두 관찰해야 완료다.

1. 실제 bounded evidence 값이 agent Decide에 도달한다.
2. non-no-action completed는 실제 family 권위 artifact를 가진다.
3. Opportunity, Context, Day, Swing, Systematic과 Derivatives가 각기 다른 실제 결과물을 만든다.
4. Research Board와 Hermes가 여섯 결과와 next wake를 개별 표시한다.
5. Day/Swing outcome이 다음 cycle에서 실제로 소비된다.
6. generated Systematic heavy work가 fast loop를 막지 않는다.
7. Critic과 Reviewer feedback이 다음 hypothesis에 반영된다.
8. Reviewer는 결과를 삭제하거나 주문 권한을 만들지 않는다.
9. 실패, blocked와 no-action이 completed research로 오인되지 않는다.
10. 새 scheduler, coordinator, framework, database 또는 provider가 추가되지 않는다.

이 완료는 수익성 보장이나 Professional Multi-Market OS 전체 완료를 의미하지 않는다. 이후 자연
시장 Paper/shadow acceptance는 기존 상위 마일스톤에서 별도로 닫는다.
