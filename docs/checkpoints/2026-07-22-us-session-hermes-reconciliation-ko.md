# 미국장 세션 Hermes 전달 대사 체크포인트

## 결과

- 세션 outbox에서 종목별 첫 Opportunity Manager WATCH와 Day Trading reply의 기대 delivery를
  deterministic하게 다시 만든다.
- production delivery store의 event payload, root/reply 계보와 기대 event가 완전히 같을 때만
  projected로 인정한다.
- acknowledgement와 dead letter를 별도로 대사하며 모든 기대 delivery가 ACK된 경우에만
  `complete=true`가 된다.
- platform message ID, Telegram owner/channel 식별자와 credential은 보고서와 CLI 출력에 쓰지 않는다.
- 보고서는 atomic private writer로 `0600`에 저장된다.

## 제품 계약

`run_hermes_delivery.py reconcile-session`은 다음 입력만 받는다.

- Hermes delivery SQLite
- opportunity outbox
- trade-signal outbox
- New York session date
- private reconciliation report 경로

같은 delivery ID의 payload나 계보가 다르거나, source가 비었거나, source event가 대사 시각보다
미래이거나, SQLite가 손상됐으면 redacted 종료 코드 2로 fail-closed한다. 대사 기능은 delivery를
추가하거나 Telegram을 전송하지 않는 query-only 경계다.

## 검증

- 집중 회귀: `19 passed`
- 전체 회귀: `3301 passed in 186.50s`
- 저장소 전체 Ruff: 통과
- 저장소 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- compileall: 통과
- 변경 파일 OMO no-excuse 검사: 무위반
- 실제 CLI root/subcommand help: 통과
- 잘못된 session date: argparse 종료 코드 2
- 손상 SQLite: traceback 없이 redacted 종료 코드 2
- 실제 프로세스 fixture: expected 2, acknowledged 2, pending 0, complete true
- fixture report mode: `0600`

## 당일 운영 연결

- 기능 커밋: `86914b383249866d7442066be6dd59d0b2818afa`
- clean detached runtime: `/private/tmp/trading-agent-projector-20260722-86914b3`
- launchd label: `ai.trading-agent.us-hermes-projection-20260722`
- wrapper: `outputs/live_sessions/20260722/hermes_projection_runner_86914b3.zsh`
- reconciliation report:
  `outputs/acceptance/hermes/sessions/2026-07-22-delivery-reconciliation.json`

2026-07-22 06시대 EDT 관찰에서 기존 ORB watch PID는 교체 전과 같았고 새 projector는 별도
PID로 실행 중이었다. 아직 장전이라 opportunity와 signal outbox는 생성 전이고 reconciliation
report도 `awaiting source` 상태다. outbox signature가 바뀌면 project를 먼저 실행하고, 해당
signature의 모든 ACK가 확인될 때까지 5초마다 report를 갱신한다. 새로운 signal이 append되면
새 signature로 다시 대사한다.

## 안전 경계

- Alpaca/KIS/LS broker client와 credential을 import하거나 읽지 않는다.
- Paper arm, 주문 승인, 주문 mutation과 위험예산 변경 권한이 없다.
- 기존 ORB watch 프로세스와 파일은 변경하거나 재시작하지 않았다.
- 실제 Telegram session WATCH ACK가 생기기 전까지 오늘 운영 대사는 완료로 주장하지 않는다.
- Allocation Manager는 두 독립 executable champion이 생기기 전까지 계속 비활성이다.
