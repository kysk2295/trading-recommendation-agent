# ORB lane 장후 forward-validation runner 체크포인트

날짜: 2026-07-15

상태: **final snapshot 성공 뒤에만 독립 Reviewer를 실행하는 fail-closed 장후 순서 경계 구현**

## 목적

기존 일일 흐름은 다음 네 산출물을 독립적으로 만들 수 있었다.

1. ORB paper metrics
2. exact-scope daily research record
3. adaptive evaluation
4. intraday lane final snapshot과 Reviewer event

이번 runner는 앞의 연구 산출물이 준비된 세션을 대상으로 4번의 두 child CLI를 안전한 순서로 묶는다. 새 전략, 스케줄러, 주문 엔진, 상태변경기 또는 Portfolio Manager를 만들지 않는다.

## 실행 순서

```text
run_intraday_lane_daily_snapshot.py
  ├─ nonzero → snapshot audit append → aggregate blocked → 종료
  └─ zero
       ↓
run_lane_reviewer.py
  ├─ nonzero → review audit append → aggregate blocked
  └─ zero    → aggregate completed
```

- snapshot 단계는 기존 local preflight 뒤에만 고정 Paper credential과 GET/WSS readiness를 사용한다.
- Reviewer 단계는 credential, broker, HTTP, execution DB 또는 mutation 모듈을 사용하지 않는다.
- snapshot 실패 시 Reviewer subprocess 자체를 만들지 않는다.
- Reviewer 실패는 이미 확정된 immutable snapshot을 수정하지 않는다.
- 재실행은 두 child의 기존 exact replay 계약을 그대로 사용하므로 snapshot/review row를 중복하지 않는다.

## CLI

```bash
./run_orb_lane_forward_validation.py outputs/live_sessions/<session> \
  --session-date YYYY-MM-DD \
  --execution-database outputs/paper_execution/paper_execution.sqlite3 \
  --lane-registry outputs/lane_control/lane_registry.sqlite3 \
  --review-ledger outputs/lane_control/lane_review.sqlite3 \
  --output-dir outputs/lane_control/forward_validation
```

허용 인자는 로컬 source와 output path뿐이다. credential, endpoint, arm, fixture, force 인자는 없다. runner가 만드는 child 명령도 mutation smoke 또는 live endpoint를 포함하지 않는다.

## 감사와 보고서

- `post_session_intraday_snapshot_cycles.csv`: 시도한 snapshot child 시작시각·종료코드·상태
- `post_session_lane_reviewer_cycles.csv`: snapshot이 성공한 경우에만 시도한 Reviewer child 감사행
- `orb_lane_forward_validation_ko.md`: 날짜·lane·두 phase 상태·권한 금지·mutation 0건만 포함한 aggregate report

aggregate report에는 session/DB/output path, account fingerprint, credential, endpoint, snapshot/scope key, SHA-256, broker order ID와 raw payload를 쓰지 않는다. child의 상세 report 실패는 성공으로 축소하지 않으며 aggregate report 쓰기 실패는 종료코드 2다.

## 검증

- 새 계약 테스트: 9 passed
- snapshot·Reviewer CLI 포함 focused 회귀: 23 passed
- 전체 회귀: 767 passed
- executable help: 0
- 실제 missing-local 실행: snapshot 1, Reviewer not started, aggregate 1
- fake child success/replay: 각 실행 0, snapshot·review audit 각 2행, aggregate redaction 확인
- fake Reviewer failure: snapshot success, Reviewer failed, aggregate 1
- Ruff focused: 통과
- 변경 Python 파일 Ruff format: 통과
- basedpyright: 0 errors, 0 warnings

수동 QA는 자격증명이나 broker network를 사용하지 않았다. Alpaca Paper POST/DELETE는 0건이며 notional 100 USD, 계획위험 10 USD, 최대 1포지션, 일손실 30 USD, 편도 20bp와 risk fraction 1/3000을 변경하지 않았다.

## 여전히 닫힌 범위

- 열린 정규장 최소 entry→보호 OCO→대사→EOD flat 실제 Paper smoke
- 자동 champion/promote/demote와 주문권한 변경
- 최소 두 champion 전 Portfolio Manager와 다음 세션 risk budget 배분
- swing broker 계좌·주문권한과 market regime 직접 거래권한

이 산출물은 확정수익이 아니라 ORB Paper forward-validation 후보와 blocker를 반복 수집하기 위한 운영 경계다.
