# KR theme day independent Reviewer 체크포인트

## 독립 입력 계약

- Reviewer는 `ExperimentLedgerReader`와 private entry, exit, terminal store를 query-only로 읽는다.
- strategy version과 as-of session까지 등록된 exact KR day trial 전체가 terminal artifact 전체와 일치해야 한다.
- sequence 1/2 event key, terminal kind/reason/time, artifact SHA와 ordered entry·exit canonical SHA를 다시 계산한다.
- import closure에는 Alpaca, Paper, broker, execution, credential/provider, lifecycle controller와 Portfolio Manager가 없다.

## 평가 정책 v1

- completed terminal의 exit만 `exit_at` 순서로 compounded return, mean realized R, win rate와 max drawdown을 계산한다.
- censored는 수익 0이 아니며 failed와 함께 별도 data-quality count다.
- 20 forward sessions 또는 30 completed signals 미만은 `continue_collection`이다.
- censored/failed가 하나라도 있으면 `data_quality_review`다.
- 최소 표본을 충족해도 `comparison_ready`일 뿐 independent comparator와 multiple-testing evidence가 추가로 필요하다.
- automatic lifecycle, Paper authority와 allocation change는 항상 false다.

## 저장과 재시작

private append-only SQLite는 `(strategy_version, as_of_session, reviewer_version)`당 review event 하나만 허용한다. exact replay는 최초 `reviewed_at`을 재사용해 append 0건이며 action/reason/blocker를 counts에서 재계산하므로 유효한 형태의 정책 불일치 payload도 거부한다.

## 검증

- focused reviewer/terminal/trial/entry/exit: `26 passed`
- 전체 회귀: `2691 passed`
- minimal driver: `continue_collection`, sessions/trades `1/1`, positive return/R, first/replay `true/false`, 세 권한 `false`, mode `600`
- Ruff, format, basedpyright, compileall, no-excuse: 통과
- network, credential, account/order/lifecycle/allocation mutation: `0`

## 다음 단계

Reviewer event를 KR shadow lifecycle v2의 evidence key로 연결하되 자동 승격은 계속 닫는다. 먼저 `EXPERIMENTAL_SHADOW → CHALLENGER`의 다음 세션 projection과 censored/failed 조기중단 권고를 분리하고, 실제 20/30 표본과 comparator·multiple-testing evidence 전에는 `SHADOW_CHAMPION`을 만들지 않는다.
