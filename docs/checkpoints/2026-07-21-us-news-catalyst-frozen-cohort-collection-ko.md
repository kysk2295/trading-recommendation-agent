# US News-Catalyst Frozen Cohort Collection 체크포인트

## 범위

사전등록 후 장중 동결된 READY treatment/control cohort 전체를 같은 current-session 완료 분봉 cycle로 관측하는 전용 수집기를 추가했다. 기존 runtime fleet의 활성 scanner owner에 우연히 포함된 종목만 사용하는 경계를 제거한다.

이 수집기는 market-data research evidence만 만든다. 뉴스 방향, 기대수익, 진입가, Trade Signal, 포지션 크기, 계좌 조회 또는 주문 권한은 없다.

## Frozen Plan

`UsNewsCatalystCohortCollector`는 provider GET 전에 다음 조건을 모두 검증한다.

- cohort status가 `ready`이고 treatment/control이 완전함
- latest Alpaca security-master snapshot이 cohort 관측보다 미래가 아니며 age가 1일 이하임
- 각 symbol이 cohort 시점에 exact provider alias 하나와 유효 instrument 하나로 해석됨
- 평가시각이 cohort 관측 30분을 엄격히 초과한 뒤 2분 이내이고 해당 NYSE 정규장 안임
- 이전 20개 적격 session의 volume profile이 같은 target session과 exact completed minute까지 존재함

검증된 cohort ID, trial ID, session, cohort/evaluation time, completed minute, security snapshot ID와 정렬된 symbol/instrument/profile evidence hash를 content-addressed plan으로 동결한다. plan 파일명은 cohort ID에 고정되고 mode `600` immutable publication을 사용하므로 같은 cohort의 다른 계획은 덮어쓸 수 없다.

## Raw-First Collection

각 instrument는 기존 `AlpacaSipMinutePageClient`의 exact `https://data.alpaca.markets/v2/stocks/bars` GET-only 계약을 사용한다. redirect를 따르지 않고 `feed=sip`, `timeframe=1Min`, `adjustment=raw`, USD, session `asof`, 정규장 open부터 동결된 완료 분 직전까지 요청한다.

원문 page는 instrument별 mode-600 SQLite에 먼저 append하고 기존 canonical minute-bar projector로 ResearchInputIdentity를 만든다. 공유 intraday feature kernel은 동일 profile과 완료 분봉으로 close, VWAP, RVOL, prior-high breakout을 계산한다.

한 instrument라도 HTTP, sequence, staleness, profile 또는 feature READY 검증에 실패하면 전체 feature 묶음과 collection receipt를 발행하지 않는다. 이미 저장된 profile과 raw page는 재시작에서 exact replay하므로 성공 instrument를 다시 요청하지 않고 누락 instrument만 GET한다. 전체 symbol이 준비된 뒤에만 정렬된 feature artifact ID를 plan에 결박한 단일 mode-600 immutable receipt를 게시한다.

완성 receipt replay는 provider 요청과 자격증명 파일 읽기를 모두 생략한다. plan 없는 orphan receipt, 다른 security snapshot, symbol/instrument 불일치, 누락·변조 feature는 fail-closed다.

## CLI

```bash
uv run python run_us_news_catalyst_cohort_collect.py \
  --cohort <immutable-cohort.json> \
  --security-master-store <alpaca-security-master.sqlite3> \
  --plan-root outputs/us_news/news-catalyst-shadow/plans \
  --profile-root outputs/us_news/news-catalyst-shadow/profiles \
  --runtime-root outputs/us_news/news-catalyst-shadow/runtime \
  --canonical-root outputs/us_news/news-catalyst-shadow/canonical \
  --feature-root outputs/us_news/news-catalyst-shadow/features \
  --receipt-root outputs/us_news/news-catalyst-shadow/receipts \
  --output-dir outputs/us_news/news-catalyst-shadow
```

exit `0`은 신규 receipt 또는 exact replay다. exit `1`은 redacted fail-closed이며 report에는 symbol, instrument, 입력 경로, 자격증명 또는 계좌 정보를 쓰지 않는다. 초기 수집만 mode-600 `~/.config/trading-agent/alpaca.env`를 읽고, receipt replay에서는 읽지 않는다.

## 검증

- 신규 collector/CLI: `7 passed`
- 인접 profile/runtime/observation 회귀: 통과
- full suite: `3109 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- changed-file no-excuse audit: 위반 `0`
- actual CLI help: exit `0`
- missing cohort: exit `1`, `not-published`, credential read `0`, 입력 경로 비노출
- fixture happy/replay: exit `0/0`, 최초 4종목 `84 GET`, replay 추가 GET `0`
- partial current-session failure recovery: 누락 1종목만 재요청, 총 `85 GET`
- credential read: 최초 `1`, receipt replay 추가 `0`
- plan/receipt/file mode: `600`; runtime/profile/canonical root mode: `700`
- account read/order mutation: `0`

## 남은 운영 경계

이번 체크포인트는 fixture HTTP transport에서 GET-only 인과성과 재시작을 검증했다. production Alpaca SIP 정규장 GET smoke, WebSocket 상시 stream, 실표본 또는 수익성을 증명하지 않는다.

다음 단계는 장전 register, 장중 start, cohort collection, 30분 observation, 장후 finalize와 독립 Reviewer를 실제 domain artifact 상태로 복구 가능한 일일 single-writer scheduler에 연결하는 것이다. 실제 forward 표본과 Reviewer 근거 전에는 lifecycle 승격, Trade Signal 또는 Alpaca Paper 실행과 결합하지 않는다.
