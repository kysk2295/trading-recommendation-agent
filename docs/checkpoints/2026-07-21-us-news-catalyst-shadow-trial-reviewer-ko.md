# US News-Catalyst Shadow Trial·Reviewer 체크포인트

## 범위

사전등록된 `us_equities/opportunity_manager/news_catalyst` baseline을 하루 단위 shadow forward-validation으로 연결했다. 이 trial은 뉴스 후보가 동일 시점·동일 universe의 zero-news control보다 30분 기술적 setup을 더 자주 충족하는지만 비교한다. 수익률, 방향, 진입가, 보유기간, 포지션 크기 또는 주문을 만들지 않는다.

## 일별 Trial

NYSE 정규장 시작 전에 global experiment ledger에 exact strategy version과 거래일을 결박한 `shadow_forward` trial을 등록한다. data version은 strategy registration key, session date, evaluator version과 고정 evidence budget으로 결정하며 exact replay는 기존 registration을 재사용한다.

장중 `ranked` Opportunity의 5분 유효구간 안에서만 sequence 1 `started` event를 기록한다. 시작 전에 다음 cohort를 content-addressed mode-600 artifact로 동결한다.

- treatment: projection의 순서가 확정된 최대 20개 news candidate
- control: 같은 complete evidence universe에서 뉴스 observation이 0건인 symbol
- control 선택: `trial_id|symbol` SHA-256 순서의 결정론적 equal-size sampling
- 대조군 부족: `insufficient_control`; 이후 0성과로 계산하지 않고 censored

## Terminal Outcome

Opportunity 관측 30분 뒤이며 같은 정규장 안에서만 terminal을 확정한다. 모든 treatment/control symbol에 대해 canonical feature evidence가 완전해야 한다. setup confirmation은 다음 세 조건의 교집합이다.

- `close > vwap`
- `rvol >= 1.5`
- `close above prior high breakout = true`

완전한 양 arm은 confirmation rate와 treatment-control lift를 basis point로 계산해 `completed`로 남긴다. 대조군 부족이나 observation manifest 부재는 `censored`이며 어떤 성과값도 0으로 대체하지 않는다. cohort, setup manifest, outcome은 같은 private artifact root에 immutable하게 게시되고 terminal event가 세 content hash, reason code와 이전 event key를 결박한다. 재시작은 기존 시각과 artifact를 exact replay하며 cohort 파일 누락·변조·중복 또는 다른 outcome은 fail-closed한다.

## 독립 Reviewer

Reviewer는 정규장 종료 뒤 query-only ledger와 immutable artifact만 읽고 `as_of_session`까지 해당 evaluator의 모든 trial을 집계한다. cohort, setup manifest, outcome과 terminal event의 strategy, session, trial, hash, 시각, reason code가 모두 일치해야 completed 표본으로 인정한다.

- censored, failed, missing terminal 또는 artifact 불일치: `data_quality_review`
- clean completed session 20개 이상, treatment/control observation 각각 100개 이상: `comparison_ready`
- 그 외 clean immature sample: `continue_collection`

세 action 모두 자동 lifecycle 변경, 주문권한 변경, allocation 변경 권한은 `false`다. `comparison_ready`도 통계 비교 준비가 됐다는 뜻일 뿐 champion 또는 수익성 판정이 아니다.

## CLI

```bash
./run_us_news_catalyst_shadow_trial.py register \
  --registration-manifest examples/us_news_catalyst/research-registration.json \
  --session-date 2026-07-22 \
  --experiment-ledger outputs/experiment_ledger/global.sqlite3 \
  --output-dir outputs/us_news/news-catalyst-shadow

./run_us_news_catalyst_shadow_trial.py start \
  --trial-id <trial-id> \
  --projection <immutable-projection.json> \
  --evidence <immutable-news-evidence.json> \
  --artifact-root outputs/us_news/news-catalyst-shadow/artifacts \
  --experiment-ledger outputs/experiment_ledger/global.sqlite3 \
  --output-dir outputs/us_news/news-catalyst-shadow

./run_us_news_catalyst_shadow_trial.py finalize \
  --trial-id <trial-id> \
  --cohort <immutable-cohort.json> \
  --observation-manifest <immutable-setup-observations.json> \
  --artifact-root outputs/us_news/news-catalyst-shadow/artifacts \
  --experiment-ledger outputs/experiment_ledger/global.sqlite3 \
  --output-dir outputs/us_news/news-catalyst-shadow

./run_us_news_catalyst_shadow_trial.py review \
  --strategy-version <strategy-version> \
  --as-of-session 2026-07-22 \
  --artifact-root outputs/us_news/news-catalyst-shadow/artifacts \
  --review-root outputs/us_news/news-catalyst-shadow/reviews \
  --experiment-ledger outputs/experiment_ledger/global.sqlite3 \
  --output-dir outputs/us_news/news-catalyst-shadow
```

exit `0`은 등록·ready start·completed terminal·mature comparison을 뜻한다. 정상적 비완결 상태인 insufficient control, censored, immature collection과 data-quality review는 exit `2`, 입력·시점·무결성 차단은 redacted exit `1`이다.

## 검증

- focused shadow trial·Reviewer·CLI: `13 passed`
- full suite: `3091 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- actual CLI help: exit `0`
- redacted missing input: exit `1`, 입력 경로 비노출
- register/start/finalize replay: `0/0`, `0/0`, `0/0`
- clean immature Reviewer/replay: `2/2`, `continue_collection`
- missing setup artifact: `data_quality_review`
- no-observation terminal: exit `2`, `censored`; Reviewer exit `2`
- ledger, artifact, report mode: `600`
- provider request, credential read, account read, order mutation: `0`

다음 경계는 production current-session canonical minute features에서 setup observation manifest를 자동 생성하고 장전 등록, 장중 cohort freeze, 30분 terminal, 장후 Reviewer를 일정 기반 단일 writer loop로 연결하는 것이다. 실제 forward 표본이 쌓이기 전에는 strategy 승격, Trade Signal 또는 Alpaca Paper 실행과 결합하지 않는다.
