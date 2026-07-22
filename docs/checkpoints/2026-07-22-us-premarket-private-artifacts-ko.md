# US premarket private artifact 체크포인트

날짜: 2026-07-22

## 실제 조회 결과

- KIS 미국시장 ranking과 NYSE halt feed를 GET-only로 조회했다.
- 최초 production snapshot은 ranking 341행, 위험 통과 8개, 선정 8개, ranking 실패 0개였다.
- 계좌·잔고·주문 endpoint와 Alpaca live endpoint는 사용하지 않았다.
- 오늘 ORB `shadow_forward` trial은 1건, trial event는 0건을 유지했다.
- Paper execution 원장의 order intent, broker order event, mutation intent, mutation event는 모두 0건이다.

## 발견과 교정

- `premarket_risk_screen.csv`, `premarket_ranking_snapshots.csv`,
  `premarket_ranking_request_coverage.csv`가 프로세스 umask를 따라 mode `644`로 생성되는 결함을 실제 수직에서 발견했다.
- 공통 private append 경계가 신규 파일을 mode `600`으로 생성하고 기존 파일도 append 전에
  mode `600`으로 교정하도록 변경했다.
- legacy CSV migration 임시 파일도 replace 전에 mode `600`으로 제한한다.
- 기존 production 세 파일은 SHA-256 내용이 바뀌지 않은 상태에서 메타데이터만 mode `600`으로 교정했다.

## 검증

- 권한 회귀 TDD: 변경 전 신규·기존 파일 assertion 3건이 정확히 `644 != 600`으로 실패했다.
- 집중 테스트: 12 passed
- 전체 테스트: 3289 passed
- Ruff: 통과
- basedpyright: 0 errors, 0 warnings
- Python no-excuse 검사: 통과
- CLI `--help`: exit 0
- CLI `--top 0`: exit 2, provider 접근 전에 입력 거절
- 별도 임시 디렉터리 GET-only happy path: 원시 357행, 위험 통과 8개, 선정 1개,
  ranking 실패 0개, 세 CSV 모두 mode `600`

## 다음 운영 단계

- Paper arm은 계속 닫혀 있다.
- 정규장에서는 어제 등록돼 오늘 effective인 clean runtime으로 preregistered ORB trial만 시작한다.
- 자연 setup이 없으면 threshold를 바꾸지 않고 장후 censored no-setup으로 확정한다.
