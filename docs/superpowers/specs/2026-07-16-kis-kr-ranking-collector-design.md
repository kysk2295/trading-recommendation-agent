# KIS 국내 랭킹 Read-Only Collector 설계

## 목표

KR 테마 lane의 미구현 `kis_ranking` source를 실제 한국투자증권 국내주식 시세 GET에 연결한다. 현재 KST 날짜의 KRX 등락률 순위와 거래량 순위를 raw-first로 수집하고, 검증된 각 종목 행을 기존 mode-600 append-only KR 원장의 receipt, catalyst observation, terminal source run으로 보존한다.

이 단계는 종목 발굴 evidence만 만든다. `volume_surge` 파생, 현재 호가, 분봉, VI/거래정지, TradeSignal, shadow fill, 계좌, 잔고와 주문은 범위 밖이다.

## 선택한 접근

기존 미국장 랭킹 CSV를 KR 원장으로 복사하는 방식은 source receipt와 item lineage가 없어 채택하지 않는다. KIS 응답을 곧바로 Opportunity로 투영하는 방식도 source 실패와 분류 실패를 구분할 수 없어 채택하지 않는다.

별도 read-only adapter가 KIS 원문 응답을 먼저 원장에 확정하고, 그 receipt에서만 canonical 종목 행을 파싱한다. 이 방식은 기존 OpenDART와 LS NWS collector의 raw-first 계약을 재사용하며, 다음 milestone의 `volume_surge`가 immutable `kis_ranking` evidence만 읽게 한다.

## 공식 API 계약

2026-07-16 현재 한국투자증권 공식 `koreainvestment/open-trading-api` 예제를 기준으로 다음 두 GET만 allow-list에 둔다.

| 종류 | Path | TR ID | 고정 시장 |
|---|---|---|---|
| 등락률 | `/uapi/domestic-stock/v1/ranking/fluctuation` | `FHPST01700000` | `J` (KRX) |
| 거래량 | `/uapi/domestic-stock/v1/quotations/volume-rank` | `FHPST01710000` | `J` (KRX) |

근거:

- [공식 등락률 순위 예제](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/fluctuation/fluctuation.py)
- [공식 거래량 순위 예제](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/volume_rank/volume_rank.py)

production client는 exact `https://openapi.koreainvestment.com:9443` origin과 `follow_redirects=False`만 허용한다. 공개 메서드는 위 두 GET을 enum으로 선택하는 `fetch_page()` 하나뿐이다. URL, TR ID, HTTP method, query key를 CLI나 manifest에서 주입할 수 없다. OAuth token 발급 외 KIS POST와 계좌, 잔고, 포지션, 주문 endpoint는 존재하지 않는다.

첫 버전은 KRX `J`만 수집한다. NXT 또는 통합시장 `UN`을 추가하려면 동일 cycle에서 중복 종목과 시장별 관찰시각을 정의하는 별도 설계가 필요하다.

## 요청과 pagination

두 요청은 공식 예제의 전체시장 순위 파라미터를 코드 상수로 둔다. 사용자는 가격, 거래량, 시장, 정렬 또는 제외 플래그를 바꿀 수 없다.

등락률 query는 공식 check 예제와 같이 다음 값을 사용한다.

```text
fid_cond_mrkt_div_code=J
fid_cond_scr_div_code=20170
fid_input_iscd=0000
fid_rank_sort_cls_code=0
fid_input_cnt_1=0
fid_prc_cls_code=0
fid_input_price_1=
fid_input_price_2=
fid_vol_cnt=
fid_trgt_cls_code=0
fid_trgt_exls_cls_code=0
fid_div_cls_code=0
fid_rsfl_rate1=
fid_rsfl_rate2=
```

거래량 query는 공식 함수 문서의 전체시장 예제와 같이 다음 값을 사용한다.

```text
FID_COND_MRKT_DIV_CODE=J
FID_COND_SCR_DIV_CODE=20171
FID_INPUT_ISCD=0000
FID_DIV_CLS_CODE=0
FID_BLNG_CLS_CODE=0
FID_TRGT_CLS_CODE=111111111
FID_TRGT_EXLS_CLS_CODE=0000000000
FID_INPUT_PRICE_1=0
FID_INPUT_PRICE_2=1000000
FID_VOL_CNT=100000
FID_INPUT_DATE_1=
```

응답 header `tr_cont=M`이면 80ms 뒤 request header `tr_cont=N`으로 다음 page를 요청한다. kind별 최대 10 page를 넘으면 `page_limit_exceeded`로 실패한다.

HTTP 500, 502, 503, 504는 원문 receipt를 먼저 append한 뒤 80ms 후 정확히 한 번 재시도한다. 429, 다른 HTTP status, 두 번째 transient status와 transport error는 재시도하지 않는다. 모든 HTTP 응답은 성공 여부와 무관하게 parse 전에 BLOB receipt가 된다.

## Raw-First 데이터 흐름

```text
current-date preflight
-> fixed KIS GET
-> response bytes + status + normalized content type + continuation metadata
-> KrSourceReceipt append/commit
-> HTTP/content-type/JSON/KIS status/schema 검증
-> item별 canonical payload
-> KrCatalystRecord + KrCatalystObservation + receipt item lineage
-> 두 ranking kind와 pagination 완결 확인
-> KrSourceCollectionRun(source=kis_ranking) terminal append
```

