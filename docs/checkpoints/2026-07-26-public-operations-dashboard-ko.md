# 공개 열람 운영 대시보드 체크포인트

기준일: 2026-07-26 KST

## 변경

- 운영 URL을 열면 별도 접근키 입력 없이 Observatory 화면과 최신 redacted snapshot을 즉시 표시한다.
- `GET /api/snapshot`은 공개 읽기 전용으로 전환했다.
- `POST /api/ingest`는 기존의 별도 Bearer token, constant-time 비교, payload 상한, strict Zod schema를 유지한다.
- 브라우저 session storage의 조회 token과 접근 form, 잠금 action, 관련 style·showcase 상태를 제거했다.
- 계좌 식별자, 자격증명, 요청 header, 로컬 경로, 원시 provider payload는 계속 snapshot schema 밖에서 거절한다.

## 검증

- TDD RED: 인증 header 없는 snapshot GET이 기존 구현에서 `401`을 반환함을 확인
- GREEN: dashboard API `5 passed`
- TypeScript `tsc --noEmit`, Biome, production client build, no-excuse 검사 통과
- Python publisher 관련 `6 passed`, Ruff, basedpyright `0 errors, 0 warnings`
- 로컬 실제 HTTP에서 인증된 ingest `202`, 키 없는 snapshot GET `200`, 인증 없는 ingest `401`
- 1280px, 768px, 375px 실제 화면에서 접근 form 없이 dashboard가 즉시 표시됨
- 독립 design-system/functional gate와 visual/CJK gate 모두 `PASS`, blocker 없음

## 보안 경계

공개되는 것은 strict redacted 최신 운영 snapshot 한 건뿐이다. 이 변경은 publisher 쓰기 권한, broker 계좌, 주문, champion 전이 또는 Allocation Manager 권한을 공개하지 않는다.
