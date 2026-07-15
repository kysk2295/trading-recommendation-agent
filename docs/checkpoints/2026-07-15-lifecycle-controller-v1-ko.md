# Lifecycle Controller v1 체크포인트

날짜: 2026-07-15

상태: **독립 Reviewer의 exact evidence를 다시 검증해 성숙 전략의 명확한 열화만 다음 세션 `suspended`로 전이**

## 목적

Reviewer event는 권고이며 직접 명령이 아니다. 이번 단계는 lane registry, review ledger와 global experiment ledger를 다시 읽어 ORB의 finalized 일일 evidence와 현재 lifecycle chain이 정확히 결합된 경우에만 제한된 상태 전이를 append한다.

v1은 자동 승격 시스템의 일반 해석기가 아니다. 이미 성숙한 전략의 최근 5일 명확한 열화에 대한 중단만 열고, 조기 reject·비교·promotion·복구·champion·주문권한·위험예산 변경은 닫아 둔다.

## 검증 계약

Controller는 다음 source를 exact payload와 canonical key로 다시 검증한다.

1. 현재 canonical intraday manifest와 ORB experiment scope
2. 요청 거래일의 finalized `LaneDailySnapshot`
3. snapshot에 결합된 exact `LaneReviewEvent`
4. canonical ORB hypothesis·strategy version과 현재 lifecycle previous-key chain
5. snapshot finalize, review, Controller 결정 시각의 단조성
6. 처리 세션까지 effective한 latest event와 future pending event 부재

snapshot은 open order, position과 planned open risk가 모두 0이어야 한다. source 손상·불일치·시간 역행은 일반 blocker로 축소하지 않고 fail-closed 오류로 처리한다.

## v1 결정

- `collecting`, `shadow_continue`: `no_change`
- `diagnose`: 진단 blocker를 남기고 `no_change`
- `early_stop`: irreversible reject가 없어 `blocked`
- `comparison_ready`: equal-risk terminal trial evidence가 없어 `blocked`
- `promotion_review`: broker/shadow, DSR/PBO, parameter plateau, SIP evidence가 없어 `blocked`
- `suspend`: exact `stop_recommended`와 `five_day_clear_degradation`, complete data quality가 모두 맞을 때만 `transitioned`

신규 suspension event는 latest lifecycle key, review key와 snapshot key를 evidence로 묶고 결정 거래일 다음 NYSE 정규 세션부터 유효하다. 같은 review replay는 최초 event를 그대로 반환하고 새 행을 만들지 않는다. 이미 `suspended` 또는 `rejected`이면 새 event가 없다.

## 로컬 CLI

`run_lifecycle_controller.py`는 다음 다섯 인자만 받는다.

```text
--experiment-ledger
--lane-registry
--review-ledger
--session-date
--output-dir
```

정상 평가된 `no_change`, `blocked`, `transitioned`는 exit 0이고 source/schema/lease/conflict 실패는 exit 1이다. mode 600 atomic report에는 aggregate outcome, created/replay, from/to state, 고정 policy blocker와 external broker mutation 0건만 기록한다. DB path, key, hash, strategy ID, account·broker 식별자와 raw review reason은 기록하지 않는다.

CLI와 service는 Alpaca, KIS, credential, HTTP, execution store, mutation adapter와 Portfolio Manager를 import하지 않는다. pure control-plane import를 지키기 위해 Alpaca Paper URL 상수는 network-free 계약 모듈로 분리했고 package root의 `RecommendationEngine` 공개 API는 lazy import로 유지했다.

## 검증

- Lifecycle Controller service 신규 테스트: 19 passed
- Controller·Reviewer·global store focused 회귀: 103 passed
- Controller CLI 테스트: 6 passed
- CLI/service·Paper config·lane model·package 결합 회귀: 63 passed
- 전체 회귀: 892 passed
- Ruff 및 변경 Python format: 통과
- basedpyright: 0 errors, 0 warnings
- executable `--help`: exit 0
- unknown option: exit 2, output 생성 0건
- missing three ledgers: exit 1, source DB 생성 0건, redacted blocked report 확인
- 현재 뉴욕 pre-close 시각의 direct executable fixture: exit 1, `blocked_source`, lifecycle transition 0건
- post-close 고정 시계 suspend fixture: 최초 exit 0·`transitioned`·created true, replay exit 0·created false, lifecycle event 총 2개
- promotion fixture: exit 0·`blocked`, 네 promotion evidence blocker, lifecycle event는 registration 1개 유지
- report mode: 600
- 모든 fixture report: aggregate field와 external broker mutation 0건만 기록
- 실제 Alpaca Paper POST/DELETE: 0건

intraday pilot 한도인 notional 100 USD, 계획위험 10 USD, 최대 1포지션, 일손실 30 USD, 편도 20bp와 risk fraction 1/3000은 변경하지 않았다.

## 체크포인트 커밋

- `7059edf`: Lifecycle Controller v1 설계와 구현 계획
- `8f6a393`: evidence-bound suspension service
- `0bb6a78`: local-only redacted Controller CLI와 network-free import 경계

## 다음 단계

- ORB daily forward-validation 결과를 exact scope의 사전등록 trial과 terminal `completed`·`failed`·`censored` evidence로 연결
- 현재 정규장·credential·current ORB 후보가 모두 있을 때만 축소 entry→보호 OCO→복구→EOD flat 실제 Alpaca Paper smoke
- equal-risk terminal trial과 승격 evidence 계약이 완성된 뒤 comparison/promotion Controller를 별도 구현
- swing은 shadow-only, market regime은 signal-only 유지
- 최소 두 executable lane champion 전 Portfolio Manager 금지

현재 ORB와 나머지 intraday 전략은 확정수익 전략이 아니라 Paper forward-validation 후보이다.
