# US Day no-setup 증거 게이트 체크포인트

기준일: 2026-07-22
구현 커밋: `649c952c5dd9282dd6be59701e913b8d5f72c090`

## 해결한 운영 결함

기존 close finalizer는 Paper 계정이 flat이고 대사가 통과하면 source artifact에
실제 ORB 추천이 있어도 `censored_no_setup` terminal을 만들 수 있었다. 이 경우
자연 setup을 놓친 세션이 setup 자체가 없었던 세션으로 잘못 집계될 수 있었다.

이제 no-setup terminal은 해시되는 source artifact 중 정확히 하나의
`paper_recommendations.sqlite3`를 요구한다. finalizer는 원장을 read-only로 열고
해당 NYSE 세션에 `opening_range_breakout` 추천 이력이 한 건이라도 있으면 현재
상태가 `time_exit`여도 `invalid_no_setup_source`로 차단한다. terminal과 Hermes
no-recommendation event는 차단 전에 생성되지 않는다.

## 실제 CLI QA

- 2026-07-15 실제 세션 watch DB: ORB 추천 0건
  - `finalize`: exit 0, `result=censored`
  - open orders 0, positions 0, broker/shadow 대사 통과
- 2026-07-21 실제 세션 watch DB: ORB 추천 1건
  - `finalize`: exit 1, `reason=invalid_no_setup_source`
  - blocked terminal 미생성
- QA delivery DB와 terminal은 임시 경로만 사용하고 확인 후 제거했다.
- 실제 Alpaca Paper에는 GET/WSS 대사만 수행했다.
- `paper_mutation_events`와 `broker_order_events`는 계속 0건이다.

## 자동 검증

- RED: 추천 이력이 있어도 기존 CLI가 exit 0 `censored`를 반환함
- 관련 terminal/acceptance 테스트: 18 passed
- 전체 pytest: 3309 passed
- 전체 Ruff: passed
- 전체 basedpyright: 0 errors, 0 warnings, 0 notes
- compileall: passed
- Python no-excuse: 4 changed files, no violations
- CLI `finalize --help`: exit 0

## 남은 운영 게이트

- 오늘 정규장 current-bar ORB source는 감시기가 자연스럽게 생성해야 한다.
- 자연 setup이 있으면 `censored_no_setup`으로 대체하지 않고 명시적 일회성 arm,
  Paper entry, 보호 OCO, flat, 대사와 Hermes 결과의 실제 lifecycle을 남긴다.
- 자연 setup이 정말 없을 때만 오늘 watch DB를 source artifact로 포함해 close
  finalizer를 실행한다.
