# US Swing Hermes 수명주기 전달 체크포인트

## 완료 범위

- `run_us_swing_shadow.py`가 완료 일봉 source를 다시 검증한 뒤 조건부 신호를 Hermes `WATCH`로 투영한다.
- 같은 장후 source에 신호가 없으면 결정적 `NO_RECOMMENDATION` 한 건을 남긴다.
- production 기본 delivery 경로는 Hermes single-worker가 사용하는 `outputs/hermes/delivery.sqlite3`다.
- `run_swing_shadow_trial.py finalize`는 global trial terminal과 Swing shadow 원장을 교차 검증한다.
- 미체결 `expired`는 원래 WATCH의 reply lineage를 가진 `NO_RECOMMENDATION`이 된다.
- shadow 진입 후 `stopped`, `targeted`, `time_exit`는 원래 WATCH의 reply lineage를 가진 `EXIT`가 된다.
- 원래 WATCH가 없거나 trial artifact hash가 다르면 terminal delivery는 fail-closed 한다.
- cycle과 terminal을 재실행해도 동일 delivery event가 추가되지 않는다.

## 안전 경계

- Alpaca 계좌·주문 mutation: 0건
- 실자금 endpoint: 사용하지 않음
- 실제 Telegram 전송: 0건
- fixture QA는 production delivery DB가 아닌 임시 mode-600 DB만 사용
- lifecycle 자동 승격: 없음
- champion 선언: 없음
- Allocation Manager 위험예산 변경: 없음

Swing source의 Alpaca 사용은 기존 read-only historical bars 경로에 한정된다. terminal projector와 trial CLI는 broker, credential, Paper execution 또는 주문 모듈을 import하지 않는다.

## 검증

- Swing 전체 테스트: `58 passed`
- 전체 테스트: `3274 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- Python no-excuse rules: 변경 production 모듈 위반 0건
- 실제 scanner CLI fixture/replay:
  - first return code: 0
  - replay return code: 0
  - delivery kinds: `watch`
  - replay 후 delivery event count: 1
- 독립 trial CLI driver:
  - register/start/finalize/replay return code: 모두 0
  - terminal delivery kinds: `watch,no_recommendation`
  - replay 후 delivery event count: 2
- 외부 계좌·주문 mutation: 0건

## 제품 상태와 다음 경계

US Swing Agent는 이제 `completed daily bars -> conditional WATCH 또는 daily NO_RECOMMENDATION -> multi-session shadow terminal -> EXIT/expired reply`의 사용자 결과 계약을 갖는다. 아직 실제 NYSE post-close bounded universe에서 생성된 forward signal과 다중세션 terminal은 0건이므로 운영 완료나 수익성 증거가 아니다.

다음 운영 경계는 실제 post-close source에서 후보를 수집하고, 다음 정규장부터 최대 10거래일 동안 shadow 상태를 재시작 가능하게 전진시킨 뒤 Reviewer와 Telegram acknowledgement까지 완주하는 것이다. 이 표본이 쌓여도 사전등록된 승격 게이트를 충족하기 전에는 champion이나 Allocation 입력으로 사용하지 않는다.
