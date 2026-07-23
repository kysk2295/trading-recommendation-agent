# M6 Treasury Yield Curve Context 체크포인트

작성일: 2026-07-24 KST

## 판정

Market Context Agent의 첫 공식 macro source로 미국 재무부 Daily Treasury Par
Yield Curve XML feed raw-first vertical을 완료했다.

- actual collection 구현 SHA:
  `4bd7361819445afc61282fecb3fe3c97c22f9a82`
- terminal replay provenance fix SHA:
  `eb2663d32a965b410e1aceca65f35c76a4301777`

이 context는 미국 재무부가 공식 게시한 일별 CMT par curve의 최신 두 관측을
보존한다. intraday tradable quote, 거래 체결, 추천 또는 주문 권한이 아니다.

## 공식 source와 fail-closed 경계

공식 Treasury 문서가 명시한 base URL, endpoint와 data key만 사용한다.

```text
https://home.treasury.gov
/resource-center/data-chart-center/interest-rates/pages/xml
data=daily_treasury_yield_curve
field_tdr_date_value_month=YYYYMM
```

month는 `through_date`에서만 계산한다. arbitrary URL, page, header, credential
입력은 없고 redirect, 다른 final origin/path, invalid content length와 1 MiB 초과
response는 transport failure로 닫는다.

HTTP status와 raw XML은 parser보다 먼저 private append-only SQLite receipt에
확정한다. strict parser는 Atom, data-service와 metadata namespace를 고정하고
properties의 `Id`, `NEW_DATE`, 14개 CMT maturity를 정확히 요구한다. unknown,
missing, duplicate, 다른 달, 미래-only, non-midnight date, non-finite와
`-5%..25%` 밖 yield는 artifact 전에 차단한다. optional 30-year display 값은
30-year source 값과 같아야 한다.

output context는 최신·이전 날짜, 14개 current/previous/change-bps와 입력에서
계산한 `10Y-2Y`, `10Y-3M`, `30Y-5Y` slope를 보존한다. aggregate report에는
yield 수치, raw XML, receipt ID와 로컬 경로를 쓰지 않는다.

## actual GET와 replay

2026-07-24 KST에 `through_date=2026-07-24`로 공식 current-month endpoint를
한 번 GET했다.

- HTTP: `200`
- content type: `text/xml`
- raw bytes: `23,848`
- raw payload SHA-256:
  `65665baeac401819e0e91527e3dd2b0543ff9a8a67b6bf3358e32f52ef82daee`
- request ID:
  `5a1a7ca3333e24324bcf2fdbb4cebc3523aeda279d99c3903a7b16792ed81ce7`
- raw receipt ID:
  `bb67908e74afeb58f9d4155c0d2829fab7ef5f19df52a744edffc60f19fc2fcc`
- run ID:
  `e54c2d67493f8c73fa1853099f2f74451fc17412c4d02eb5a8f45c02e1050207`
- latest/previous curve date: `2026-07-22` / `2026-07-21`
- maturity count: `14`
- current slopes: `10Y-2Y 36 bps`, `10Y-3M 78 bps`, `30Y-5Y 74 bps`
- context ID:
  `3fc449d92749b6182aa429fa7baae6bc4a593097bf05ea1a89609b42630cb3cd`
- context artifact SHA-256:
  `c4b0ab0197bdc088d67ca801b888506e1838976ee815361a3f2ec35273c7e36a`
- store/context/report mode: 모두 `600`

첫 실행은 receipt/run `1/1`, artifact created `yes`였다. nonexistent fixture를
지정한 replay는 stored terminal을 먼저 읽어 fixture와 network를 열지 않았고
receipt/run `1/1`, artifact created `no`, context SHA 동일이었다. replay report의
provider operation도 `stored terminal query-only`로 기록한다.

credential, broker, account, order와 allocation mutation은 모두 `0`이다.

## 다음 공식 게시 후 one-shot

