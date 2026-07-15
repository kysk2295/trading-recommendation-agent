# Lifecycle Controller v1 설계

날짜: 2026-07-15

상태: 구현 승인 기준

## 1. 목표

독립 Reviewer의 immutable event 하나를 직접 상태변경 명령으로 취급하지 않고, global experiment ledger의 현재 projection과 finalized lane snapshot을 query-only로 다시 검증해 다음 NYSE 세션부터 유효한 lifecycle transition만 append한다.

첫 버전은 ORB `experimental_shadow` 계보의 명확한 mature-window degradation에 대한 `suspended` 전이만 연다. 승격·복구·champion·주문권한·위험예산 변경은 열지 않는다.

## 2. 입력과 권한

Controller는 다음 세 source만 읽는다.

- `LaneRegistryReader`: exact intraday manifest/scope와 finalized `LaneDailySnapshot`
- `LaneReviewReader`: snapshot에 결합된 exact `LaneReviewEvent`
- `ExperimentLedgerStore`: query-only hypothesis/version/lifecycle projection과 lifecycle event append Writer

Controller와 CLI는 Alpaca, KIS, credential, HTTP, execution store, mutation adapter, broker client와 Portfolio Manager를 import하지 않는다. lifecycle event를 append해도 주문권한이나 lane risk contract는 변하지 않는다.

## 3. exact source 검증

Controller는 다음 조건을 모두 다시 확인한다.

1. current intraday manifest와 ORB experiment scope의 payload/key가 canonical source와 동일하다.
2. snapshot key를 payload에서 다시 계산할 수 있고 lane/date/manifest/scope가 요청과 일치한다.
3. intraday snapshot은 open order 0, position 0, planned open risk 0으로 finalized됐다.
4. review event key를 payload에서 다시 계산할 수 있고 snapshot/scope/date/strategy/evaluator/reviewer version이 동일하다.
5. `snapshot.finalized_at <= review.reviewed_at <= controller.decided_at`이다.
6. global ORB hypothesis/version이 canonical strategy contract와 일치하고 lifecycle chain이 유효하다.
7. Controller 결정 시각의 New York 날짜는 처리할 `session_date`와 동일하다.
8. latest recorded lifecycle event가 그 session까지 이미 effective하다. future-effective pending event가 있으면 새 결정을 만들지 않는다.

source 손상·불일치·시간 역행은 blocker 결과가 아니라 `InvalidLifecycleControllerSourceError`로 fail-closed한다.

## 4. v1 결정표

| adaptive action | v1 결과 | lifecycle append |
|---|---|---|
| `collecting`, `shadow_continue` | `no_change` | 없음 |
| `diagnose` | `no_change` + diagnosis blocker | 없음 |
| `early_stop` | `blocked` | 없음. irreversible reject는 v1에서 금지 |
| `comparison_ready` | `blocked` | 없음. equal-risk terminal trial evidence 미구현 |
| `promotion_review` | `blocked` | 없음. broker/shadow·DSR/PBO·plateau·SIP evidence 미구현 |
| `suspend` | 아래 exact 조건 통과 시 `transitioned` | `suspended` event 하나 |

`suspend` 조건은 다음과 같다.

- Reviewer action이 `stop_recommended`
- reason에 `five_day_clear_degradation` 존재
- snapshot의 data quality가 complete이고 incident가 없음
- 현재 state가 `experimental_shadow`, `experimental_paper`, `challenger` 또는 `paper_champion`
- 동일 review evidence의 Controller event가 아직 없거나 exact replay임

한 번의 provider/data-quality 실패는 suspension으로 바꾸지 않는다. 현재 state가 이미 `suspended` 또는 `rejected`면 v1은 새 event를 만들지 않는다.

## 5. lifecycle event

첫 신규 suspend event는 다음 계약을 사용한다.

- `policy_version="lifecycle_controller_v1"`
- `sequence=latest.sequence + 1`
- `from_state=latest.to_state`
- `to_state=suspended`
- `decision_session_date=session_date`
- `effective_session_date=다음 NYSE regular session`
- `previous_event_key=latest event key`
- `evidence_keys=(latest lifecycle key, review event key, snapshot key)` 정렬
- `reason_codes=("five_day_clear_degradation", "review_evidence_verified")`

같은 session/policy의 event가 이미 있으면 exact review/snapshot/previous evidence와 event shape를 검증해 `created=false` replay로 반환한다. 내용이 다르면 immutable source 오류다. 같은 review의 재실행 시 새 `decided_at`으로 다른 event를 만들지 않는다.

## 6. 결과와 CLI

서비스 결과는 `no_change`, `blocked`, `transitioned` 중 하나와 created 여부, from/to state, 정렬된 reason/blocker code, lifecycle event를 반환한다.

CLI 인자는 다음으로 제한한다.

```text
--experiment-ledger
--lane-registry
--review-ledger
--session-date
--output-dir
```

정상적으로 평가한 `blocked`와 `no_change`는 exit 0이며 source/schema/lease/conflict 실패만 exit 1이다. atomic report에는 outcome·created/replayed·state·정책 blocker·broker mutation 0건만 쓰고 path, key, hash, strategy ID, account/broker 식별자와 raw payload를 쓰지 않는다.

## 7. 비목표

- `experimental_shadow → challenger` 자동 전이
- `paper_champion` 전이
- `suspended` 복구
- early-stop 자동 reject
- trial 자동 등록·완료
- Reviewer의 직접 상태변경
- execution admission·lane snapshot champion 필드 변경
- Portfolio Manager
- broker mutation 또는 위험 한도 확대
