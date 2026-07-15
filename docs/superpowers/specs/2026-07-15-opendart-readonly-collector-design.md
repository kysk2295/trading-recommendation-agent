# OpenDART Read-Only Catalyst Collector 설계

## 1. 범위

이 설계는 다중 시장 Research OS Milestone 3과 KR Theme Phase T0의 첫 production source adapter다.

```text
OpenDART 공시검색 GET
-> exact HTTP response receipt append
-> strict page/status validation
-> disclosure별 canonical DART catalyst + receipt lineage append
-> immutable DART source-run 결과
```

이번 단계는 OpenDART `list.json` 공시검색만 연결한다. 뉴스, KIS 국내 랭킹, 거래량 급증, LLM, 현재가, KR 위험 게이트, TradeSignal, shadow fill과 국내 주문은 포함하지 않는다. 실제 API 키를 사용하는 네트워크 QA도 수행하지 않고 injected transport와 committed fixture로 계약을 검증한다.

공식 계약은 [OpenDART 공시검색 개발가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001)를 기준으로 한다.

## 2. 비교한 접근

### 2.1 응답에서 필요한 필드만 골라 바로 catalyst로 저장 - 기각

페이지 상태, API 오류와 원래 응답 bytes를 잃어 source가 실제로 무엇을 반환했는지 재감사할 수 없다. 프로세스가 파싱 중 중단되면 수신 사실도 사라진다.

### 2.2 응답 페이지 전체를 catalyst 하나로 저장 - 기각

분류 단위가 개별 공시가 아니라 페이지가 되어 하나의 페이지에 여러 테마가 섞인다. 접수번호 기반 멱등성과 기존 classification 계약도 훼손된다.

### 2.3 raw receipt와 disclosure catalyst를 분리해 연결 - 채택

HTTP response bytes를 먼저 append-only receipt로 확정한다. 그 다음 strict parser가 개별 공시 객체의 모든 공식 필드를 canonical JSON으로 보존하고, observation별로 receipt ID와 item index를 연결한다. 파싱 또는 pagination 검증이 실패해도 이미 받은 receipt와 부분 observation은 삭제하지 않고 실패 source run으로 종결한다.

## 3. 공식 API 경계

- 허용 base URL: `https://opendart.fss.or.kr`
- 허용 method/path: `GET /api/list.json`
- redirect: 금지
- 검색 범위: 명시적인 KST 달력일 한 날짜, `bgn_de=end_de=YYYYMMDD`
- 정렬: `sort=date`, `sort_mth=asc`
- pagination: `page_count=100`, `page_no=1..total_page`
- 최대 page: 100. 초과하면 부분 결과를 성공으로 축약하지 않는다.
- `corp_code`, 공시 유형과 법인 구분은 이번 adapter에서 필터링하지 않는다.

`status=000`만 일반 성공이다. `status=013`은 공식 의미가 조회 결과 없음이므로 성공 0건으로 보존한다. `010`, `011`, `012`, `014`, `020`, `021`, `100`, `101`, `800`, `900`, `901`과 알 수 없는 status는 실패다. API message, 요청 URL과 query는 로그·예외·보고서에 출력하지 않는다.

## 4. 비밀과 HTTP 안전 계약

- API 키는 기본적으로 `~/.config/trading-agent/opendart.env`의 `OPENDART_API_KEY`에서만 읽는다.
- 파일은 symlink가 아닌 regular file이며 mode가 정확히 `600`이어야 한다.
- 키는 공백 없는 ASCII 40자여야 하고 누락·중복·추가 설정을 거부한다.
- 키 값, query string, request/response header와 raw response는 repr·CLI·보고서에 포함하지 않는다.
- production client는 exact base URL과 `follow_redirects=False`를 생성·검증하고 다른 host/path 또는 method를 보내지 않는다.
- HTTP retry가 있더라도 idempotent GET에만 제한한다.

## 5. 원장 schema v2

기존 네 표와 의미는 바꾸지 않고 다음 표만 추가한다.

### `kr_source_receipts`

```text
receipt_id
source_run_id
source
request_key
received_at
http_status
content_type
payload_sha256
payload_blob
```

`request_key`는 `opendart:list:<YYYYMMDD>:page:<N>`처럼 인증정보가 없는 canonical key다. 같은 source run과 request key의 동일 응답은 no-op이고 내용이 다르면 conflict다.

### `kr_catalyst_observation_receipts`

```text
collection_cycle_id
catalyst_id
receipt_id
item_index
item_payload_sha256
```

하나의 cycle observation이 어느 receipt의 몇 번째 공시에서 파생됐는지 보존한다. receipt source와 catalyst source가 다르거나 receipt 수신시각보다 이른 observation은 거부한다.

### `kr_source_collection_runs`

```text
source_run_id
collection_cycle_id
source
adapter_version
started_at
completed_at
status
record_count
failure_code
receipt_ids
```

source run은 terminal append-only 결과다. 기록 전에 해당 cycle/source의 observation 수, receipt link와 receipt ID 집합을 다시 계산한다. 성공·실패 모두 immutable이며 같은 cycle/source를 다른 결과로 덮어쓸 수 없다.

