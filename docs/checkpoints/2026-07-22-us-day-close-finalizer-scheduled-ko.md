# US Day close finalizer 배치 체크포인트

기준 시각: 2026-07-22 07:38 EDT
코드 기준: `d0f03cfcab89963f10f245496ef26eb500a6017b`

## 배치한 운영 경로

- launchd label: `ai.trading-agent.us-day-finalizer-20260722`
- 실행 시각: 2026-07-22 16:05 EDT
- source 대기 마감: 2026-07-22 16:15 EDT
- clean detached runtime:
  `/private/tmp/trading-agent-us-day-finalizer-20260722-d0f03cf`
- source:
  `outputs/live_sessions/20260722/paper_recommendations.sqlite3`
- terminal:
  `outputs/acceptance/us_day/sessions/2026-07-22.json`
- production delivery store: `outputs/hermes/delivery.sqlite3`

finalizer는 오늘 등록된 ORB shadow trial의 정확한 strategy version
`orb_5m_buffer5bp_volume1.5_v1-code-16ccc245d84f`를 사용한다. 이 값은 champion
승격 주장이 아니며 오늘 scheduled-session source identity다.

## fail-closed 계약

1. 16:05 EDT 전에는 아무 broker 요청도 하지 않고 대기한다.
2. ORB watch launchd job이 종료될 때까지 기다린다.
3. 오늘 watch DB가 없거나 16:15까지 안정화되지 않으면 terminal을 만들지 않고
   명시적 blocked 로그로 종료한다.
4. source DB를 clean runtime의 ignored output 경로에 mode `600`으로 복사한다.
5. runtime이 dirty하면 finalizer를 실행하지 않는다.
6. 기존 `finalize` CLI의 Paper GET/WSS recovery와 broker/shadow 대사만 실행한다.
7. arm, 주문 POST, 주문 DELETE, entry 또는 OCO mutation을 호출하지 않는다.

watch DB의 ORB 추천이 0건이면 `censored_no_setup`, 추천이 있지만 operating
terminal이 없으면 `natural_setup_without_terminal` blocked 결과를 생성한다.
실제 run terminal이 이미 있다면 별도 refresh 경로를 사용해야 하며 이 one-shot은
그 terminal을 덮어쓰는 용도로 사용하지 않는다.

## 배치 전 검증

- wrapper mode: `700`
- `zsh -n`: exit 0
- dry-run: exit 0, `broker_mutation=false`
- wrapper에서 arm, POST, DELETE, Alpaca order 문자열 없음
- detached runtime HEAD: exact `d0f03cf`, clean
- launchd state: running, one-shot wrapper 대기 중
- ORB watch, Hermes projector, Hermes delivery worker: 모두 running
- 실제 Paper mutation events: 0
- 실제 broker order events: 0

## 장후 확인 항목

- finalizer label이 종료되고 exit 상태가 기록됐는지 확인한다.
- terminal의 source hash, session ID, strategy version과 clean commit을 확인한다.
- terminal이 `censored`, `blocked`, 또는 기존 run 결과 refresh 중 하나인지 확인한다.
- open orders 0, positions 0, broker/shadow 대사 결과를 확인한다.
- Hermes delivery acknowledgement 또는 명시적 delivery incident를 확인한다.

이 체크포인트는 자동 결과 수집 경로의 배치 증거이며 M2 실제 Paper lifecycle 완료
증거가 아니다. 실제 Paper POST는 계속 0건이다.
