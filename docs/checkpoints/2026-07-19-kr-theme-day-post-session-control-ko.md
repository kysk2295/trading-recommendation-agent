# KR theme day 장후 control cycle 체크포인트

## 독립 child 계약

- `run_kr_theme_day_trial_terminal.py`: exact started trial과 private entry·exit store를 재생해 completed, censored 또는 failed terminal artifact/event를 확정한다.
- `run_kr_theme_day_reviewer.py`: global trial chain과 terminal·entry·exit evidence를 query-only로 다시 검증하고 별도 review store에 집계 action을 append한다.
- `run_kr_theme_day_lifecycle.py`: persisted review와 원천 evidence, 당일 공식 KIS calendar를 다시 검증해 다음 open session lifecycle만 append한다.

세 CLI는 local store와 immutable trial/strategy/session identity만 받는다. credential, provider, endpoint, account, order, arm, force와 timestamp override는 명령행에 없다. 각 report는 identifier·가격·hash·path 없이 aggregate status와 권한 false, external mutation 0만 mode 600으로 기록한다.

## 직렬 runner

`run_kr_theme_day_post_session.py`는 `terminal → independent Reviewer → Lifecycle Controller`를 별도 child process로 실행한다. 각 child exit code를 전용 append-only CSV에 기록하고 mode 600으로 확정한 뒤에만 다음 child를 시작한다.

- terminal nonzero 또는 terminal audit 실패: Reviewer와 lifecycle `not_started`
- Reviewer nonzero 또는 Reviewer audit 실패: lifecycle `not_started`
- lifecycle nonzero: aggregate blocked
- 세 단계 zero: `completed_control_cycle`

`completed_control_cycle`은 전략 성과, 승격 또는 주문 승인이 아니라 control-plane 단계가 모두 실행됐다는 뜻이다. failed terminal이 유효하게 기록되면 Reviewer 정책이 data-quality evidence로 판정할 수 있지만, 변조된 source 때문에 Reviewer가 evidence를 재생하지 못하면 lifecycle을 실행하지 않는다.

## 재시작 수정

기존 terminal finalizer는 sequence 2가 이미 존재해도 재호출 시각으로 artifact를 다시 계산해 immutable conflict를 만들었다. 이제 기존 terminal event가 있으면 최초 `occurred_at`을 canonical terminal 시각으로 재사용한다. 같은 session의 늦은 replay는 terminal artifact/event, review와 lifecycle 행을 늘리지 않으며 source evidence가 달라졌다면 conflict를 유지한다.

## 검증

- focused post-session children/runner/terminal/Reviewer/lifecycle: `29 passed`
- 전체 회귀: `2718 passed`
- actual CLI help: terminal/Reviewer/runner 모두 exit `0`
- runner invalid date: exit `2`
- actual missing-source runner: exit `1`, terminal audit `failed`, 뒤 두 단계 `not_started`
- fixture happy/replay: exit `0/0`, trial events/reviews/lifecycle `2/1/1`
- aggregate report와 세 phase audit mode `600`
- Ruff 전체, changed-file format, basedpyright `0 errors, 0 warnings`, compileall, 신규·변경 production no-excuse: 통과
- credential, provider, external network, account/order mutation: `0`

## 다음 단계

KR session supervisor가 개장 전 trial 등록·시작, 장중 read-only quote/setup/signal·shadow entry/exit와 이 장후 runner를 하나의 durable session manifest로 연결한다. supervisor는 current KST session과 공식 calendar를 매 phase에서 다시 검증하고 재시작 시 마지막 terminal audit 이후 단계만 exact replay해야 한다. 국내 account/order path와 Portfolio Manager는 계속 추가하지 않는다.
