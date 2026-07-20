# SEC Filing Document Raw-First 체크포인트

## 범위

Milestone 5의 검증된 SEC filing metadata에서 primary document 본문을 exact binding해 수집하는 bounded read-only vertical을 추가했다. 이 경로는 규제 공시 evidence 수집이며 추천·수익성 판정·계좌·주문 권한이 없다.

## Target 계약

- source metadata SQLite를 하나의 query-only snapshot에서 structural·semantic 검증한다.
- recent parent와 수집 완료된 additional-history child의 immutable filing version만 읽는다.
- accession별 최신 observation을 `accepted_at`, `observed_at`, version identity 순으로 결정하고 invocation당 최대 8개만 선택한다.
- archive path는 `/Archives/edgar/data/{issuer CIK without leading zeroes}/{accession without dashes}/{primary document}`로만 만든다.
- accession prefix는 filing agent CIK일 수 있다. archive의 issuer folder는 source filing event의 issuer CIK로 결정한다.
- caller가 URL, origin, accession folder나 primary-document path를 직접 주입할 수 없다.

## Transport·원장 계약

- production origin은 exact `https://www.sec.gov`, method는 GET, redirect는 금지다.
- SEC 연락처 User-Agent, 전체 request deadline, wire payload 64 MiB 상한을 적용한다.
- HTTP success/error와 빈 body raw bytes를 parser 이전에 별도 mode-600 SQLite receipt로 먼저 확정한다.
- receipt와 terminal run은 append-only이며 UPDATE/DELETE trigger, exact DDL, foreign key, integrity, target/raw/payload hash를 재검증한다.
- `transport` 실패만 receiptless다. HTTP status와 empty body 실패는 raw receipt를 보존한다.
- receipt-backed terminal은 raw response의 status·body·byte count·receipt identity와 정확히 일치하고 `started_at <= received_at <= completed_at`을 만족한다.
- 완료 terminal과 receipt-only crash state는 fixture·User-Agent·provider를 열기 전에 재생한다.
- batch는 최대 8개를 순차 처리하고 첫 terminal failure에서 멈춘다.

## CLI

```bash
uv run run_sec_filing_document_collect.py \
  --parent-collection-id sec-YYYYMMDD-001 \
  --cik 0000320193 \
  --metadata-database outputs/us_regulatory/sec_edgar.sqlite3 \
  --document-database outputs/us_regulatory/sec_filing_documents.sqlite3 \
  --output-dir outputs/us_regulatory/sec/document-latest \
  --max-documents 1
```

fixture와 User-Agent 설정은 동시에 사용할 수 없다. report는 문서 수·raw byte·replay 수만 기록하며 CIK, accession, primary document, 로컬 경로, raw body와 User-Agent를 포함하지 않는다.

## 검증

- document focused: `22 passed`
- SEC focused: `158 passed`
- full suite: `3011 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- manual CLI: `--help`, invalid input exit `2`, fixture 1 document/35 bytes, missing User-Agent replay 1 확인
- production SEC GET: `0`
- credential read: `0`
- broker/account/position/order operation: `0`

다음 M5 경계는 issuer/company-announcement evidence를 독립 raw-first source로 추가하는 것이다. 그 뒤 여러 issuer를 묶는 시장 단위 coverage assessment로 확장한다.
