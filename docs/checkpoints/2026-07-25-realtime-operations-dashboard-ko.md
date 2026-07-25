# 실시간 운영 대시보드 체크포인트

기준일: 2026-07-25 KST

## 완료한 수직축

- Railway `observatory` 서비스와 managed Postgres를 생성하고 HTTPS 도메인에 배포했다.
- 로컬 immutable 산출물과 launchd 상태를 strict public schema로 축약하는 publisher를 구현했다.
- 한국·미국 시장 시계, forward 품질·차단 사유, 런타임 fleet, 추천, source-backed 신호, actual research foundation을 한 화면에 표시한다.
- 조회 토큰과 ingest 토큰을 분리하고 constant-time 비교, no-store, CSP, payload 상한과 strict Zod 검증을 적용했다.
- 계좌 ID·fingerprint, 자격증명, 요청 헤더, 로컬 경로, 원시 provider 응답은 snapshot 경계를 통과하지 못한다.
- `ai.trading-agent.dashboard-publisher` launchd agent가 mode-600 설정을 읽어 15초마다 snapshot을 갱신한다.

## 운영 주소와 로컬 설정

- 대시보드: <https://observatory-production-3172.up.railway.app>
- publisher 설정: `~/.config/trading-agent/dashboard.env`
- 사용자 조회 키: `~/.config/trading-agent/dashboard-view-token.txt`
- launchd 정의: `~/Library/LaunchAgents/ai.trading-agent.dashboard-publisher.plist`

모든 설정 파일은 현재 사용자 소유 mode 600이다. 문서에는 토큰 값을 기록하지 않는다.

## 검증

- Railway Docker build와 TypeScript strict build 통과
- 원격 `/api/health` 성공
- 실제 redacted snapshot ingest 및 authenticated read 성공
- 원격 화면에서 session `2026-07-24`, 추천 3건, 신호 3건, agent 6개를 확인
- launchd publisher `running`, stderr 0행, 15초 이내 freshness 확인
- 1440px, 768px, 390px, 320px 화면 캡처와 axe 감사 수행
- dashboard와 showcase 모두 axe violations 0, incomplete 0
- Taste Skill의 anti-slop 원칙을 운영 화면에 맞게 적용하고 별도 `dashboard/DESIGN.md` 계약과 primitive showcase를 유지

## 의미

이 화면은 paper recommendation과 연구 운영 상태를 관측하기 위한 read-only 관제면이다. `BLOCKED`는 실패를 숨기지 않고 현재 clean actual forward 품질이 부족함을 뜻한다. 대시보드는 주문권한, champion 전이 또는 Allocation Manager 권한을 추가하지 않는다.
