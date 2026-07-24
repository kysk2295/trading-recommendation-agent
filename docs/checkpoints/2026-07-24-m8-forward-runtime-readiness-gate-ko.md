# M8 strict forward runtime readiness 게이트

- 기록 시각: 2026-07-24 13:22 KST
- 구현 커밋: `453f57b703bce73490ac5adc2e37e5e539efb031`
- 대상 세션: 2026-07-27 미국 정규장
- 주문·위험예산·전략 승격 권한: 없음

## 완료한 제품 경계

예약된 strict forward 실행은 다음 계약을 모두 만족할 때만 시작한다.

1. frozen runtime이 현재 사용자 소유의 mode 700 디렉터리이고 symlink가 아니다.
2. runtime HEAD가 예약에 기록된 40자리 SHA와 정확히 같고 working tree가 깨끗하다.
3. KIS 5xx 복구 커밋과 EOD 마지막 분봉 semantic-lag 복구 커밋이 runtime HEAD의 조상이다.
4. 현재 intraday lane manifest와 네 experiment scope가 immutable lane registry에 정확히 등록되어 있다.
5. runtime HEAD에 대응하는 네 strategy version과 Alpaca Paper authority가 experiment ledger에 존재한다.
6. 네 lifecycle이 대상 세션 날짜에 `experimental_shadow`로 활성이다.
7. execution database가 현재 schema로 초기화되어 있다.
8. 390 cycle, 60초 cadence, KIS server 4회, EOD semantic 3회 계약이 정확하다.

검증 실패는 blocker 이름만 mode 600 보고서에 기록하며 경로, 자격증명, 계좌 식별자는 기록하지 않는다.
게이트 자체는 broker mutation을 만들지 않는다.

## 실제 예약 결합 증거

- private frozen runtime HEAD: `453f57b703bce73490ac5adc2e37e5e539efb031`
- 필수 조상:
  - `3c476d5390b39c7db252216f2191c6d0d4b8b6fb`
  - `581eebc08965a647c9b84374e9fade98ccc8a75a`
- 2026-07-27 experiment ledger:
  - strategy version 신규/재사용: 4/0
  - authority 신규/재사용: 4/0
  - lifecycle event 신규/재사용: 4/0
  - replay 신규: 0
  - effective session date: 2026-07-27
  - state: `experimental_shadow`
- 실제 readiness 결과: `ready`
- blocker 수: 0
- 보고서 mode: 600
- 예약 wrapper PID, runs, receipt/claim, stdout/stderr 상태는 payload 교체 전후 불변

## 검증

- 타깃 테스트: 5 passed
- 전체 pytest: 3633 passed
- Ruff: 통과
- basedpyright: 0 errors, 0 warnings
- CLI `--help`: 통과
- 잘못된 SHA: exit 2, 보고서 미생성
- 실제 원장·frozen runtime happy path: exit 0, `ready`
- payload `zsh -n`: 통과
- origin/main과 구현 HEAD 일치 확인

이 게이트는 실제 세션 품질을 대신 판정하지 않는다. 실행 후 ranking, watch, candidate,
retry, post-session metric 완전성은 기존 strict 품질 게이트가 그대로 판정한다.
