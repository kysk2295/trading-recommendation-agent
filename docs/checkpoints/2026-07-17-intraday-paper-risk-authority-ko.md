# Intraday Paper Risk Authority 체크포인트

- 날짜: 2026-07-17
- 범위: GET-only current-epoch safety planning의 active lane risk contract 통일
- broker mutation: 0건
- schema migration: 없음

## 변경

`plan_current_paper_safety`가 generic `DEFAULT_PAPER_RISK_CONFIG`를 암묵적으로 사용하지 않고 `INTRADAY_PILOT_PAPER_RISK_CONFIG`를 operating session에 명시적으로 전달한다.

현재 활성 한도는 다음과 같다.

- reference equity: USD 30,000
- maximum notional: USD 100
- maximum planned risk: USD 10
- maximum open positions: 1
- daily loss limit: USD 30
- minimum cost: 편도 20bp

일반 하드 상한은 코드 유효성의 바깥 경계일 뿐 pilot 운용 승인값이 아니다.

## 변경하지 않은 것

- Alpaca Paper endpoint와 arm 계약
- entry, OCO, cancel, flatten mutation 구현
- execution·lane·experiment schema
- 계좌 binding과 credential loader
- 실제 자금 또는 한국 주문 경로

## 검증

- TDD RED: active contract 경계 테스트가 generic 기본값과의 차이로 예상대로 1건 실패
- TDD GREEN: 같은 경계 테스트 `1 passed`
- focused Paper 운영 회귀: `29 passed`
- fixture-backed GET-only CLI happy path: `1 passed`
- 전체 회귀: `1678 passed`
- Ruff: `All checks passed!`
- basedpyright: `0 errors, 0 warnings, 0 notes`
- `git diff --check`: 종료코드 0
- 수동 `--help`: 종료코드 0, credential loading 없음
- 수동 missing-ledger 입력: 종료코드 1, database 생성 없음, mode `600` sanitized blocked report 생성
- 실제 broker POST·PATCH·DELETE: 0건
