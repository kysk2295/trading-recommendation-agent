# Alpaca News Raw-First 체크포인트

## 범위

미국 주식 뉴스 evidence의 첫 고정 provider source로 Alpaca Historical News REST를 연결했다. 이 경로는 최대 50개 명시 종목과 최대 24시간 window를 조회하는 read-only 수집기다. 뉴스 추천, 감성 판정, 종목 선정, 계좌 조회 또는 주문 권한은 포함하지 않는다.

회사별 임의 IR RSS URL은 아직 허용하지 않는다. issuer-direct source는 고정 endpoint onboarding, 이용권, retention과 correction 계약이 추가된 뒤 독립 source로 구현한다.

## 요청·Transport 계약

- origin은 exact `https://data.alpaca.markets`, path는 `/v1beta1/news`, method는 GET으로 고정한다.
- redirect와 자동 retry를 금지하고 전체 요청 45초, wire payload 8 MiB 상한을 적용한다.
- request는 1~50개 canonical symbol, `start < end`, 최대 24시간, 페이지당 1~50건과 최대 8페이지를 고정한다.
- query는 `sort=asc`, `include_content=false`, `exclude_contentless=false`를 강제한다.
- 자격증명은 현재 사용자 소유, hardlink가 아닌 exact mode-600 파일에서만 읽고 repr·보고서에 포함하지 않는다.
- HTTP status와 content metadata, wire bytes를 parser 전에 receipt로 보존한다.

Provider 계약 근거는 Alpaca의 [Historical News Data](https://docs.alpaca.markets/us/docs/historical-news-data), [News endpoint](https://docs.alpaca.markets/us/reference/news-3), [Market Data API limits](https://docs.alpaca.markets/us/docs/about-market-data-api)다. capability rate limit은 plan별 최대치를 추측하지 않고 이 수집기의 보수적인 로컬 상한 60 requests/minute로 선언한다.

## Parser·원장 계약

- Pydantic wire parser는 최대 50건만 받고 extra field를 정규화 event 밖에 둔다.
- normalized metadata는 provider article ID, headline, source, symbols, created/updated time과 HTTPS URL만 갖는다.
- licensed `content`와 `summary`는 normalized event에 복제하지 않는다.
- 각 article은 요청 symbol과 교집합이 있고 updated time이 request window와 receipt time 안에 있어야 한다.
- page 내부·page 간 provider ID 중복, token cycle, page limit, malformed response를 서로 다른 terminal failure로 보존한다.
- mode-600 SQLite는 raw receipt와 terminal run만 append하며 normalized article은 raw receipt에서 deterministic replay한다.
- exact DDL, UPDATE/DELETE trigger, request/run/raw hash와 전체 ledger projection을 모든 public read/write에서 재검증한다.
- terminal run은 fixture·credential·network를 열기 전에 replay하고, raw receipt만 남은 crash state는 다음 page부터 재개한다.

## Capability·Entitlement

local-only registry CLI는 성공 terminal run을 `alpaca/news`, `news_events`, `us_equities:bounded_symbols` capability로 투영한다. 성공한 bounded window만 complete/10000 bps로 기록하고 실패 run은 failed/0 bps로 기록한다.

- delivery: `rest_snapshot`
- timestamps: `provider_time`, `published_at`, `received_at`
- uses: `historical_research`, `shadow_forward`
- `real_time=false`, Paper recommendation use 미부여
- redistribution: `none`
- retention: raw 30일, derived 365일, deletion required, append correction

이 선언은 Alpaca WebSocket real-time entitlement, 미국시장 전체 coverage, 뉴스 품질 또는 수익성을 증명하지 않는다.

## CLI

```bash
uv run --script run_alpaca_news_collect.py \
  --collection-id alpaca-news-fixture-001 \
  --symbols AAPL \
  --start-at 2026-07-21T13:00:00Z \
  --end-at 2026-07-21T14:00:00Z \
  --limit 50 \
  --max-pages 2 \
  --fixture-manifest tests/fixtures/alpaca_news/fixture-manifest.json \
  --database outputs/us_news/alpaca_news.sqlite3 \
  --output-dir outputs/us_news/alpaca-news-latest

uv run --offline --script run_alpaca_news_capability_registry.py \
  --collection-id alpaca-news-fixture-001 \
  --symbols AAPL \
  --start-at 2026-07-21T13:00:00Z \
  --end-at 2026-07-21T14:00:00Z \
  --limit 50 \
  --max-pages 2 \
  --database outputs/us_news/alpaca_news.sqlite3 \
  --registry outputs/data_capability/registry.sqlite3 \
  --output-dir outputs/data_capability/alpaca-news-latest
```

fixture와 credentials path는 동시에 사용할 수 없다. collection report는 symbol 개수, window 길이, page/article/raw byte와 replay 여부만 기록하고 symbol, headline, URL, raw body, token과 로컬 경로를 포함하지 않는다.

## 검증

- Alpaca News focused: `38 passed`
- full suite: `3049 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- manual CLI: collection/registry `--help`, invalid request exit `2`
- fixture process: 1 page, 331 raw bytes, article metadata 1건
- provider-free replay: missing credential path 상태에서 성공
- capability replay: 두 번째 실행 append `0/0`, resolved `1/1`
- SQLite와 report mode: `600`
- production Alpaca News GET: `0`
- credential read: `0`
- broker/account/position/order operation: `0`

다음 데이터 경계는 여러 bounded issuer run을 과장 없이 집계하는 coverage assessment와, 고정 endpoint·이용권이 등록된 issuer-direct announcement source onboarding이다. 그 뒤 검증된 news metadata를 US Opportunity hypothesis의 point-in-time evidence로 연결한다.
