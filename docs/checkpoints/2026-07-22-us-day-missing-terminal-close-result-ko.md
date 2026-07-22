# US Day missing-terminal close 결과 체크포인트

기준일: 2026-07-22
구현 커밋: `88515e8e32c9f50b00e25d9ca17fde2b3bc2b78f`

## 해결한 사용자 결과 공백

scheduled session에 자연 ORB 추천이 있었지만 `run --terminal-output` 결과가 없으면
기존 close finalizer는 거짓 `censored_no_setup`을 막은 뒤 아무 terminal도 남기지
않았다. 이 상태는 모든 scheduled session이 명시적 결과를 가져야 한다는 M2 계약을
충족하지 못했다.

이제 close finalizer는 해시된 `paper_recommendations.sqlite3`에서 해당 NYSE 세션의
ORB 추천 이력을 읽는다.

- 추천 0건: 기존 `censored_no_setup` terminal과 `no_recommendation` delivery
- 추천 1건 이상, 운영 terminal 없음: `natural_setup_without_terminal` blocked
  terminal과 `incident` delivery

blocked 사유는 실제 주문이 없었다고 단정하지 않는다. source에는 자연 setup이
있지만 실행 lifecycle을 증명할 terminal이 없다는 사실만 기록한다. terminal은 현재
broker 상태가 flat이고 broker/shadow 대사가 통과한 경우에만 생성된다.

## 실제 CLI QA

- 2026-07-15 실제 watch DB, ORB 추천 0건
  - exit 0, `result=censored`
  - delivery: `no_recommendation / censored_no_setup`
- 2026-07-21 실제 watch DB, ORB 추천 1건
  - exit 0, `result=blocked`
  - reason: `natural_setup_without_terminal`
  - transitions: `flat`, `reconciled`, `hermes_result_projected`
  - delivery: `incident / blocked`
- 두 terminal 모두 open orders 0, positions 0, broker/shadow 대사 통과를 기록했다.
- QA delivery와 terminal은 임시 경로에서 확인 후 제거했다.
- 실제 Alpaca Paper에는 GET/WSS 대사만 수행했고 mutation은 0건이다.

## 검증

- RED: 추천 이력이 있으면 CLI가 blocked 출력만 하고 terminal을 쓰지 않음
- 관련 terminal/projection/acceptance 테스트: 19 passed
- 전체 pytest: 3309 passed
- 전체 Ruff: passed
- 전체 basedpyright: 0 errors, 0 warnings, 0 notes
- compileall: passed
- Python no-excuse: 4 changed files, no violations
- CLI `finalize --help`: exit 0
- CLI 필수 인자 누락: exit 2

## 남은 운영 게이트

- 오늘 정규장 결과는 오늘 생성되는 watch DB와 clean runtime commit에 결합해야 한다.
- 실제 `run` terminal이 있으면 close finalizer는 `--terminal-input` refresh 경로를
  사용해 Hermes acknowledgement와 최종 대사를 갱신한다.
- missing-terminal blocked 결과는 자연 Paper lifecycle 완료 증거가 아니며 M2를
  완료시키지 않는다.
