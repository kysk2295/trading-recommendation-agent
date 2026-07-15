# Global Experiment Ledger와 Strategy Lifecycle 설계

날짜: 2026-07-15

상태: 구현 전 확정 설계

## 1. 목표

세 lane이 공유하는 전역 append-only experiment ledger를 추가해 가설, 전략 버전, 실험 시도와 전략 상태 변경을 재생 가능한 계보로 보존한다. 기존 lane registry와 독립 Reviewer는 유지하며, Reviewer가 직접 전략 상태·주문권한·위험예산을 변경하지 않는다.

이 단계의 첫 operational 대상은 `intraday_momentum`의 ORB, VWAP reclaim, HOD breakout, Gap-and-Go 네 전략이다. swing과 market regime도 같은 계약을 사용하지만, 현재 각각 shadow-only와 signal-only 권한을 유지한다.

## 2. 현재 공백

현재 구현에는 다음이 있다.

- lane registry의 immutable `ExperimentScope`
- 일별 exact-scope `DailyResearchRecord`
- adaptive evaluation과 독립 `LaneReviewEvent`
- `LaneDailySnapshot.champion_strategy_versions`

그러나 전역 `hypotheses`, `strategy_versions`, `experiment_trials`, `promotion_events` 원장은 없다. 현재 snapshot producer는 champion을 항상 비우고 Reviewer는 promotion을 항상 차단한다. 일일 JSONL만으로는 실패한 실험 시도, 전략 버전의 현재 상태, 상태 전이 근거 또는 다음 세션 유효 상태를 전역에서 재생할 수 없다.

또한 source-bound Paper entry가 임시 `paper-smoke-v1`을 사용하던 계보 결함은 `af12bbb`에서 canonical `StrategyResearchContract.strategy_version`을 사용하도록 먼저 고쳤다.

## 3. 비교한 접근

### 3.1 lane registry schema 확장

기각한다. lane registry는 lane 실행 계약과 일일 확정 snapshot의 소유자다. 전역 가설·cross-lane trial·전략 상태를 넣으면 lane control-plane과 연구 계보의 Writer 책임이 섞인다.

### 3.2 lane review ledger에 상태 이벤트 추가

기각한다. Reviewer는 query-only source를 읽어 독립 권고를 남겨야 한다. 같은 Writer가 상태까지 바꾸면 검토와 결정의 독립성이 사라진다.

### 3.3 별도 global experiment ledger와 Lifecycle Controller

채택한다. 전역 ledger는 broker·credential·execution mutation을 import하지 않는다. Reviewer는 evidence producer로 남고, 별도 deterministic Lifecycle Controller가 고정 policy와 exact evidence를 검증해 다음 세션부터 유효한 상태 이벤트만 append한다.

## 4. 저장소 경계

기본 경로는 `outputs/research_control/experiment_ledger.sqlite3`다. 다른 control-plane DB와 파일을 공유하지 않는다.

- SQLite schema version 1
- database와 `.writer.lock` mode 600
- 한 개의 비차단 Writer lease
- 한 Writer context의 append 묶음은 하나의 transaction이며 정상 종료 시 commit, 예외 시 전체 rollback
- reader는 `mode=ro`, `PRAGMA query_only = ON`
- WAL, foreign keys, payload JSON과 canonical SHA-256 key
- 모든 table에 UPDATE/DELETE 금지 trigger
- exact replay는 새 행을 만들지 않음
- 같은 immutable identity의 payload가 다르면 typed conflict
- reader는 payload를 model validation하고 key를 다시 계산

원장은 lane registry와 review ledger를 ATTACH하거나 cross-database foreign key로 묶지 않는다. bootstrap과 Controller가 두 DB를 각각 query-only로 읽고 canonical key·payload를 애플리케이션 경계에서 검증한다.

## 5. 핵심 모델

### 5.1 `HypothesisRegistration`

- `hypothesis_id`
- canonical `ExperimentScope`와 `experiment_scope_key`
- `primary_lane`
- `hypothesis`
- `falsification_rule`
- `source_registered_at`
- `ledger_recorded_at`

scope key, hypothesis ID와 lane은 embedded scope와 정확히 일치해야 한다. `ledger_recorded_at >= source_registered_at`을 요구해 기존 lane registry contract를 이관한 시점과 최초 source registration 시점을 구분한다.

### 5.2 `StrategyVersionRegistration`

- `strategy_id`
- immutable `strategy_version`
- `hypothesis_id`와 `experiment_scope_key`
- `lane_id`
- `code_version`
- `parameter_set`
- `data_contract`
- `cost_model`
- `portfolio_policy`
- `source_registered_at`
- `ledger_recorded_at`