모든 새 표도 UPDATE/DELETE trigger로 보호한다. 기존 schema v1 DB는 한 Writer lease 안에서 새 표와 trigger만 추가한 뒤 v2로 승격한다. 기존 catalyst, observation, cycle과 classification 행은 재작성하지 않는다.

## 6. OpenDART 응답과 catalyst 계약

성공 응답은 다음 pagination metadata와 disclosure list를 strict하게 검증한다.

```text
status, message
page_no, page_count, total_count, total_page
list[]:
  corp_cls, corp_name, corp_code, stock_code
  report_nm, rcept_no, flr_nm, rcept_dt, rm
```

- 접수번호는 14자리이며 DART catalyst identity는 `opendart://disclosure/<rcept_no>`다.
- 고유번호는 8자리, 상장 종목코드는 빈 문자열 또는 6자리다.
- `rcept_dt`는 유효한 `YYYYMMDD`인지 검증하지만 시각이 없으므로 `published_at`을 임의의 자정으로 만들지 않고 `None`으로 둔다.
- 개별 catalyst payload는 공식 disclosure object의 필드를 누락하거나 별칭으로 바꾸지 않은 canonical UTF-8 JSON이다.
- `publisher_id`는 공시 제출 회사의 `corp_code`, `first_observed_at`은 receipt의 실제 수신시각이다.
- keyword baseline은 공식 `report_nm`과 `corp_name`을 명시적인 DART text field로 추가한다. nested 임의 순회는 계속 금지한다.

첫 페이지가 선언한 `page_count`, `total_count`, `total_page`는 모든 페이지에서 같아야 한다. 수집 중 값이 달라지거나 전체 list 수가 `total_count`와 다르거나 접수번호가 중복되면 source run을 실패로 종결하고 새 cycle에서 다시 수집한다.

## 7. 수집 상태기계

```text
final source run 존재 -> network 없이 저장 결과 반환
final source run 없음
  -> page GET
  -> raw receipt append
  -> parse/status/pagination validation
  -> disclosure catalyst + observation + receipt link append
  -> 다음 page
  -> exact count/lineage 재검증
  -> terminal source run append
```

transport failure처럼 response가 없는 실패는 receipt 없이 실패 run을 남긴다. HTTP/API/schema/pagination 실패는 받은 raw receipt와 이미 저장한 catalyst를 보존하고 실패 run을 남긴다. 동일 cycle의 terminal 실패를 자동 재시도하거나 성공으로 바꾸지 않으며 새 cycle ID가 필요하다.

DART source run만으로 기존 `KrCatalystCollectionCycle`을 확정하지 않는다. 후속 multi-source coordinator가 네 source run을 모두 읽고 exact coverage가 맞을 때만 최종 cycle을 append한다.

## 8. CLI와 fixture QA

`run_opendart_collect.py`는 cycle ID, KST collection date, database와 output directory를 명시적으로 받는다.

- production mode: mode-600 secret을 읽고 hard-coded official endpoint에 GET한다.
- fixture mode: path-contained fixture manifest의 raw page bytes와 고정 수신시각을 사용하며 secret과 network를 사용하지 않는다.
- fixture와 production 입력을 동시에 지정하면 거부한다.
- 보고서는 source status, page/record/new-row count와 failure code만 기록한다.
- 회사명, 보고서명, 접수번호, 원문 hash, API message, URL/query와 자격증명은 출력하지 않는다.

CLI가 실패 source run을 정상적으로 원장에 보존한 경우에도 nonzero로 종료한다. 재실행은 terminal source run을 읽어 network나 새 row 없이 같은 aggregate 결과를 반환한다.

## 9. 검증

- config: mode, symlink, 키 길이, 중복·추가 설정, repr redaction
- HTTP: exact host/path/method/query, redirect 금지, URL·키 비노출 오류
- parser: `000`, `013`, 공식 필드, malformed JSON, unknown/known API 오류
- pagination: 다중 page, metadata drift, duplicate 접수번호, page cap
- ledger: v1->v2 migration, raw receipt 우선, checksum, observation lineage, append-only trigger, restart conflict
- collector: success, no-data success, partial failure 보존, terminal-run restart no-network
- CLI: `--help`, bad input, fixture happy path, report redaction, DB mode `600`
- 전체 pytest, Ruff, basedpyright

실제 OpenDART API, LLM, KIS/Alpaca, broker와 외부 메시지 호출은 이 체크포인트 QA에서 0건이어야 한다.

## 10. 후속 범위

1. 네 source run을 exact `KrCatalystCollectionCycle`로 확정하는 coordinator
2. production 뉴스 read-only adapter
3. KIS 국내 랭킹과 canonical volume-surge adapter
4. configured LLM 분류와 keyword/human audit 비교
5. KR quote·VI·가격제한·경고·거래정지 gate와 day shadow

한국 계좌·잔고·포지션·주문 endpoint는 계속 범위 밖이다.
