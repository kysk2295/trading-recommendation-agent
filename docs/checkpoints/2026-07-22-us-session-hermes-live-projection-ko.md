# 미국장 세션 Hermes 실시간 투영 체크포인트

## 결과

- 미국장 세션에서 종목별 첫 Opportunity Manager `WATCH`만 전달한다.
- 같은 종목이 이후 스캔 주기에 다시 등장해도 중복 루트 알림을 만들지 않는다.
- Day Trading 신호는 해당 종목의 첫 세션 `WATCH`에 reply로 연결한다.
- 신호가 참조한 정확한 opportunity에 종목이 없으면 fail-closed로 투영을 거부한다.
- 동일 outbox를 재실행하면 기존 delivery identity를 재사용하며 새 행을 만들지 않는다.

## 운영 연결

- 제품 커밋: `f308f0607848d2a8dfe4f1a8c83fd92db94e10d6`
- 깨끗한 detached 런타임: `/private/tmp/trading-agent-projector-20260722-f308f06`
- launchd label: `ai.trading-agent.us-hermes-projection-20260722`
- 입력: `outputs/live_sessions/20260722/opportunities.v1.jsonl`, `trade-signals.v1.jsonl`
- 출력: 기존 `outputs/hermes/delivery.sqlite3`
- 종료 시각: 2026-07-22 16:15 America/New_York
- poll 계약: 입력 파일의 크기 또는 수정 시각이 바뀔 때만 투영하며 실패 시 5초 뒤 재시도한다.

2026-07-22 05:54 EDT 관찰에서 기존 ORB watch는 동일 PID로 실행 중이었고 별도
Hermes projector도 실행 중이었다. 아직 장전이라 두 outbox는 생성 전이었으며 projector
표준 출력, 표준 오류, 이벤트, 오류 로그는 모두 0바이트였다. 따라서 실제 Telegram
`WATCH` ACK는 정규장 outbox 생성 이후의 후속 운영 증거로 남긴다.

## 검증

- 집중 회귀: `47 passed`
- 전체 회귀: `3297 passed in 188.25s`
- 저장소 전체 Ruff: 통과
- 저장소 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- 실제 CLI:
  - `run_hermes_delivery.py --help`: `project-session` 노출
  - `project-session --help`: 필수 네 인자 노출
  - 잘못된 날짜: 종료 코드 2, 파일 생성 전 거부
  - fixture 첫 실행: 2건 삽입
  - 동일 fixture 재실행: 0건 삽입
  - Day 신호의 root delivery 연결 확인
  - delivery DB가 Paper recommendation DB와 같으면 종료 코드 2로 자격증명 접근 전에 거부

## 안전 경계

- projector에는 broker client, Paper arm, 주문 mutation 권한이 없다.
- 기존 ORB watch 파일과 프로세스를 변경하거나 재시작하지 않았다.
- 런타임 wrapper는 `0700`, 관련 로그는 `0600`이다.
- Alpaca Paper POST와 실제 자금 거래는 이 마일스톤에서 수행하지 않았다.
- 실제 `WATCH` 전달 및 ACK가 관찰되기 전까지 미국 Day Trading 전달 마일스톤을 완료로
  승격하지 않는다.
