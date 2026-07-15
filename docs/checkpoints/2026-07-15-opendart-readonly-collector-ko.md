# OpenDART Read-Only Catalyst Collector 체크포인트

## 범위

다중 시장 Research OS Milestone 3과 KR Theme Phase T0의 첫 production source adapter로 공식 OpenDART 공시검색 read-only 경로를 추가했다.

```text
GET /api/list.json
-> exact response receipt append
-> strict status/page/disclosure parse
-> disclosure별 canonical DART catalyst
-> observation-receipt lineage
-> terminal DART source run
```

이번 검증은 committed fixture와 injected transport만 사용했다. 실제 OpenDART API 요청, 뉴스·KIS 국내 수집, LLM, 현재가, KR 위험 gate, shadow fill, 외부 메시지와 broker 호출은 0건이다. 국내 계좌·잔고·포지션·주문 코드는 추가하지 않았다.

## 공식 계약

[OpenDART 공시검색 개발가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001)를 기준으로 다음을 고정했다.

- exact base URL: `https://opendart.fss.or.kr`
- exact method/path: `GET /api/list.json`
- 당일 `bgn_de=end_de`, 접수일 오름차순, `page_count=100`
- redirect 금지, 최대 100 page
- `000`: 성공
- `013`: 조회 결과 없음이므로 성공 0건
- 다른 API status, HTTP 오류, content type·JSON·필드·pagination 불일치: 실패

API message, URL/query, header와 key는 예외·terminal·보고서에 출력하지 않는다.

## 구현

### 비밀과 HTTP 경계

- 기본 secret: `~/.config/trading-agent/opendart.env`
- 설정: `OPENDART_API_KEY` 정확히 하나
- current-user regular file, symlink 금지, exact mode `600`
- 공백 없는 ASCII 40자 검증
- credential과 raw response bytes는 repr에서 제외
- production client는 고정 endpoint와 `follow_redirects=False`를 생성·재검증
- public adapter는 `fetch_page` GET만 노출

### KR ledger schema v2

기존 v1 행과 네 표를 재작성하지 않고 다음 append-only 표를 추가했다.

- `kr_source_receipts`: exact HTTP response BLOB, checksum, 수신시각, nonsecret request key
- `kr_catalyst_observation_receipts`: cycle observation과 receipt item index의 lineage
- `kr_source_collection_runs`: exact receipt 집합·observation count를 재검증한 terminal source 결과

모든 새 표도 UPDATE/DELETE trigger가 보호한다. Writer는 v1 DB를 한 lease에서 v2로 migration하고 기존 catalyst를 그대로 보존한다. Reader는 receipt BLOB checksum, disclosure payload checksum, source 일치와 causal receipt/observation 시각을 다시 검증한다.

### Raw-first OpenDART collection

수집기는 응답 bytes를 receipt에 commit한 뒤에만 parser를 호출한다. 개별 공시는 공식 필드 전체를 canonical UTF-8 JSON으로 보존하고 `opendart://disclosure/<rcept_no>` identity, `corp_code` publisher와 receipt 수신시각을 사용한다. 접수일은 날짜만 제공되므로 임의 자정 timestamp를 만들지 않고 `published_at=None`으로 둔다.

첫 페이지의 `page_count`, `total_count`, `total_page`가 이후 페이지에서 바뀌거나 접수번호가 중복되거나 총 row 수가 다르면 실패 source run으로 종결한다. 받은 receipt와 이미 연결한 observation은 삭제하지 않는다. terminal run 재실행은 저장 결과를 읽고 fetcher를 호출하지 않는다.

기존 schema v1 DB에서도 collector가 reader lookup 전에 Writer migration을 열도록 회귀를 고정했다. `rcept_dt`는 parse 가능 여부뿐 아니라 exact 8자리인지 검증한다.

### Fixture와 CLI

`run_opendart_collect.py`는 cycle ID, KST 날짜, database와 output directory를 명시적으로 받는다.

- fixture mode는 path-contained manifest의 raw page를 모두 먼저 읽고 credential/network를 사용하지 않는다.
- production mode는 fixture와 secret path를 함께 받지 않으며 공식 endpoint만 사용한다.
- source run 실패도 원장과 aggregate 보고서에 보존한 뒤 nonzero로 종료한다.
- 보고서는 상태·건수·failure code만 포함하고 회사명·보고서명·접수번호·hash·provider message를 포함하지 않는다.
- DART source run 하나만으로 네 source `KrCatalystCollectionCycle`을 확정하지 않는다.

## 검증

- 전체 pytest: `1155 passed`
- OpenDART·KR source/ledger/keyword/projection focused: `110 passed` 이후 migration/date 회귀 추가 통과
- Ruff 전체: 통과
- basedpyright 전체: 오류 0, 경고 0
- `./run_opendart_collect.py --help`: exit 0
- invalid date: exit 2, DB 생성 없음
- committed fixture 첫 실행: receipt 1, catalyst 2, observation link 2, source run 1 success
- 같은 cycle 재실행: 신규 receipt 0, 신규 catalyst 0, fetch no-op
- DB와 aggregate report mode: `600`
- 보고서에서 fixture 회사명·보고서명·접수번호·원문 marker·credential setting 비노출
- 실제 OpenDART/KIS/Alpaca/LLM/외부 메시지 호출: 0
- broker·계좌·주문 mutation: 0

fixture 결과는 OpenDART 운영 가용성, 테마 분류 정확도, 추천 품질 또는 수익성 증거가 아니다.

## 커밋

- `a94b474 feat: add KR source evidence contracts`
- `c4bbf89 feat: preserve KR source response lineage`
- `971252f feat: add guarded OpenDART read client`
- `0185e16 feat: collect OpenDART catalysts raw first`
- `0baeb4b feat: add OpenDART collection CLI`
- `7d0fb99 fix: migrate OpenDART collector ledgers`

## 다음 단계

1. source run 네 개를 exact coverage로 확정하는 multi-source cycle coordinator
2. production news read-only adapter
3. KIS 국내 랭킹과 canonical volume-surge adapter
4. configured LLM 분류와 keyword stability·human audit 비교
5. KR quote·VI·가격제한·경고·거래정지 gate와 day shadow

한국 execution은 계속 설계 범위 밖이다.
