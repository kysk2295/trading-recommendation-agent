# US runtime fleet supervisor CLI 체크포인트

## 완성 범위

- 매 attempt scanner bundle과 durable policy state를 다시 읽는다.
- 자동 20일 profile materialization과 current-minute fleet를 실행한다.
- 현재 evaluated time과 정확히 일치하는 fleet audit만 supervisor READY에 연결한다.
- stale/preflight/provider block은 이전 audit을 재사용하지 않고 다음 분으로 격리한다.
- 정규장 종료와 최대 390회/interval 제한은 supervisor 계약을 따른다.
- SIGINT/SIGTERM은 process-local event를 설정해 interval wait를 깨우고 다음 cycle 전에 종료한다.
- 정상 signal 종료는 기존 handler를 복원하고 aggregate `stopped` private report를 남긴다.

## 검증

- 1-cycle 자동 CLI: historical GET 20 + current GET 1, READY
- 2-cycle soak: fresh scanner 갱신, historical cache 재사용, 총 GET 22, READY 2
- 폐장 시작: credential read, fleet/supervisor DB 생성 0건
- shutdown 시작: secret/scanner/fleet/supervisor DB 접근 0건, exit 0
- 실제 SIGTERM handler 설정·event 전달·handler 복원
- focused 11 passed, full 2389 passed, Ruff와 basedpyright 0/0
- `--help`, bounded 인자, private report 확인
- account/order endpoint와 mutation 0건

## 운영 전 남은 게이트

- KIS watch와 supervisor를 같은 정규장 세션에서 병행하는 actual read-only smoke
- 장시간 provider rate/error soak와 실제 signal 종료 리포트 검토
