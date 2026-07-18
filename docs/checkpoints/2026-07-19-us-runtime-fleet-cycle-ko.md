# US runtime fleet 운영 사이클 체크포인트

## 완성 범위

- scanner raw Opportunity, verified broad snapshot, data foundation을 같은 projection 세대에서 재생한다.
- desired candidate마다 content-addressed historical volume profile을 검증한다.
- profile 목표일과 현재 정규장 날짜, `through_minute`와 현재 완료 분 수를 정확히 일치시킨다.
- 종목별 독립 Alpaca SIP GET-only owner, M4.4 evidence gate, append-only fleet audit을 한 사이클로 연결한다.
- 폐장, stale/expired Opportunity, candidate 축소, profile 누락·변조·분 불일치는 credential과 HTTP 전에 차단한다.

## 검증

- scanner/profile/fleet library E2E: 두 owner READY, gate READY, audit replay
- CLI fixture E2E: FIXT 35분, Alpaca data GET 1건, mode-600 audit READY
- CLI 수동 QA: `--help`, malformed `--profile`, 폐장/누락 scanner blocked
- account/order endpoint, POST/DELETE, 외부 금융 mutation: 0건

## 남은 운영 게이트

- 다음 실제 미국 정규장에서 current-minute read-only GET smoke
- subscription minimum-residency와 eviction-cooldown의 durable process-restart state
- 장중 반복 supervisor, rate/backoff/soak 관측과 종료 리포트
