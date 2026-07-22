# US Day Paper 운영 coordinator 체크포인트

기준일: 2026-07-22

## 완료 범위

- 현재 ORB 추천과 최신 1분봉을 기존 `PaperOrderAdmissionRequest`로 읽는다.
- Hermes의 서명된 일회성 arm을 session, lane, account, risk, commit, champion, strategy version에 결합한다.
- 기존 `PaperOperatingSession` 하나가 recovery, admission, entry, protective OCO, safety action, terminal 대사를 소유한다.
- coordinator는 별도 broker client나 두 번째 execution writer를 만들지 않는다.
- actionable과 terminal 결과를 Hermes delivery 원장에 root/reply lineage로 투영한다.
- CLI는 계좌 fingerprint, API 자격증명, arm signing key를 출력하지 않는다.

## Fixture 검증 행렬

- 자연 흐름: actionable -> entry acknowledged -> protective OCO acknowledged -> flat -> reconciled -> Hermes result
- 부분체결: 기존 OCO cancel acknowledgment 후 현재 수량 보호주문 재설치
- 진입 거절 및 ambiguous 응답의 targeted recovery
- 프로세스 재시작 시 동일 intent exposure를 복구하고 중복 진입 금지
- 외부 계좌 활동과 broker/shadow 불일치 fail-closed
- stale bar, stale quote, closed market, daily loss latch 차단
- 이미 소비된 arm의 중복 주문 금지
- EOD 미체결 주문 취소 후 포지션 평탄화
- terminal timeout에서 flat을 주장하지 않고 incident 투영

## 관찰된 검증

- Task 7 신규 테스트: 17 passed
- 기존 Paper operating/mutation 회귀 포함: 44 passed
- 전체 저장소 테스트: 3218 passed
- 변경 파일 Ruff: passed
- 변경 파일 basedpyright: 0 errors, 0 warnings
- 전체 저장소 basedpyright: 0 errors, 0 warnings
- `compileall` (`trading_agent`, Hermes integration): passed
- 전체 Ruff: Task 7은 통과, 별도 사용자 수정 파일의 기존 121자 한 줄 1건은 보존
- Python no-excuse rules: passed
- CLI `--help`: exit 0
- CLI missing watch DB: exit 1, redacted `invalid_current_orb_source`
- CLI injected fixture happy path: exit 0, redacted `completed`

## 아직 완료로 주장하지 않는 범위

- 이 체크포인트에서 실제 Alpaca Paper POST는 수행하지 않았다.
- 정규장 자연 setup의 entry -> OCO -> flat 증거와 세 개의 scheduled-session manifest는 Task 8의 운영 증거 게이트다.
- 장이 닫혔거나 현재 데이터가 없으면 setup을 강제로 만들거나 임계값을 완화하지 않는다.
- Paper 결과는 수익성 근거가 아니라 forward-validation 운영 후보의 실행 증거다.
