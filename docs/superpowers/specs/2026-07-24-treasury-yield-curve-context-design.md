# Treasury Yield Curve Context 설계

## 목적

Milestone 6 Market Context Agent의 첫 macro source로 미국 재무부 공식 Daily
Treasury Par Yield Curve XML feed를 raw-first 수집하고, 한 기준일까지 알려진 최신
두 곡선을 immutable context로 만든다.

이 vertical은 인증키, broker, account, order, 추천, strategy lifecycle과
allocation을 열지 않는다. 공식 HTTPS GET, private append-only receipt/run,
strict XML parser와 content-addressed aggregate context만 추가한다.

## source와 request 경계

공식 문서가 명시한 다음 endpoint와 parameter만 허용한다.

```text
https://home.treasury.gov
/resource-center/data-chart-center/interest-rates/pages/xml
data=daily_treasury_yield_curve
field_tdr_date_value_month=YYYYMM
```

request는 collection ID, `through_date` 하나를 받는다. query month는
`through_date`의 UTC calendar month에서만 계산하며 arbitrary URL, page, year,
header 또는 credential 입력은 받지 않는다. redirect, non-HTTPS, 다른 host/path,
1 MiB 초과 response는 receipt 전에 transport failure로 닫는다.

HTTP status와 content type을 포함한 wire bytes는 parser보다 먼저 mode-600
append-only SQLite에 기록한다. exact terminal replay는 fixture와 network를 열지
않는다.

## parser와 context

namespace-aware XML parser는 Atom `feed/entry/content/m:properties`만 읽고 다음
필드를 정확히 요구한다.

```text
NEW_DATE
BC_1MONTH
BC_1_5MONTH
BC_2MONTH
BC_3MONTH
BC_4MONTH
BC_6MONTH
BC_1YEAR
BC_2YEAR
BC_3YEAR
BC_5YEAR
BC_7YEAR
BC_10YEAR
BC_20YEAR
BC_30YEAR
```

각 properties에는 공식 feed metadata `Id`도 정확히 하나 있어야 하고
`BC_30YEARDISPLAY`가 있으면 `BC_30YEAR`와 같아야 한다. 그 밖의 property는
schema drift로 차단한다.

날짜는 timezone 없는 midnight ISO datetime이어야 하고 중복될 수 없다. 각 yield는
finite decimal percent이며 `-5 <= value <= 25` 범위여야 한다. parser는
`date <= through_date`인 row만 사용해 날짜 역순 최신 두 row를 선택한다. 최신 row가
request month 밖이거나 두 row가 없거나 unknown/missing/duplicate property가 있으면
fail-closed한다.

context는 최신·이전 날짜, 14개 maturity의 현재/이전 yield와 basis-point 변화,
`10Y-2Y`, `10Y-3M`, `30Y-5Y` 현재 slope를 보존한다. slope는 입력 yield 차이에서
exact decimal bps로 계산하며 caller 숫자를 받지 않는다. feed observation은 raw
response `received_at`이고 미래 as-of를 제조하지 않는다.

## 영속화와 출력

store는 request payload, raw receipt ID/SHA, terminal run canonical bytes/SHA를
읽을 때마다 다시 검증한다. transport failure는 receipt 없이 terminal failed,
HTTP/structure failure는 raw receipt를 보존한 failed terminal이다.

성공 context는 canonical JSON SHA-256을 context ID로 사용해 다음 mode-600
immutable file로 발행한다.

```text
treasury_yield_curve_context_<context-id>.json
```

aggregate report에는 날짜, maturity count, curve count, artifact created,
network/replay와 mutation 0만 기록한다. yield 수치, receipt ID, raw XML과 로컬
경로는 기록하지 않는다.

## 검증과 한계

TDD는 strict latest-two parse, missing/duplicate/unknown property, future row,
raw-before-parse failure와 replay no-fetch를 먼저 고정한다. CLI는 help, bad date,
fixture happy/replay를 검증한 뒤 공식 endpoint actual GET과 nonexistent fixture
replay를 수행한다.

Treasury CMT는 공식 일별 par yield curve이고 intraday tradable quote나 거래 체결이
아니다. 한 달 bounded context는 장기 revision history, real yield, bill rate,
credit/liquidity, strategy performance 또는 Paper authority를 뜻하지 않는다.