같은 `strategy_version`은 정확히 한 가설·scope·lane·code/parameter contract에만 속한다. evaluator와 개별 data partition은 strategy version이 아니라 trial에 속한다.

### 5.3 `ExperimentTrialRegistration`

- `trial_id`
- `strategy_version`
- `trial_kind`: `historical_replay`, `shadow_forward`, `broker_paper_forward`, `equal_risk_comparison`, `cross_lane_hypothesis`
- `experiment_scope_key`
- `evaluator_version`
- `data_version`
- `feed_entitlement`
- `planned_start`, `planned_end`
- `registered_at`
- `evidence_budget`

trial은 첫 source session 전에 등록한다. cross-lane trial은 embedded scope가 `cross_lane_hypothesis`이고 사전 등록된 두 개 이상 source hypothesis를 가져야 한다. 한 lane의 결과를 기존 single-lane trial에 사후 혼합할 수 없다.

### 5.4 `ExperimentTrialEvent`

- `trial_id`
- `sequence`
- `event_kind`: `started`, `completed`, `failed`, `censored`
- `occurred_at`
- 정렬된 `artifact_sha256s`
- 정렬된 `reason_codes`
- `previous_event_key`

registration 뒤 first event sequence는 1이다. completed·failed·censored는 terminal이며 뒤 event를 허용하지 않는다. 실패와 검열도 삭제하거나 성공 0건으로 바꾸지 않는다. event chain은 gap, fork와 previous-key 불일치를 거절한다.

### 5.5 `StrategyLifecycleEvent`

- `strategy_version`
- `sequence`
- `event_kind`: `registration`, `transition`
- `from_state`
- `to_state`
- `policy_version`
- `decision_session_date`
- `effective_session_date`
- `decided_at`
- 정렬된 `evidence_keys`
- 정렬된 `reason_codes`
- `previous_event_key`

최초 event는 `sequence=1`, `event_kind=registration`, `from_state=null`, `previous_event_key=null`이다. 새 전략은 `idea`로 등록한다. 이미 코드·scope·일일 연구 계약이 존재하는 현재 전략을 이관할 때만 `historical`, `experimental_shadow` 또는 `experimental_paper`를 initial state로 허용하고 `existing_contract_import` reason과 canonical source evidence hash를 요구한다. `challenger`, `paper_champion`, `suspended`, `rejected`는 initial state가 될 수 없다. 이관 event의 `decided_at`과 `effective_session_date`는 새 ledger 기록 시점 기준이며 과거 상태 전이를 합성하지 않는다.

이후 event는 `event_kind=transition`, `sequence=previous.sequence+1`이며 latest recorded event의 exact `to_state`와 previous key를 요구한다. 아직 effective date가 오지 않은 event가 있으면 다음 transition을 append하지 않는다. `effective_session_date`는 결정일보다 뒤의 NYSE regular session이어야 하므로 장후 결과가 당일 주문이나 snapshot을 소급 변경하지 않는다. state reader는 `as_of_session_date`를 받아 그 날짜까지 effective한 마지막 event만 projection한다.

## 6. 상태기계

현재 daily research의 실제 단계를 반영해 `experimental_shadow`를 명시한다.

```text
idea
→ historical
→ experimental_shadow
→ experimental_paper
→ challenger
→ paper_champion
↔ suspended
→ rejected
```

허용 전이는 닫힌 표로 검증한다.

- `idea → historical | rejected`
- `historical → experimental_shadow | rejected`
- `experimental_shadow → experimental_paper | challenger | suspended | rejected`
- `experimental_paper → challenger | suspended | rejected`
- `challenger → paper_champion | suspended | rejected`
- `paper_champion → suspended`
- `suspended → experimental_shadow | experimental_paper | challenger | paper_champion | rejected`
- `rejected`는 terminal

복구 전이는 suspension 직전의 non-suspended state보다 높은 단계로 갈 수 없고, 동일 strategy version에 대해 원인이 해소됐다는 새 exact evidence가 있어야 한다. 새 parameter나 rule은 기존 version 복구가 아니라 새 `StrategyVersionRegistration`이다.

첫 bootstrap은 네 intraday version을 새 ledger 기록 시각의 `existing_contract_import` registration event 하나로 `experimental_shadow`에 등록한다. 과거 idea/historical 전이를 합성하지 않는다. ORB의 explicit-arm 1회 functional smoke는 champion 또는 반복 pilot 권한을 뜻하지 않는다.

## 7. Lifecycle Controller

