# Intraday Code-Version Rollover 체크포인트

날짜: 2026-07-17 KST

상태: **새 intraday 코드 commit을 별도 append-only strategy version으로 등록하고 ORB shadow trial evidence를 그 identity에 고정**

## 문제와 결정

기존 global experiment ledger는 `strategy_version`에 parameter-set 이름만 사용하고 `code_version`을 별도 필드로 저장했다. 코드가 바뀐 뒤 bootstrap을 재실행하면 같은 immutable version과 충돌했고, 새 session의 preregistration이 fail-closed 됐다.

기존 row를 update하거나 runtime code equality를 완화하지 않는다. `strategy_version_identity`는 base parameter-set version과 exact code version SHA-256 digest를 결합한다. 같은 코드에는 같은 identity, 다른 코드에는 다른 identity가 생긴다.

## 구현 경계

- bootstrap은 기존 exact hypothesis의 최초 recorded-at을 재사용하고, 새 code identity의 네 strategy version과 sequence-one lifecycle event만 append한다.
- daily record와 adaptive aggregation은 code-coupled identity로만 같은 표본을 누적한다.
- lane snapshot, independent Reviewer, ORB register/start/finalize/fail, Lifecycle Controller는 record 또는 review identity를 실제 ledger version과 다시 대사한다.
- 같은 NYSE session에 ORB version이 둘 이상이면 trial은 차단된다. 이미 등록된 legacy static version은 재작성하지 않는다.
- lifecycle의 suspension 권고·champion·allocation·Paper order 권한은 변경하지 않았다.

## 정규장 운영 확인

- Alpaca Paper GET-only preflight: 빈 주문·포지션, 로컬 execution ledger 대사 통과.
- Alpaca Paper readiness: Paper endpoint 고정, WSS 인증·구독·Pong 및 REST·ledger·portfolio 대사 통과. 주문 POST/DELETE는 비활성 상태로 유지.
- NYSE 정규장 중 KIS read-only ORB 관찰을 실행했다. 일부 개별 분봉 HTTP 500은 failure audit으로 보존했으며, 누락을 성공으로 처리하거나 추천·주문으로 보정하지 않았다.
- 정규장 뒤 새 ORB trial registration은 pre-open 규칙에 따라 차단됐다. 과거 시점 preregistration을 만들지 않았다.
- 실제 Alpaca Paper POST/DELETE: 0건.

## 검증

- focused identity, daily record, lane snapshot/Reviewer, bootstrap, ORB trial/CLI, Lifecycle Controller/CLI 통과
- 전체 회귀: `1676 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`

## 다음 정규장 전 절차

1. clean `main` checkout에서 local-only `run_experiment_ledger_bootstrap.py`를 current `git rev-parse HEAD`로 실행한다.
2. 새 code-coupled version이 append 또는 exact replay됐는지 redacted report로 확인한다.
3. NYSE open 전에만 lane 경로와 experiment ledger를 가진 ORB watch를 시작한다.
4. 현재 후보·current completed bar·runtime readiness·명시적 Paper arm이 모두 맞을 때만 별도 1주 Paper smoke를 검토한다. 조건 하나라도 빠지면 POST/DELETE를 호출하지 않는다.
