# US bounded minute supervisor 계약 체크포인트

## 계약

- 정규장 안에서만 operation을 시작한다.
- 최대 390회, interval 1~3600초로 제한한다.
- 한 cycle block 뒤에도 다음 cycle을 계속한다.
- 16:00 ET에는 operation 전에 종료한다.
- shutdown 요청은 새 clock·operation과 다음 interval wait 전에 확인한다.
- process restart는 append-only history를 재생해 같은 뉴욕 거래일의 다음 cycle index와 남은 budget을 복원한다.
- 거래일별 index 중복·누락, 시간 역행과 exact duplicate append는 fail-closed 한다.
- 모든 READY/blocked attempt를 fleet audit과 별도인 append-only store에 기록한다.
- record는 시작/종료 시각, 순번, 상태/reason, optional fleet cycle ID와 deterministic hash를 포함한다.
- store는 mode 600, current-user regular file, no-symlink, single hard link와 single writer를 요구한다.
- table 하나와 update/delete 차단 trigger 둘의 exact schema object 집합을 연결마다 검사한다.

## 검증

- blocked → 다음 분 READY 회복
- 15:59 실행 → 16:00 추가 operation 0건
- 완료 cycle 뒤 shutdown → wait와 추가 operation 0건
- 같은 거래일 process restart → index 2만 실행, budget 소진 뒤 operation 0건
- 중복된 같은 거래일 index history 차단
- append-only trigger 삭제와 hard-link alias 차단
- payload, public mode, symlink 변조 fail-closed
- credential/account/order/mutation 기능 0건

## 다음 단계

- 실제 정규장 장기 soak에서 process restart 전후 마지막 attempt와 provider 상태 대사
