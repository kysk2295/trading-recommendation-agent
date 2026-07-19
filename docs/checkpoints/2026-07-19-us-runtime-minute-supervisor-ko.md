# US bounded minute supervisor 계약 체크포인트

## 계약

- 정규장 안에서만 operation을 시작한다.
- 최대 390회, interval 1~3600초로 제한한다.
- 한 cycle block 뒤에도 다음 cycle을 계속한다.
- 16:00 ET에는 operation 전에 종료한다.
- shutdown 요청은 새 clock·operation과 다음 interval wait 전에 확인한다.
- 모든 READY/blocked attempt를 fleet audit과 별도인 append-only store에 기록한다.
- record는 시작/종료 시각, 순번, 상태/reason, optional fleet cycle ID와 deterministic hash를 포함한다.
- store는 mode 600, current-user regular file, no-symlink, single writer를 요구한다.

## 검증

- blocked → 다음 분 READY 회복
- 15:59 실행 → 16:00 추가 operation 0건
- 완료 cycle 뒤 shutdown → wait와 추가 operation 0건
- payload, public mode, symlink 변조 fail-closed
- credential/account/order/mutation 기능 0건

## 다음 단계

- 실제 정규장 장기 soak에서 종료 전 마지막 attempt와 provider 상태 대사
