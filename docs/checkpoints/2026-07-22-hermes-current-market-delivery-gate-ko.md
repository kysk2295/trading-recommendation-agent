# Hermes 현재시장 전달 Freshness Gate 체크포인트

기준 직전 커밋: `6968d4a50fe668b266e315f2aee4d2cc4b9374cb`

## 판정

- Hermes worker는 과거 또는 미래 시각의 watch/actionable event를 Telegram sender에 넘기지 않는다.
- source event 시각부터 delivery claim 시각까지 0초 이상 30초 이하인 경우만 현재시장 알림으로 보낸다.
- ineligible event는 attempt 1건과 terminal `market_event_ineligible` transition을 남기고 재시작 뒤 다시 claim하지 않는다.
- 이 gate를 통과한 fresh event의 기존 acknowledgement와 reply-lineage 계약은 그대로 유지된다.

## 정책 경계

이 30초 gate는 source 단계의 현재 quote 5초 cap, completed one-minute bar 90초 cap 또는 정규장 검증을
대체하지 않는다. source producer가 더 엄격한 point-in-time 조건을 먼저 통과한 뒤에도, projection이나
worker 지연으로 사용자에게 늦은 진입 알림이 전달되지 않게 하는 마지막 fail-closed 경계다. 이 값은
runtime 설정이나 agent 판단으로 완화할 수 없는 코드 상수다.

`watch`와 `actionable`만 현재 진입 판단에 직접 영향을 주므로 이 gate의 대상이다. `invalidation`, `exit`,
`incident`, `no_recommendation`, `research`, `daily_summary`는 뒤늦게 도착하더라도 결과와 감사 정보를
보존해야 하므로 기존 전달 수명주기를 유지한다.

## 원장 계약

- stale: claim 시각이 source event 시각보다 30초를 초과하면 suppress
- future: source event 시각이 claim 시각보다 미래면 suppress
- suppression: Telegram sender 호출 0회, acknowledgement 0건, terminal transition 1건
- restart/replay: terminal event는 다시 claim하지 않음
- timeout redrive: `telegram_timeout`만 허용하므로 `market_event_ineligible`은 redrive 불가

## 실제 운영과 수동 QA

- 설치된 stockagent plugin worker와 repository source를 동일하게 동기화했다.
- delivery LaunchAgent만 재시작했고 상태는 `running`이다.
- production 원장은 재시작 전후 events 3, attempts 7, acknowledgements 1, dead letters 2로 동일했다.
- 격리 fresh actionable: sender 1회, acknowledgement 1건, restart `idle`
- 격리 stale watch: sender 0회, dead letter 1건, restart `idle`
- 격리 future actionable: sender 0회, dead letter 1건, restart `idle`
- service `--help`: `run`, `provision`, `verify` 노출, credential 이름 미노출

## 검증

- 실패 우선: stale/future 네 경우가 기존 worker에서 sender를 호출해 4개 테스트 실패
- worker 집중 테스트: 11 passed
- Hermes 집중 회귀: 63 passed
- 전체 pytest: **3258 passed in 189.98s**
- Ruff: 통과
- basedpyright: 0 errors, 0 warnings, 0 notes
- no-excuse와 compileall, Hermes Python 3.11 py_compile: 통과

이 체크포인트는 과거 알림 방지 계약을 닫는다. 실제 현재 US/KR 추천, 무추천, 결과 전달과 연속 세션
soak 증거는 아직 별도 M1 운영 조건으로 남아 있다. 실제 금융 주문, Alpaca live endpoint, KIS·LS 주문
endpoint 또는 broker mutation은 사용하지 않았다.
