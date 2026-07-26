# 실시간 운영 대시보드 체크포인트

기준일: 2026-07-25 KST

## 완료한 수직축

- Railway `observatory` 서비스와 managed Postgres를 생성하고 HTTPS 도메인에 배포했다.
- 로컬 immutable 산출물과 launchd 상태를 strict public schema로 축약하는 publisher를 구현했다.
- 한국·미국 시장 시계, forward 품질·차단 사유, 런타임 fleet, 추천, source-backed 신호, actual research foundation을 한 화면에 표시한다.
- 공개 조회와 private ingest를 분리하고 ingest constant-time 비교, no-store, CSP, payload 상한과 strict Zod 검증을 적용했다.
- 확정 lane daily ledger의 평가기준액·일간/실현/미실현 PnL·계획 리스크·포지션/주문 수를 표시한다. 계좌 ID·fingerprint, 자격증명, 요청 헤더, 로컬 경로, 원시 provider 응답은 snapshot 경계를 통과하지 못한다.
- 초기 `ai.trading-agent.dashboard-publisher` launchd agent는 mode-600 설정을 읽어 15초마다 snapshot을 갱신했다. 이 주기적 HTTP 구현은 2026-07-26 비용 감사에서 중지됐으며 아래 이벤트 기반 계약으로 대체됐다.

## 운영 주소와 로컬 설정

- 대시보드: <https://observatory-production-3172.up.railway.app>
- publisher 설정: `~/.config/trading-agent/dashboard.env`
- launchd 정의: `~/Library/LaunchAgents/ai.trading-agent.dashboard-publisher.plist`

모든 설정 파일은 현재 사용자 소유 mode 600이다. 문서에는 토큰 값을 기록하지 않는다.

## 검증

- Railway Docker build와 TypeScript strict build 통과
- 원격 `/api/health` 성공
- 실제 redacted snapshot ingest 및 authenticated read 성공
- 원격 화면에서 session `2026-07-24`, 추천 3건, 신호 3건, agent 6개를 확인
- 초기 launchd publisher `running`, stderr 0행, 15초 이내 freshness 확인
- 1440px, 768px, 390px, 320px 화면 캡처와 axe 감사 수행
- dashboard와 showcase 모두 axe violations 0, incomplete 0
- Taste Skill의 anti-slop 원칙을 운영 화면에 맞게 적용하고 별도 `dashboard/DESIGN.md` 계약과 primitive showcase를 유지

## 의미

이 화면은 paper recommendation과 연구 운영 상태를 관측하기 위한 read-only 관제면이다. `BLOCKED`는 실패를 숨기지 않고 현재 clean actual forward 품질이 부족함을 뜻한다. 대시보드는 주문권한, champion 전이 또는 Allocation Manager 권한을 추가하지 않는다.

## 2026-07-26 비용 감사와 대체 계약

- 브라우저 10초 snapshot GET과 맥미니 15초 snapshot POST가 유휴 상태에서도 Railway와 Postgres를 깨우는 과금 위험임을 확인했다.
- 주기적 publisher를 즉시 unload했고 한국·미국 시장 에이전트와 예약 작업은 변경하지 않았다.
- 대체 구현은 브라우저 public WebSocket 1개와 Bearer 인증 publisher WebSocket 1개만 유지한다.
- 로컬 snapshot은 연결 시 한 번, `live_sessions`, `experiment_control`, 또는 `lane_control` 산출물의 파일시스템 변경 시에만 전송한다.
- 유휴 HTTP/DB poll과 자동 유료 AI 호출은 각각 0회다. 연결 장애 때만 bounded exponential reconnect를 수행한다.
- 공개 열람은 계속 무인증이다. 운영 제어·질문·명령 API와 pairing UI는 제공하지 않는다.

### 운영 검증

- 구현 commit: `cce1967415a0cd6c508392fb64a691b7ff3145dd`
- Railway deployment: `1b1f624d-e309-4dda-ab95-f7f76a8ff5c0`, `SUCCESS`
- Railway image digest: `sha256:9d1bccda574d37b4b89df48c037b7020d559838d4f6a108adab5794485883e79`
- production health `200`, 폐기한 operator·interaction API `404`, 무인증 publisher 연결 `401`
- 실제 production snapshot은 session `2026-07-24`, agent 6개, recommendation 3개와 USD 평가기준액 `100000`, 일간·실현·미실현 PnL `0`, 포지션·미체결 주문 `0`을 반환했다.
- 계좌 상태는 `incomplete`다. 원장이 확정한 값을 그대로 공개하며, 누락을 추정하거나 broker cash·buying power를 만들어내지 않는다.
- 운영 브라우저에서 계좌 패널을 확인했고 axe violations 0, incomplete 0이었다. 네트워크 기록을 비운 뒤 12초 유휴 구간의 요청은 0건이었다.
- launchd publisher PID가 동일한 채 20초 동안 `running`을 유지했으며 stdout/stderr 크기·mtime과 production snapshot timestamp가 모두 변하지 않았다.
- Railway의 사용하지 않는 조회·운영자 토큰은 제거했다. `DASHBOARD_INGEST_TOKEN`만 publisher 쓰기 경계에 남는다.
