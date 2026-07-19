# KR theme day lifecycle Controller 체크포인트

## Multi-market lifecycle 원장

global experiment ledger schema v6는 `multi_market_lifecycle_events`를 추가한다. event는 exact multi-market strategy version과 `StrategyLaneRef`를 부모로 두고, market-local decision session과 다음 open effective session, 공식 calendar snapshot, canonical evidence와 previous-key chain을 보존한다.

v1~v5 migration은 기존 payload와 key를 재작성하지 않는다. table, index와 UPDATE/DELETE 차단 trigger를 한 transaction에서 추가하며 Reader와 Writer는 current schema exact-set과 모든 parent·sequence·state·시간·운영모드 계약을 다시 검증한다. shadow version은 `EXPERIMENTAL_PAPER`와 `PAPER_CHAMPION`으로 갈 수 없다.

## KR day 정책 v1

- exact lane: `kr_equities/day_trading/theme_leader_vwap_reclaim`
- 입력: code-coupled shadow version, persisted Reviewer v1 event, exact global trial chain, private entry·exit·terminal evidence, decision session의 공식 KIS calendar snapshot
- 최초 event: 다음 open session `EXPERIMENTAL_SHADOW`
- `continue_collection`: 상태 변경 없음
- censored/failed `data_quality_review`: eligible active state를 다음 open session `SUSPENDED`
- 20 forward sessions·30 completed signals `comparison_ready`: `EXPERIMENTAL_SHADOW`에서 `CHALLENGER`까지만 전이
- independent comparator·multiple-testing evidence가 없으므로 `SHADOW_CHAMPION`은 항상 차단

Reviewer는 상태를 직접 바꾸지 않는다. Controller가 Reviewer와 모든 원천 evidence를 다시 재생한 뒤 별도 lifecycle Writer로 전이를 append한다. same policy/session exact replay는 기존 event를 반환하며 미래 시각, 다른 ledger, 변형 review, stale/wrong-date calendar와 future-effective parent는 append 전에 차단한다.

## CLI와 안전 경계

`run_kr_theme_day_lifecycle.py`는 여섯 local immutable store와 strategy version, as-of session, private output path만 받는다. credential, provider, broker, account, position, execution adapter와 Portfolio Manager를 import하지 않는다. 보고서는 identifier·가격·계좌정보 없이 outcome, 상태, reason/blocker와 mutation 0건만 mode 600으로 기록한다.

이 체크포인트는 lifecycle evidence만 append한다. 국내 계좌·잔고·포지션·주문 endpoint, Alpaca Paper endpoint, 외부 알림, risk allocation과 자동 champion은 모두 0건이다.

## 검증

- focused lifecycle/schema/CLI: `73 passed`
- migration/bootstrap regression: `68 passed`
- 전체 회귀: `2708 passed`
- actual CLI help: exit `0`
- invalid session date: exit `2`, append `0`
- fixture happy/replay: exit `0/0`, lifecycle row `1`, created `true/false`
- report mode `600`, external account/order mutation `0`
- Ruff 전체, changed-file format, basedpyright `0 errors, 0 warnings`, compileall: 통과
- 신규·분리 production module no-excuse: 위반 `0`; 기존 `experiment_ledger_store.py`의 oversized module, `object` annotation, broad catch 기술부채 `3`건은 이번 변경에서 늘리지 않음

## 다음 단계

KR 장후 운영 순서를 `trial terminal → independent Reviewer → Lifecycle Controller`로 직렬화하는 redacted local runner를 추가한다. 각 단계는 앞 단계 성공 뒤에만 시작하고 audit을 별도로 남기며, 실패 terminal이나 Reviewer 누락을 상태 유지 성공으로 축소하지 않는다. provider 수집, current quote 신호 생성과 이 장후 control-plane은 서로 다른 Writer 경계를 유지한다.
