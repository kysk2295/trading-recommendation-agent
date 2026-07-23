# M6 CFTC TFF positioning context 체크포인트

## 제품 경계

공식 CFTC Public Reporting Environment의 Traders in Financial Futures
futures-only dataset을 하나의 contract market code와 기준일로 제한해 조회하고,
최신 두 주간 raw response에서 결정적인 positioning context를 만드는 vertical을
추가했다.

```text
official CFTC bounded GET
-> raw response append-only receipt
-> exact two-report identity/date/open-interest reconciliation
-> five-category net positioning + weekly change
-> content-addressed mode-600 context
-> aggregate-only report
```

dealer, asset manager, leveraged money, other reportable, nonreportable 다섯
범주의 current/previous net, weekly change와 current open-interest 대비 net bps를
원시 정수 position에서 계산한다. CFTC report date는 포지션 기준일로 보존하고,
우리 시스템의 실제 response receipt 시각을 별도 `observed_at`으로 사용한다.

고정 HTTPS origin과 TFF futures-only dataset, exact field selection, 최신 두 행
내림차순, 1 MiB response 상한과 no-redirect를 강제한다. HTTP status를 포함한 raw
bytes는 parser보다 먼저 private append-only SQLite에 확정한다. market identity,
report kind·날짜·단위, 음수 position, 중복 report와 long/short open-interest
reconciliation이 하나라도 맞지 않으면 artifact를 게시하지 않는다.

동일 request의 terminal이 있으면 fixture나 HTTP client를 열기 전에 저장 결과를
재생한다. 이 경로에는 credential, broker, account, order나 allocation API가 없다.

## TDD와 검증

- parser identity/date/position reconciliation: `7 passed`
- raw-before-parse terminal과 exact replay: `2 passed`
- fixed client origin/query/redirect/size: `5 passed`
- CLI fixture happy/replay와 bad market code: `2 passed`
- CFTC와 futures roll 관련 회귀: `27 passed`
- 전체 pytest: `3523 passed`
- Ruff 전체: pass
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- compileall: pass
- changed-file no-excuse pattern 위반: `0`

수동 CLI는 `--help` exit `0`, invalid market code exit `2`와 state 생성 `0`,
canonical private fixture happy exit `0`을 확인했다. macOS `/var` symlink alias
아래 private store는 query 단계에서 차단됐고 물리 경로 `/private/var`에서는
정상 동작해 private path 경계를 유지했다.

## Actual CFTC GET과 exact replay

2026-07-24 02:44 KST에 자격증명 없이 ES CFTC contract market code `13874A`를
`through_date=2026-07-24`로 조회했다. 공식 응답은 최신 report date
`2026-07-14`와 직전 주를 포함한 정확히 두 행이었고 다섯 category
reconciliation을 통과했다.

- 최초 actual CLI: exit `0`, artifact created `yes`
- exact replay CLI: exit `0`, artifact created `no`
- receipt/run row: `1/1`
- replay network access: `0`
- raw payload SHA-256:
  `f9284ccf996bf0b87bef02428364046415d38fb832dc2fc3416583c678c606e1`
- receipt ID:
  `011f225abf239daa81f4e74936cb774cd3d73aa447c3b136207622f20a6ce2e9`
- context ID:
  `2204fd0de65cb76f138c5f6384db40c64b9ae20b7d516f5b2a8691fc88a20346`
- artifact SHA-256:
  `318619090a28443c33f49771b33c2fab2bdec9ec440eb75dd1b293eb2d19d10d`
- database/artifact/report mode: `600/600/600`
- credential use: `0`
- broker, account, order, allocation mutation: `0`

공개 report는 status, 날짜, row/category 수, replay와 network 집계만 노출하며
개별 포지션과 raw payload, 로컬 경로는 기록하지 않는다.

## 제한

이 증거는 CFTC가 게시한 market-level weekly aggregate 두 시점의 실제 source
availability와 인과적 receipt/replay 계약을 검증한다. 계약 월별 포지션, 현재
futures security master, licensed CME·ICE price/settlement, basis·curve, intraday
시장 상태나 파생상품 전략 성과를 뜻하지 않는다.

CFTC 수정 이력의 장기 수집, 다중 market capability/coverage, futures roll master와
정확한 as-of 결합, volatility context 및 derivatives shadow strategy agent는 후속
milestone이다. Paper champion이나 주문 권한은 생성하지 않았고 Allocation Manager도
계속 비활성이다.
