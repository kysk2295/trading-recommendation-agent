# Intraday Code-version Bootstrap 체크포인트

- 실행일: 2026-07-24 KST
- clean main SHA: `701b354e2fbf15c1dbffaa3a5c1a58ddef92ec1e`
- 대상: 다음 NYSE 정규장 전 local-only global experiment ledger

새 systematic Reviewer code commit 뒤 우선순위 11의 사전등록 절차를 실행했다. 기존 exact intraday 가설 네 개는 재사용하고 current commit에 결속된 전략 버전, authority binding과 `experimental_shadow` lifecycle event를 각각 네 건 append했다.

첫 실행:

- hypothesis 신규/재사용: `0/4`
- strategy version 신규/재사용: `4/0`
- strategy authority 신규/재사용: `4/0`
- lifecycle event 신규/재사용: `4/0`

동일 SHA exact replay:

- hypothesis 신규/재사용: `0/4`
- strategy version 신규/재사용: `0/4`
- strategy authority 신규/재사용: `0/4`
- lifecycle event 신규/재사용: `0/4`

query-only 재검증에서 global legacy strategy version/lifecycle은 `20/20`, current SHA version/lifecycle은 `4/4`였다. lane registry, experiment ledger와 두 report는 mode `600`이다. credential, provider, account, broker와 order mutation은 모두 0건이다. 이 registration은 다음 세션 사전등록 identity일 뿐 Paper champion, trial 성과 또는 주문 권한이 아니다.

동일 코드 상태의 검증 기준선:

- 전체 pytest: `3575 passed in 221.67s`
- 전체 Ruff: 통과
- 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