Controller는 다음 source만 query-only로 읽는다.

- global experiment ledger의 current projection
- lane registry의 finalized `LaneDailySnapshot`
- lane review ledger의 exact `LaneReviewEvent`
- 등록된 trial의 terminal evidence artifacts

Controller는 Alpaca, KIS, credential, HTTP, execution store, mutation adapter 또는 Portfolio Manager를 import하지 않는다.

Reviewer event의 `automatic_state_change_allowed=false`는 Reviewer 자신이 상태를 바꿀 권한이 없다는 뜻으로 유지한다. Controller는 Reviewer action 하나만으로 전이하지 않고, lifecycle policy가 요구하는 모든 독립 evidence key를 다시 검증한다.

### 7.1 중단·강등

다음 구조적 incident는 현재 executable state에서 다음 세션 `suspended` 후보가 된다.

- causality/lookahead 위반
- 미해결 broker-shadow/ledger 불일치
- EOD non-flat
- immutable source 충돌
- 명시된 mature-window degradation과 exact `suspend` review

일시 provider 실패는 해당 session을 censored/failed로 보존하되 단 한 번으로 전략 알파 실패나 영구 suspension을 만들지 않는다.

### 7.2 `paper_champion` 승격

모든 조건을 동시에 만족해야 한다.

1. 최소 60 적격 forward session
2. 최소 100 완결 거래
3. broker Paper PF 1.15 이상
4. conservative shadow PF 1.15 이상
5. 편도 20bp 평균 거래수익 양수
6. 거래일 block-bootstrap 95% CI 평균 하한 0 이상
7. 모든 trial을 포함한 DSR/PBO gate 통과
8. 단일 거래가 총이익의 15% 이하
9. 인접 parameter plateau 통과
10. causality·미대사 주문·overnight position incident 0
11. SIP 또는 동등 consolidated feed 검증
12. 기존 champion이 있으면 동일 위험·동일 데이터의 최소 20-session overlap 비교 우월

현재 코드에는 broker/shadow 통합 promotion evidence, DSR/PBO, plateau와 SIP evidence가 없으므로 첫 Controller checkpoint에서 `paper_champion` 전이는 반드시 blocker로 남고 event를 생성하지 않는다.

## 8. 일일 순서

```text
session artifacts
→ DailyResearchRecord
→ AdaptiveEvaluation
→ finalized LaneDailySnapshot
→ independent LaneReviewEvent
→ Lifecycle Controller decision
→ next-session-effective StrategyLifecycleEvent
```

day D snapshot은 day D 개장 전에 유효했던 champion projection만 담는다. day D 장후 결정은 다음 NYSE session부터 반영한다. Portfolio Manager는 최소 두 개의 executable lane champion이 생긴 이후 별도 설계로만 추가한다.

## 9. 첫 구현 체크포인트

1. models, canonical keys, SQLite schema/store와 state projection
2. append-only/lease/query-only/integrity/conflict/replay/chain 테스트
3. 현재 네 intraday `StrategyResearchContract`를 exact lane scope와 결합하는 무네트워크 bootstrap CLI
4. bootstrap 시 네 strategy version을 `experimental_shadow`로 등록
5. help, 잘못된 경로, fake happy path 수동 QA

첫 체크포인트는 Reviewer ingestion, 자동 Controller 전이, entry admission 변경, champion snapshot, Portfolio Manager 또는 broker mutation을 구현하지 않는다. 이 기능은 다음 체크포인트에서 exact review evidence와 연결한다.

## 10. 테스트 계약

- model은 비정규 ID, naive time, unsorted/duplicate evidence, scope mismatch와 invalid state transition을 거절
- store는 mode 600, single Writer, UPDATE/DELETE 금지, exact replay와 immutable conflict를 검증
- reader는 `mode=ro/query_only`이며 payload/key 손상을 감지
- trial event chain은 sequence gap, fork, terminal 뒤 append를 거절
- lifecycle projection은 previous key와 from-state 불일치를 거절
- bootstrap은 lane registry source를 credential·network보다 먼저 읽고 네 exact current contract만 등록
- 기존 lane/review/execution schema와 전체 회귀를 변경하지 않음
- 외부 Alpaca Paper POST/DELETE는 0건 유지

## 11. 비목표

- 위험 한도 확대
- 실제 자금 거래 또는 Alpaca live endpoint
- Reviewer의 직접 상태 변경
- lane 간 사후 성과 혼합
- 과거 실험을 새 원장에 소급 사전등록한 것처럼 표현
- 증거가 없는 champion 생성
- 최소 두 executable lane champion 전 Portfolio Manager 구현
