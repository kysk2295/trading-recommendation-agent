# Alpaca News Coverage·Opportunity Evidence 체크포인트

## 범위

단일 bounded Alpaca News run을 여러 symbol slice로 확장하되 미국시장 전체 coverage로 과장하지 않는 deterministic assessment와 Opportunity Manager 입력 evidence를 추가했다. 이 단계는 뉴스 기반 종목 ranking, 감성 판정, `OpportunitySnapshot`, Trade Signal 또는 주문을 만들지 않는다.

실제 후보 선정은 이 evidence를 소비하는 별도 hypothesis와 immutable strategy version을 사전등록한 뒤에만 추가한다.

## Wire replay 수정

이전 raw-first client는 compressed wire bytes와 `content_encoding`을 보존했지만 parser가 wire bytes를 JSON으로 바로 읽었다. 실제 gzip/deflate 응답이 구조 실패가 되는 결함을 다음처럼 수정했다.

- client는 parser가 지원하는 `gzip, deflate`만 `Accept-Encoding`으로 광고한다.
- parser는 raw receipt를 변경하지 않고 gzip/deflate를 별도 buffer로 decode한다.
- wire와 decoded payload 모두 8 MiB 상한을 적용한다.
- incomplete stream, trailing stream, unsupported encoding과 decompression bomb은 fail-closed한다.

## Coverage manifest

`AlpacaNewsCoverageManifest`는 다음 범위를 고정한다.

- lowercase opaque universe ID
- assessment cutoff
- 1~8개 request slice
- slice별 기존 1~50 symbols, 최대 24시간, 최대 8 pages 계약
- 모든 slice의 exact start/end/limit/max-pages 일치
- collection ID와 request ID의 유일성
- slice 사이 symbol 중복 금지
- 최대 400개 declared symbols

CLI는 mode-600 query-only manifest만 읽고 cutoff가 현재보다 미래면 source DB를 assessment하지 않는다.

## Terminal assessment

각 manifest request ID를 news raw ledger에서 exact replay하고 cutoff 시점의 상태를 `success`, `failed`, `missing`으로 고정한다.

- cutoff 뒤에 완료된 terminal은 존재하더라도 as-of assessment에서는 `missing`이다.
- completeness는 page나 slice 수가 아니라 성공한 declared symbol 수로 계산한다.
- failed slice의 partial article은 기록하되 accepted article 합계와 downstream evidence에는 포함하지 않는다.
- missing과 failed를 success 또는 0건 success로 바꾸지 않는다.
- manifest와 assessment는 하나의 content-addressed mode-600 coverage artifact로 게시한다.

## Opportunity evidence

모든 declared symbol이 success일 때만 `AlpacaNewsOpportunityEvidenceBundle`을 만든다.

- declared symbol마다 snapshot 하나를 발행한다.
- 뉴스 0건도 complete source에서 관측된 명시적 0건 snapshot으로 보존한다.
- article observation은 opaque event ID, raw receipt ID, symbol, source와 provider/received timestamp만 포함한다.
- headline, URL, summary, content와 raw body는 evidence artifact에 복제하지 않는다.
- `EvidenceRef`는 coverage assessment와 receipt-bound article observation의 canonical key만 담는다.
- source coverage는 `alpaca_news`, exact record count와 `complete=true`로 고정한다.
- bundle에는 ranking, score, feature, recommendation, 유효 진입가 또는 주문권한이 없다.

coverage와 evidence artifact는 content-addressed private immutable file로 게시한다. exact replay는 새 artifact를 만들지 않고 conflict·tamper·foreign slice는 typed contract error로 닫는다.

## CLI

```bash
uv run --offline --script run_alpaca_news_opportunity_evidence.py \
  --manifest outputs/us_news/manifests/bounded-news-scope.json \
  --database outputs/us_news/alpaca_news.sqlite3 \
  --output-dir outputs/us_news/alpaca-news-opportunity-evidence
```

manifest는 현재 사용자 소유 mode-700 parent 아래의 mode-600 regular file이어야 한다. CLI는 provider client, credential loader, account 또는 execution 모듈을 import하지 않는다.

complete assessment는 coverage와 evidence artifact를 둘 다 게시한다. incomplete assessment는 coverage artifact와 redacted report를 남긴 뒤 exit `2`로 끝나며 evidence artifact는 만들지 않는다.

## 검증

- Alpaca News focused: `52 passed`
- full suite: `3063 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- manual CLI `--help`: exit `0`
- missing manifest: redacted exit `2`
- complete fixture: symbols `2/2`, completeness `10000`, snapshots `2`
- exact replay: coverage/evidence artifact created `0/0`
- incomplete fixture: symbols `1/2`, missing slice `1`, coverage만 게시 후 exit `2`
- DB, manifest, artifact, report mode: `600`
- network/credential/provider request: `0`
- broker/account/position/order mutation: `0`

다음 경계는 이 snapshot을 소비하는 US news-catalyst Opportunity hypothesis와 deterministic baseline strategy version을 전역 experiment ledger에 사전등록하고, ranking 근거·유효시간·반증 조건을 고정한 뒤 실제 `OpportunitySnapshot`을 발행하는 것이다. issuer-direct announcement source는 고정 endpoint·이용권·retention 계약이 등록된 경우에만 별도로 추가한다.
