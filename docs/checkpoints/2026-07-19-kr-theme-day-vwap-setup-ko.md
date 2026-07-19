# KR theme day 완료봉 VWAP setup 체크포인트

## 완료 범위

- `KrCompletedMinuteBar`는 KR symbol, 1분 시작·종료·최초 관측시각, OHLC, 거래량, 실제 거래대금과 canonical evidence를 frozen contract로 보존한다.
- KST 09:00 장 시작부터 최신 봉까지 정확히 연속된 완료봉만 허용한다. 분봉 공백, 미래 관측, 늦은 평가, 장외 봉과 중복 evidence는 fail-closed한다.
- session VWAP은 각 시점까지의 누적 `trading_value_krw / volume`으로 계산하며 provider가 준 미래 누적값이나 현재 형성 중인 봉을 사용하지 않는다.
- v1 규칙은 `1%` 이상 확장, VWAP `±20bp` 첫 눌림, 최대 `5봉` 안의 `5bp` 재돌파, 눌림 봉 대비 `1.2배` 거래량을 순서대로 요구한다.
- latest 완료봉에서 첫 재돌파가 성립한 경우에만 30초 유효 setup을 만든다. 손절은 첫 눌림 저가, 목표는 trigger 종가 기준 `1R`·`2R`이다.
- setup ID는 Opportunity ID, producer version, symbol, trigger 종료시각과 evidence에 결합되어 exact replay된다.
- fixture E2E에서 이 setup을 기존 KR 시장제약 gate와 현재 호가 projector에 넣어 typed shadow signal까지 연결했다.

## 안전 경계

- extractor는 immutable local input만 소비하는 pure function이며 provider, credential, SQLite, network와 broker를 import하거나 호출하지 않는다.
- 현재 단계는 setup과 추천 신호의 결정 계약이다. 체결, 수익성, strategy champion 또는 실시간 운영 준비의 증거가 아니다.
- KR 계좌·잔고·포지션·주문 경로는 없고 국내 주문 mutation은 계속 금지다.

## 검증

- focused setup/signal: `9 passed`
- 전체 회귀: `2639 passed`
- Ruff, changed-file format: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, no-excuse: 통과
- 최소 fixture driver: setup `1`, signal `1`, no-reclaim setup `0`, external mutation `0`

## 다음 단계

LS/KIS read-only 분봉·현재 호가·VI/status 응답을 raw-first evidence로 정규화하는 provider adapter를 이 계약 앞에 연결한다. 그 뒤 exact multi-market strategy version에 사전등록된 append-only shadow trial과 KR 특수 상태를 반영한 보수적 fill 원장을 추가한다. 국내 주문은 열지 않는다.