`request_key`에는 kind, page, attempt, request/response continuation만 저장한다. 자격증명, token, query value와 provider message는 저장하지 않는다. payload BLOB은 KIS 응답 bytes 자체이며 canonical item payload와 분리한다.

## Canonical 종목 행

각 검증 행은 schema v1 JSON으로 다음 필드를 가진다.

- `market`: `KRX`
- `ranking_kind`: `fluctuation` 또는 `volume`
- `symbol`: KIS가 반환하는 6자리 대문자 영숫자 단축코드 (`[0-9A-Z]{6}`)
- `name`: canonical 비제어문자 종목명
- `rank`: 1 이상의 정수
- `price_krw`: 0 이상의 Decimal
- `change_pct`: 유한 Decimal
- `accumulated_volume`: 0 이상의 정수
- `prior_day_volume`, `average_volume`, `volume_increase_pct`, `accumulated_trading_value_krw`: 거래량 응답에서만 값이 있고 등락률 응답에서는 `null`

`source_record_id`는 `collection_cycle_id + ranking_kind + symbol`의 cycle-local identity다. 같은 kind에서 종목 중복, rank 중복, malformed number, 빈 종목명과 `[0-9A-Z]{6}`이 아닌 symbol은 전체 source run을 실패시킨다. 다른 kind에 같은 종목이 있는 것은 정상이며 별도 catalyst로 보존한다. 2026-07-16 production smoke에서 거래량 순위 30행 중 7행이 문자를 포함한 공식 6자리 단축코드였으므로 숫자 전용 필터로 폐기하지 않는다.

KIS가 제공하는 순위 데이터에는 발생시각이 없으므로 `published_at=None`으로 두고, 신호 가용시각은 receipt의 timezone-aware `received_at`만 사용한다. 모든 receipt의 KST 날짜가 CLI의 `collection_date`와 같아야 한다.

## 재시작과 실패

동일 cycle에 terminal `kis_ranking` run이 있으면 credential과 network를 열지 않고 exact replay 결과를 반환한다.

terminal run 없이 같은 source run의 receipt가 남아 있으면 변화하는 현재 랭킹을 재조회해 서로 다른 시각을 섞지 않는다. 기존 receipt와 observation만으로 `incomplete_restart` failed run을 확정하고 network 0건으로 종료한다. 운영자는 새 cycle ID로 다시 수집한다.

부분 성공도 숨기지 않는다. 예를 들어 등락률 성공 후 거래량 실패 시 등락률 receipt와 observation을 유지하고 `record_count`를 실제 보존된 observation 수로 기록한 failed run을 append한다. 성공 run은 두 kind가 모두 terminal page에 도달했을 때만 가능하다.

## CLI

`run_kis_kr_ranking_collect.py`는 다음 옵션만 노출한다.

- `--collection-cycle-id`
- `--collection-date`
- `--database`
- `--output-dir`
- `--fixture-manifest`

CLI는 기존 DB의 exact terminal run 또는 orphan receipt를 먼저 local preflight한다. terminal run은 exact replay하고 orphan receipt는 `incomplete_restart` failed run으로 닫으며, 두 경우 모두 날짜 gate, fixture load, credential과 network를 열지 않는다. 신규 production collection은 fixture가 없을 때 자동 선택되며 KIS live 시세 origin과 기존 mode-600 `~/.config/trading-agent/kis.env`만 사용한다. 신규 production의 `collection_date`는 실행 시점 KST 날짜와 정확히 같아야 하며, 이 검사는 DB 생성과 credential load보다 먼저 수행된다. fixture mode는 committed historical synthetic 응답을 허용하고 credential과 network를 사용하지 않는다.

CLI 보고서는 source 상태, receipt/catalyst/observation aggregate count, 재시작 여부와 failure code만 mode 600으로 쓴다. token, header, raw payload, provider message, DB 경로, receipt ID와 checksum은 출력하지 않는다. failed run은 보고서를 쓴 뒤 nonzero로 종료한다.

## 검증 기준

- exact origin/path/TR ID/query/header와 redirect 차단 client test
- raw receipt가 parser보다 먼저 append되는 순서 test
- 두 kind success, zero-row success, pagination, transient 1회 retry test
- 429/no retry, malformed JSON/schema/number, duplicate row/rank, page limit test
- partial failure와 orphan receipt `incomplete_restart` no-network test
- terminal replay no-network test
- fixture path traversal/symlink/manifest mismatch test
- CLI help, invalid ID/date, fixture happy path, fixture failure와 private marker 검사
- targeted pytest, 전체 pytest, Ruff, basedpyright

실제 KIS smoke는 현재 KST 날짜, exact live origin, mode-600 credential과 read-only GET 조건이 모두 맞을 때 두 순위 요청만 bounded하게 수행한다. 결과는 source 가용성 확인일 뿐 추천 품질이나 수익성 증거가 아니다.

## 다음 단계

이 collector가 검증된 뒤 별도 milestone에서 `kis_ranking`의 저장된 거래량 행만 읽어 canonical `KrVolumeSurgePayload` 하나를 만들고 `volume_surge` terminal source run을 확정한다. 현재 `KrVolumeSurgePayload`의 숫자 전용 symbol 계약은 실제 KIS `[0-9A-Z]{6}` 단축코드를 누락하지 않도록 명시적으로 버전 확장한 뒤 사용한다. 두 source를 한 run으로 합치거나 현재 랭킹을 사후 재조회하거나 영숫자 코드를 조용히 폐기하지 않는다.
