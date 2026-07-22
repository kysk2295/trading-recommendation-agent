# US Swing source incident delivery 체크포인트

날짜: 2026-07-22

## 완료한 제품 동작

- 장후 completed-day scanner는 성공 시각 또는 `source_unavailable` 실패를 typed outcome으로 반환한다.
- 실패한 장후 source cycle은 추천이나 trial 성과로 축소되지 않고 Hermes `INCIDENT`로 기록된다.
- incident identity와 payload는 session date, failure reason, lane, strategy version으로 결정론적으로 고정된다.
- incident `occurred_at`은 해당 session의 공식 정규장 종료시각이므로 다른 재시도 시각에도 exact replay된다.
- 같은 실패 재실행은 사건을 추가하지 않으며, 이후 source가 복구되면 같은 session scanner를 다시 실행할 수 있다.
- incident에는 예외 문자열, provider 응답, 자격증명, 계좌 정보가 포함되지 않는다.

## 운영 결과 계약

- `SwingOperatingResult.incidents`가 tick에서 처리한 source incident 수를 노출한다.
- private operating report가 incident 수와 broker mutation 0을 기록한다.
- source failure는 처리된 incident이므로 CLI exit 0이며 Hermes delivery worker가 사용자에게 전달할 수 있다.
- 이 경로는 completed daily data 기반 shadow 연구 전용이며 account, position, order endpoint를 호출하지 않는다.

## 검증

- US Swing 집중 테스트: `66 passed`
- 전체 회귀: `3285 passed`
- Ruff 전체: 통과
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- 변경 production compileall, format, no-excuse: 통과
- 변경 production 파일: 모두 250 pure LOC 이하

## 수동 CLI QA

- `run_us_swing_operating_session.py --help`: exit 0
- 잘못된 session date: exit 1, scanner/provider 미실행
- fixture happy path: exit 0, WATCH 1건, prospective trial 1건, broker mutation 0
- missing fixture source path: exit 0, INCIDENT 1건, status `blocked_source_unavailable`
- 같은 source failure replay: delivery event는 계속 1건

## 다음 운영 게이트

- 실제 NYSE 장후 current-session completed daily source로 auto-universe cycle을 실행한다.
- Hermes delivery acknowledgement까지 확인해 source incident 또는 WATCH/NO_RECOMMENDATION 사용자 전달을 실증한다.
- 독립 executable champion 2개 전에는 Allocation Manager 구현 및 위험예산 배분을 열지 않는다.
