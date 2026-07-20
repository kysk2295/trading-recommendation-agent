# US News-Catalyst Feature Observation 체크포인트

## 범위

사전등록된 일별 news-catalyst treatment/control cohort를 실제 current-session 완료 분봉 피처와 연결했다. 이 경계는 30분 setup observation manifest만 생성한다. 뉴스 방향, 진입가, 기대수익, 포지션 크기, Trade Signal 또는 주문은 만들지 않는다.

## Canonical Feature

공유 intraday feature kernel의 READY snapshot에 최신 완료 봉의 `close`를 추가했다. blocked snapshot은 종가를 노출하지 않는다. 기존 VWAP, ATR14, RSI14, MACD, RVOL, prior-high breakout 계산은 별도 순수 수학 모듈로 분리했고 기존 수식 회귀를 유지했다.

runtime fleet cycle에 `--news-catalyst-feature-root`를 지정하면 각 READY binding을 다음 lineage에 결박한 content-addressed mode-600 artifact로 게시한다.

- symbol과 instrument ID
- NYSE session date, observation time, latest completed source end
- research input identity와 intraday volume-profile evidence hash
- indicator semantic version
- close, VWAP, RVOL, prior-high breakout

옵션을 생략하면 기존 fleet 동작은 바뀌지 않는다. 이 export는 기존 Alpaca market-data GET-only runtime binding만 읽으며 account, position, order 또는 Paper mutation 경로를 추가하지 않는다.

## Same-Cycle Projection

`run_us_news_catalyst_setup_observation.py`는 immutable cohort와 feature artifact directory를 로컬에서만 읽는다. 다음 조건을 모두 만족하는 가장 최신의 완전한 동일 observation cycle만 선택한다.

- cohort status가 `ready`
- treatment와 control 전체 symbol이 정확히 한 번씩 존재
- feature session이 cohort session과 일치
- feature observation이 평가시각보다 미래가 아니고 age가 2분 이하
- latest completed source end가 cohort 관측 30분 뒤 이상
- 평가시각이 해당 NYSE 정규장 안

종목 누락, 중복, stale cycle, blocked feature, 다른 session 또는 불완전 control은 partial 성과로 바꾸지 않고 exit `1`로 차단한다. 성공 manifest는 기존 evaluator의 `close > VWAP`, `RVOL >= 1.5`, prior-high breakout 교집합을 사용하며 content-addressed mode-600 immutable artifact로 게시한다. exact replay는 동일 파일을 재사용한다.

## CLI

runtime fleet에서 feature artifact export를 활성화한다.

```bash
./run_us_runtime_fleet_cycle.py \
  <기존 GET-only runtime 옵션> \
  --news-catalyst-feature-root outputs/us_news/news-catalyst-shadow/features
```

cohort 관측 30분 뒤 setup manifest를 생성한다.

```bash
./run_us_news_catalyst_setup_observation.py \
  --cohort <immutable-cohort.json> \
  --feature-root outputs/us_news/news-catalyst-shadow/features \
  --artifact-root outputs/us_news/news-catalyst-shadow/artifacts \
  --output-dir outputs/us_news/news-catalyst-shadow
```

exit `0`은 신규 게시 또는 exact replay, exit `1`은 redacted fail-closed다. report에는 symbol, instrument, 입력 경로 또는 자격증명을 쓰지 않는다.

## 검증

- focused feature, runtime export, observation CLI: `17 passed`
- full suite: `3102 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- no-excuse changed-file audit: 위반 `0`
- actual CLI help: exit `0`
- missing cohort: exit `1`, `not-published`, 입력 경로 비노출
- fixture happy/replay: `0/0`, observation artifact 1개, mode `600`
- provider request, credential read, account read, order mutation: `0`

## 남은 운영 경계

현재 runtime fleet export는 그 cycle의 활성 scanner owner만 저장한다. 따라서 frozen cohort의 treatment/control 전체가 fleet scope에 포함된다는 보장은 아직 없다. 다음 체크포인트는 cohort symbol 전용 bounded GET-only collection, 장전 register, 장중 start, 30분 observation, 장후 finalize/Reviewer를 하나의 재시작 가능한 일일 single-writer scheduler로 연결하는 것이다. 그 scheduler와 실제 Paper forward 표본이 생기기 전에는 lifecycle 승격, Trade Signal 또는 Alpaca Paper 실행과 결합하지 않는다.
