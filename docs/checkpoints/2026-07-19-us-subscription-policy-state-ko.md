# US subscription policy state 체크포인트

## 목적

US bounded subscription 정책의 minimum residency와 eviction cooldown을 프로세스 재시작 뒤에도 보존한다.

## 계약

- state는 provider 연결 사실이 아니라 policy intent만 기록한다.
- exact policy decision payload의 SHA-256을 state에 결합한다.
- desired instrument의 최초 `subscribed_at`을 유지한다.
- 퇴출 cooldown은 `eligible_after`까지 유지하고 만료 뒤 제거한다.
- mode-600 current-user regular SQLite, no-symlink, append-only trigger, `BEGIN IMMEDIATE` single writer를 요구한다.
- exact retry만 idempotent하며 payload SHA와 state ID를 reader가 다시 계산한다.

## 검증

- 30초 뒤 재시작: minimum residency incumbent 유지
- 3분 뒤 퇴출 및 재시작: 5분 cooldown 재진입 차단
- payload, 파일 mode, symlink 변조: fail-closed
- CLI READY cycle: state와 fleet audit을 각각 보존
- account/order endpoint와 외부 mutation: 0건
