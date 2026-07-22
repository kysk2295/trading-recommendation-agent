# 미국장 세션 terminal 전달 체크포인트

## 결과

- 정규장 종료 전에는 session terminal delivery를 만들 수 없다.
- 세션에 deduplicated WATCH가 있지만 Day signal이 없으면 `NO_RECOMMENDATION`을 전달한다.
- 하나 이상의 Day signal이 있으면 종목과 신호 개수만 담은 `DAILY_SUMMARY`를 전달한다.
- summary는 성과·수익·체결 결과가 아니라 당일 publication count라는 문구를 명시한다.
- terminal artifact를 session reconciliation에 포함하면 WATCH, reply와 terminal이 모두 ACK돼야
  최종 `complete=true`가 된다.

## 결정적 재시작

terminal 효과 시각은 CLI 실행 시각이 아니라 해당 NYSE session의 공식 close로 고정한다. CLI의
현재 시각은 close 이후이며 24시간 recovery window 안인지 확인하는 causal gate로만 사용한다.
따라서 event append 뒤 artifact publication 전에 재시작해도 같은 source는 같은 event가 되어
`inserted=0` replay로 끝난다.

같은 session close에 이미 terminal이 있으면 완전히 같은 event만 허용한다. terminal 이후 outbox가
변해 source digest가 달라지면 두 번째 terminal을 만들지 않고 fail-closed한다.

## 검증

- 집중 terminal/reconciliation 회귀: `14 passed`
- 전체 회귀: `3306 passed in 186.11s`
- 저장소 전체 Ruff: 통과
- 저장소 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- compileall: 통과
- 변경 파일 OMO no-excuse: 무위반
- 실제 CLI help와 잘못된 날짜 종료 코드 2 확인
- 실제 전일 recovery-window E2E:
  - session projection 1건
  - no-recommendation terminal 1건
  - ACK 2/2, pending 0, complete true
  - terminal/reconciliation artifact mode `0600`
- 실제 재시작 E2E:
  - 첫 terminal `inserted=1`
  - 다른 실행 시각 replay `inserted=0`
  - 늦은 signal source 변경 종료 코드 2
  - durable terminal event 총 1건

## 당일 운영 연결

- 기능 커밋: `39f429a`
- deterministic replay 수정: `eca77921abd53bd4e44781401ce2d3ff9a369134`
- clean runtime: `/private/tmp/trading-agent-projector-20260722-eca7792`
- launchd label: `ai.trading-agent.us-hermes-projection-20260722`
- wrapper: `outputs/live_sessions/20260722/hermes_projection_runner_eca7792.zsh`
- terminal 시각: 2026-07-22 16:05 EDT 이후
- 종료 시각: 2026-07-22 16:15 EDT
- terminal artifact:
  `outputs/acceptance/hermes/sessions/2026-07-22-terminal.json`
- reconciliation report:
  `outputs/acceptance/hermes/sessions/2026-07-22-delivery-reconciliation.json`

장중에는 outbox signature 변경마다 WATCH/reply를 project하고 ACK를 대사한다. 16:05 이후 source가
안정된 signature에서 terminal을 생성하고 reconciliation 범위에 terminal artifact를 추가한다.
terminal ACK까지 확인되면 해당 signature의 대사를 멈춘다.

2026-07-22 06시대 EDT 관찰에서 기존 ORB watch는 기존 PID로 계속 실행 중이었고 새 clean-SHA
projector도 별도 PID로 실행 중이었다. 아직 장전이라 opportunity, signal, terminal과 reconciliation
artifact는 모두 생성 전이었다.

## 안전 경계

- broker client, credential, Paper arm과 주문 mutation을 사용하지 않는다.
- broker flatness, 체결 성과 또는 전략 수익을 terminal summary에서 추정하지 않는다.
- 기존 ORB watch와 Hermes dispatcher를 재시작하지 않았다.
- 실제 당일 Telegram terminal ACK 전까지 오늘의 운영 terminal은 완료로 주장하지 않는다.
- Allocation Manager는 독립 executable champion 두 개 전까지 비활성이다.
