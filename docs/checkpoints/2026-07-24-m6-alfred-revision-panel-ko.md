# M6 ALFRED multi-vintage revision panel 체크포인트

상태: **query-only point-in-time panel과 exact READY data foundation actual 검증 완료**

기능 커밋:
`42a42f8671407425e9e1249ac1aebbc60f937cb3`

data foundation 커밋:
`dd0036825d10cd166d6919d945abf7f5066b3504`

## 제품 경계

검증된 ALFRED snapshot 2~100개를 외부 재조회 없이 읽어 하나의
content-addressed revision panel로 만든다.

- 입력 파일은 mode `0600` private immutable file이어야 한다.
- 파일명은 snapshot의 semantic ID와 정확히 일치해야 하고 본문도 canonical
  JSON과 byte-exact로 같아야 한다.
- 모든 입력은 `alfred` mode, 동일 series·units·관측 범위여야 한다.
- vintage date와 snapshot ID는 중복될 수 없다.
- 관측일이 해당 vintage보다 미래면 panel 전체를 차단한다.
- vintage 순서는 입력 순서와 무관하게 날짜 오름차순으로 고정한다.
- 각 관측일과 vintage의 cell은 `not_observed`, `missing`, `available`을
  구분한다. 최신값으로 과거 `not_observed`나 `missing`을 채우지 않는다.
- revision은 직전 available vintage 값과 현재 available 값의 exact Decimal
  차이이며 panel validator가 다시 계산해 대사한다.

panel은 source snapshot ID, vintage date, 관측 범위와 모든 cell을 포함한 canonical
bytes의 SHA-256을 semantic ID로 사용한다. 동일 입력의 순서를 바꿔 재실행해도 같은
artifact를 재생한다. 이 경로에는 credential, provider, hypothesis, strategy,
trial, Reviewer, lifecycle, broker, account, order 또는 allocation mutation이 없다.

## actual ALFRED DFF 증거

기존 2026-07-23 vintage DFF snapshot과 같은 관측 범위의 2026-07-22 vintage를
공식 ALFRED GET으로 새로 수집했다.

- observation range: `2026-07-01..2026-07-22`
- 2026-07-22 vintage:
  - first official GET/network: `1/1`
  - observations: `21`, missing `0`
  - capability: `complete`, completeness `10000 bps`
  - snapshot ID:
    `83790eb45ba7e6293d4d78f65ec3eb7d502e64792fbd09297a16eb2b6a7911ea`
  - snapshot file SHA-256:
    `0963c7414f6a7272b456d6b3d3ec49591f5944fcfde5c5adb151badf3fd87124`
  - missing credential exact replay: network `0`, artifact created `no`
- 2026-07-23 vintage:
  - observations: `22`, missing `0`
  - snapshot ID:
    `08fb874aa01d8da343b6fce25299ee8df37bbc253a9bfc23802b89ce7149a123`
  - snapshot file SHA-256:
    `3c29084c9b0c2c8b531abcfeb9d1482df8074ad38dab87db7df7ae8713db773c`

exact committed runtime의 panel 결과:

- vintage count: `2`
- observation rows: `22`
- comparable revisions: `21`
- changed revisions: `0`
- `not_observed` release 차이: 보존
- panel ID:
  `f531cb32bd53b2b7e413055ae8b5b37e44b778657aa8ec768ed08c7585ad830a`
- panel file SHA-256:
  `a82aa22197a436af13df6790bc552bedda3b772f5d7e06cefc5858850eb5bea6`
- first/replay artifact created: `yes/no`
- replay provider/credential access: `0/0`
- evidence files: `11`, mode `0600` 아닌 파일 `0`
- credential 값이 evidence에 존재한 파일: `0`

`changed revisions=0`은 이 두 DFF vintage의 비교 결과이며 revision logic이나 다른
series의 무수정을 일반화하지 않는다. 두 번째 vintage에서 관측 하나가 새로 등장한
release availability 차이는 revision 값으로 오분류하지 않고 `not_observed →
available`로 보존했다.

## TDD·검증

- RED: revision panel module 부재로 collection error
- focused ALFRED panel·FRED/ALFRED regression: `6 passed`
- full pytest: `3649 passed in 234.96s`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- CLI isolated `--help`: exit `0`
- single-snapshot bad input: exit `2`, output 생성 `0`
- fixture happy/replay: exit `0/0`, artifact `yes/no`
- actual happy/replay: exit `0/0`, artifact `yes/no`

이 panel은 point-in-time 연구 입력 증거다. release calendar의 공식 발표시각,
다중 series macro regime, 전략 성과, champion 또는 주문 권한은 아직 만들지
않는다. 다음 운영 우선순위는 예약된 실제 forward의 clean strict closeout과 causal
dataset·READY v2·독립 Reviewer 결과 검증이다.

## FRED change calendar와 READY admission

기능 커밋:
`7538e8b360f2fe36c2bb84f366e8f7482c0195a5`

공식
[`fred/series/vintagedates`](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html)
GET을 별도 raw-first capability로 연결했다. 이 endpoint는 해당 series 값이 새로
발표되거나 수정된 날짜만 반환하며, 데이터가 바뀌지 않은 release date와 미래
no-data release date는 포함하지 않는다.