최초 actual GET의 최신 curve가 `2026-07-22`였으므로 미국 재무부의 다음 일별
게시를 검증하도록 `2026-07-24 07:05 KST` one-shot을 등록했다.

- label: `ai.trading-agent.treasury-yield-close-20260723`
- frozen runtime SHA:
  `eb2663d32a965b410e1aceca65f35c76a4301777`
- collection ID: `treasury-yield-close-20260723`
- through date: `2026-07-23`
- wrapper mode: `700`
- stdout/stderr mode: `600`
- 예약 직후 receipt: 아직 없음

wrapper는 원자적 claim과 receipt를 사용하고 완료 뒤 자기 launchd label을 제거한다.
따라서 이전 KR finalizer처럼 반복 실행하거나 최신 aggregate를 덮어쓰는 예약이 아니다.
실행 결과가 성공인지, 아직 새 곡선이 없는지, provider 실패인지는 07:05 이후 receipt와
private artifact를 다시 검증해 확정한다.

## 다음 공식 게시 one-shot 실측

예약 작업은 2026-07-24 07:05:11 KST에 exit `0`으로 끝났고 mode-600 atomic
receipt를 남긴 뒤 launchd label을 제거했다. stdout은 성공 한 줄, stderr는
0 bytes였으며 반복 실행은 없었다.

- HTTP: `200`
- raw bytes: `25,393`
- raw payload SHA-256:
  `fe59c184dc465c7c5438b90bb5fb51626005647f5f81607d163694ae44c01287`
- request ID:
  `4bf700fe52d75e1dda50277aabd85b747315222a22d8330e6354a14c31ea29e9`
- raw receipt ID:
  `001fbae46b6275fe4c6054032a94136cedba3b28170af4b916a2f51751c6e57a`
- run ID:
  `811627d6f9adbb84cbd4b69c98c3613e46aab1b0eb244399c598ebb5fd36d44b`
- latest/previous curve date: `2026-07-23` / `2026-07-22`
- maturity count: `14`
- context ID:
  `f7bf3a814e71c8f3c62864a22c431947c2c5c04c67d95f5b1a66c53eb3b36e03`
- persisted artifact SHA-256:
  `a1af4b879a9cdac55cebc0ae8636bc900531c00737712b9c1141e4d8229a3ea9`
- raw receipt / terminal run: `1/1`

같은 frozen runtime에 nonexistent fixture를 지정한 별도 output replay는 source를
열지 않고 `replayed terminal: true`, `network access: 0`, `stored terminal
query-only`로 끝났다. replay artifact의 content SHA는 위 persisted artifact와
같았고 원본 DB의 receipt/run은 `1/1`을 유지했다. 공식 GET과 replay 모두
credential, broker, account, order와 allocation mutation은 `0`이다.

## 검증

- parser/store/client/artifact/CLI 신규: `14 passed`
- Treasury/CFTC/futures 인접 회귀: `44 passed`
- 전체 pytest: `3554 passed in 220.47s`
- 전체 Ruff: 통과
- 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- compileall, changed-file format, diff check: 통과
- no-excuse production grep: 위반 `0`
- production 최대 pure LOC: store `221`, models `205`, 나머지 `156` 이하
- CLI help/bad date/fixture happy/replay/actual/replay: `0/2/0/0/0/0`

## 한계와 다음 경계

한 달 bounded official nominal par curve이고 Treasury revision의 전체 장기 이력,
real yield, bill rate, credit/liquidity와 intraday price를 포함하지 않는다. 이
context를 strategy regime에 사용하려면 별도 immutable strategy version과 같은
기간·위험의 baseline/challenger 비교가 먼저다.

M6의 다음 큰 source dependency는 licensed current CME/ICE futures roll master다.
그 전에도 이 macro context는 독립 Market Context evidence로만 유지하며 derivatives
strategy, Paper champion이나 Allocation Manager를 자동 활성화하지 않는다.
