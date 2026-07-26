# 2026-07-26 인터랙티브 에이전트 Observatory 체크포인트

## 완료 범위

공개 read-only Observatory를 다음 네 작업 탭으로 재구성했다.

- `개요`: 시장 시계, forward 세션 품질, blocker, 실제 연구 기반
- `에이전트`: 여섯 에이전트 선택, 현재 실행·예약 상태, 명령 입력, 응답 원장
- `계좌·PnL`: 확정 Paper 원장의 평가액·손익·노출과 provenance
- `추천·근거`: immutable 추천과 source-backed evidence

에이전트 탭은 기본 화면이다. 명령은 최대 2,000자이며 `Ctrl+Enter` 또는 명시적 전송
버튼으로 접수한다. interaction은 `queued → running → completed|failed` 상태와 최대
8,000자의 redacted 응답을 Railway Postgres에 보존한다.

## 보안·비용 경계

- 공개 snapshot GET과 viewer WebSocket에는 접근키가 필요 없다.
- 명령 UI에는 비밀번호·접근키 필드가 없다.
- Mac publisher의 인증 WebSocket이 2분 유효 single-use ticket을 발급하고, ticket URL을
  연 기기에만 `Secure`, `HttpOnly`, `SameSite=Strict` operator cookie를 설정한다.
- 장기 operator secret은 page JavaScript, URL, localStorage, 명령 form과 로그에
  포함하지 않았다.
- 공개 viewer에게 command와 response를 broadcast하지 않는다.
- publisher는 argv 배열로 Hermes를 실행하며 shell 문자열을 만들지 않는다.
- 브라우저는 초기 GET 뒤 WebSocket 이벤트만 수신하고 publisher는 filesystem event만
  관측한다. 10초·15초 HTTP/DB polling과 자동 모델 호출은 없다.
- 명시적 명령 한 건당 Hermes 한 번만 실행한다. publisher 연결이 실행 중 끊기면 해당
  interaction을 `failed`로 닫고 자동 재전달하지 않는다. 아직 실행하지 않은 `queued`
  interaction만 재연결 publisher에 전달한다.
- 실제 자금 주문 경로와 Allocation Manager 권한은 추가하지 않았다.

## 배포

- design commit: `836db226ce083e3b8ed0245ee7949dbde36548e2`
- implementation commit: `2d98386`
- GitHub: `origin/main`에 push
- Railway service: `observatory`
- Railway production deployment:
  `a16716e6-9632-4049-bb8f-a5feb0e61eee`
- deployment status: `SUCCESS`
- image digest:
  `sha256:c45f433643114efb897bf03150629f04072ac1fb706284784223bc508893ce8a`
- URL: <https://observatory-production-3172.up.railway.app>

서버 operator token은 현재 사용자 Keychain 값을 stdout에 출력하지 않고 Railway
`DASHBOARD_OPERATOR_TOKEN` 변수로 전달했다. 기존 `DASHBOARD_INGEST_TOKEN`과
Postgres 경계는 유지했다.

배포 뒤 dashboard publisher launchd job만 PID `68352`에서 `11438`로 재시작했다.
KR/US market runner와 Hermes delivery service는 변경·중단·재시작하지 않았다.

## 운영 smoke

production에서 다음을 직접 확인했다.

- `/api/health`: `200`, `{"ok":true}`
- `/api/snapshot`: public `200`
- 무인증 `POST /api/agents/research/interactions`: `401`
- 네 탭과 command form: 존재
- password field: 없음
- publisher snapshot: 6 agents, fresh event delivery
- 일회용 ticket으로 Chrome operator session 연결
- `데이터 연구` agent에 파일·프로세스·외부 시스템 변경을 금지한 read-only 명령 1건 전송
- interaction: `running → completed`
- 응답: 최신 2026-07-24 세션의 추천 3건, `watch_cycle_failures:83`에 따른 forward 차단,
  research blocked, Paper account incomplete와 agent schedule을 redacted 한 문장으로 요약
- 중복 interaction과 자동 재시도: 없음

production 완료 화면은 로컬 QA evidence에 desktop `1265px`와 mobile `360px` 실제
브라우저 JPEG로 보존했다. 공개 URL의 현재 화면에는 completed 응답 원장이 표시되며
계좌 식별정보와 자격증명은 표시되지 않는다.

## 검증

- dashboard typecheck, Biome, build: 통과
- dashboard tests: `20 passed`
- Python target tests: `10 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- 전체 Python suite: `3674 passed`
- publisher CLI:
  - `--help`: 통과
  - relative/non-mode-600 credential bad input: non-zero 차단
  - local authenticated WebSocket happy path: 통과
- 실제 브라우저:
  - 1280/768/375에서 네 탭, 한 visible tabpanel, page overflow `0`
  - ArrowRight 이동, hash·focus·selected tab 동기화
  - locked/queued/running/completed/failed 상태
  - 모바일 workspace·agent reel `더 보기 →` cue
  - 한국어 단어 단위 wrapping과 selected `추천·근거` 탭 노출
- 현재 build 뒤 새로 촬영한 16개 화면을 두 독립 visual QA reviewer가 모두 `PASS`

로컬 QA server와 fake Hermes publisher는 종료했다. production dashboard publisher만
running 상태로 남겼다.