- request identity: series, realtime start/end, limit, ascending sort
- raw receipt: HTTP bytes를 parser 전에 mode `0600` immutable file로 확정
- strict response: exact realtime range, `order_by=vintage_date`,
  `sort_order=asc`, offset `0`, count·limit·정렬·중복·범위 대사
- capability: `fred/series_vintage_dates`,
  `global_macro/macro_release_date`, historical research·shadow forward
- exact terminal replay: credential과 network를 열기 전에 receipt를 재투영

actual DFF 결과:

- realtime range: `2026-07-01..2026-07-24`
- first official GET/network: `1/1`
- vintage date count: `16`
- capability: `complete`, completeness `10000 bps`
- calendar snapshot ID:
  `35a8a445595df1f59507e55f61773edc01352253b23001c5eb06dccffe299500`
- calendar file SHA-256:
  `0a8ae82da697cd5aa1fe5a7299616d5fe32c24accde82ec6c295bc4150533657`
- missing credential exact replay: network `0`, artifact created `no`

별도 query-only release gate는 canonical panel 파일과 calendar 파일을 다시 읽어
파일명·본문·semantic ID, series 일치와 panel vintage 전체가 official change
calendar에 포함되는지를 검증한다. 하나라도 빠지면 READY artifact를 만들지 않는다.

- admitted vintage: `2026-07-22`, `2026-07-23`
- admitted count: `2/2`
- exact panel ID:
  `f531cb32bd53b2b7e413055ae8b5b37e44b778657aa8ec768ed08c7585ad830a`
- exact calendar ID:
  `35a8a445595df1f59507e55f61773edc01352253b23001c5eb06dccffe299500`
- assessment ID:
  `26ed19eb3bf8b82ee5311cc329ee4fca230e0c1aca224facd374290ea2cc813a`
- assessment file SHA-256:
  `1a6ab2adcb52ef2dacd080ff13b307f7fa01c1f270818662f12306f3ed271cd7`
- first/replay artifact created: `yes/no`
- replay network/credential access: `0/0`
- calendar/assessment evidence file: `8/3`, mode `0600` 아닌 파일 `0`
- credential 값이 관련 evidence에 존재한 파일: `0`

추가 TDD·검증:

- RED: vintage dates CLI와 release gate module 부재
- focused FRED·ALFRED panel·calendar·gate: `10 passed`
- full pytest: `3653 passed in 235.15s`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- 두 isolated CLI `--help`: exit `0`
- invalid range/missing calendar: exit `2`
- actual calendar/gate happy·replay: exit `0/0`, artifact `yes/no`

이 admission은 날짜 단위 release-or-revision causality를 증명한다. source가 공표한
장중 시각이나 FRED 웹사이트에 실제 반영된 초 단위 시각은 증명하지 않으므로 intraday
announcement 전략 입력으로 해석하지 않는다. broker·account·order·lifecycle과
allocation mutation은 `0`이다.

## exact Macro Revision Data Foundation

generic capability READY만으로는 실제 연구에 사용한 panel·calendar·admission 파일의
bytes를 식별할 수 없으므로 세 파일 SHA와 세 source capability를 하나의
content-addressed foundation에 결합했다.

- strategy lane: `us_equities/market_context/macro_revision_context`
- requirement `3/3 READY`:
  - `fred/series_observations` 최신 macro observation
  - `alfred/vintage_observations` point-in-time macro observation
  - `fred/series_vintage_dates` 공식 release-or-revision date
- exact panel file SHA-256:
  `a82aa22197a436af13df6790bc552bedda3b772f5d7e06cefc5858850eb5bea6`
- exact calendar file SHA-256:
  `0a8ae82da697cd5aa1fe5a7299616d5fe32c24accde82ec6c295bc4150533657`
- exact admission file SHA-256:
  `1a6ab2adcb52ef2dacd080ff13b307f7fa01c1f270818662f12306f3ed271cd7`
- foundation ID:
  `13e1b5343dfcc1a3efe00d54ffa26c8e0c03c65faa30819c3b91d586080c8ca5`
- foundation file SHA-256:
  `bb648cd77011d90ed7b168f58a4bf85fdd979911dbcc3673209413667b598236`
- first/exact committed replay artifact created: `yes/no`
- replay provider network/credential access: `0/0`
- foundation/report mode: `0600/0600`
- broker·account·order·lifecycle·allocation mutation: `0`

TDD는 foundation module 부재 RED 뒤 exact identity와 SHA 위조 차단을 GREEN으로
만들었다. 관련 회귀 `11 passed`, 전체 `3654 passed in 240.25s`, 전체 Ruff와
basedpyright `0 errors, 0 warnings, 0 notes`가 통과했다. CLI `--help`, missing
assessment bad input exit `2`·output `0`, actual happy/replay `yes/no`도 직접
검증했다.

이 foundation은 실제 DFF 두 vintage에 대한 재현 가능한 macro research 입력이다.
다중 macro series의 regime 효과, 전략 성과, champion 또는 Paper 권한을 증명하지
않는다.
