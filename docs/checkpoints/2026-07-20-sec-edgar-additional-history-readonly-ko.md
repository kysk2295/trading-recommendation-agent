# SEC EDGAR Additional-History Read-Only 체크포인트

## 범위

Institutional Multi-Market Quant Research OS Milestone 5의 SEC recent 수집을 과거 filing history까지 확장했다. 이 경로는 공시 evidence를 GET-only로 수집하며 추천, 전략 승격, 계좌 조회 또는 주문 권한이 없다.

## 공식 계약

- SEC submissions 응답의 `filings.files`는 추가 JSON 파일명과 각 파일의 filing date range를 제공한다.
- client는 부모 recent raw receipt에 선언된 `CIK##########-submissions-###.json` 파일만 exact `https://data.sec.gov/submissions/` origin에서 가져온다.
- redirect, path traversal, 다른 CIK, query string과 임의 URL은 network 전에 거부한다.
- 한 invocation은 선언 순서의 최대 8개 파일만 직렬 처리한다. recent request 하나와 합쳐도 SEC의 최대 10 requests/second 경계를 넘지 않는다.
- 각 요청은 기존 recent client와 같은 전체 45초 deadline, wire/decoded 각각 64 MiB 상한, 선언된 연락처 User-Agent와 no-redirect 정책을 사용한다.

공식 근거는 SEC의 [EDGAR API 안내](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)에서 submissions 응답이 최소 1년 또는 1,000개의 recent filing을 포함하고, 추가 filing이 있으면 `files`에 추가 JSON 파일과 날짜 범위를 제공한다는 계약을 따른다.

## 구현

- `SecAdditionalHistoryFile`은 CIK, exact basename, filing count, 시작일과 종료일을 immutable manifest로 파싱한다. duplicate name, 다른 CIK, 음수/초과 count와 역방향 날짜 범위는 거부한다.
- child collection ID는 부모 recent receipt ID와 exact manifest canonical JSON의 SHA-256으로 결정된다.
- `SecSubmissionRun`은 `recent`와 `additional_history` source kind를 구분한다. history run은 부모 receipt와 manifest 없이는 생성할 수 없고 additional-history child를 다시 부모로 사용할 수 없다.
- store schema는 바꾸지 않았다. history raw receipt, run과 filing observation은 기존 append-only table과 accession correction chain을 사용하고 run payload의 parent binding을 매번 재검증한다.
- store는 부모가 terminal success인지, 부모 raw receipt에서 같은 manifest가 다시 파싱되는지, child CIK/collection ID가 exact한지, child provider receipt가 부모 provider receipt보다 빠르지 않은지, child terminal이 부모 terminal보다 빠르지 않은지를 확인한다.
- history JSON의 top-level parallel columns는 recent와 같은 2,000-item streaming preflight와 strict column-length 검사를 통과해야 한다. 실제 filing count와 모든 filing date가 부모 manifest 범위와 일치해야 한다.
- raw response는 parser 전에 확정한다. HTTP/구조/manifest 오류도 raw receipt와 terminal failure로 남고, transport failure만 receiptless terminal을 허용한다.
- parse 이전 orphan은 부모 terminal 시각과 provider receipt 시각 중 늦은 값으로 deterministic terminal을 복구한다. 완료 child는 fixture·User-Agent·HTTP client를 열기 전에 exact replay한다.
- 새 CLI 보고서는 파일/filing 집계와 replay 여부만 mode `600`으로 기록하고 CIK, 파일명, accession, 원문, User-Agent와 경로를 노출하지 않는다.
- typed manifest 이전 run payload에 `source_kind`가 실제로 없으면 과거 parser가 허용했던 opaque `filings.files`는 count와 recent filing만 compatibility projection으로 재생한다. 이 경로는 history child를 만들지 않으며 신규 collection은 계속 typed manifest 없이는 실패한다.
- 파일·User-Agent·store 실패는 CLI에서 로컬 경로를 포함하지 않는 고정 오류로 변환한다.
- 존재하지 않는 사용자의 `~user` 경로도 alias 검사 안에서 fail-closed해 traceback과 로컬 경로를 노출하지 않는다.
- fixture manifest는 `O_NOFOLLOW` descriptor 하나에서 64 KiB까지만 읽고, 읽은 inode와 최종 resolved path inode가 같을 때만 파싱한다.

## 실행

먼저 recent collection을 terminal success로 확정한 뒤 같은 private database를 history CLI에 전달한다.

```bash
uv run --script run_sec_edgar_history_collect.py \
  --parent-collection-id sec-YYYYMMDD-001 \
  --cik 0000320193 \
  --database outputs/us_regulatory/sec_edgar.sqlite3 \
  --output-dir outputs/us_regulatory/sec/history-latest \
  --max-files 1
```

`--max-files`는 `1..8`만 허용한다. terminal failure는 같은 child collection에서 재시도하지 않고 새 recent parent collection으로 다시 관측해야 한다.

## 검증

- focused SEC: `117 passed`
- full suite: `2970 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- manual CLI: `--help`, invalid CIK, recent fixture `2/2`, history fixture `1/1`, missing User-Agent terminal replay `1/0`
- actual SEC production GET: `0`
- credential read: `0`
- broker, account, position or order operation: `0`

이 source의 실제 coverage와 health를 canonical capability registry에 투영하는 경계는 [SEC EDGAR capability registry 체크포인트](2026-07-21-sec-edgar-capability-registry-ko.md), filing document 본문은 [SEC filing document raw-first 체크포인트](2026-07-21-sec-filing-document-raw-first-ko.md)에서 완료했다. 다음 M5 경계는 issuer/company-announcement evidence를 별도 bounded source로 추가하는 것이다.
