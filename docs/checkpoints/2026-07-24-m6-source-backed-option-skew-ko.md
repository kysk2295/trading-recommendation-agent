# M6 source-backed option skew·SIP spot admission 체크포인트

## 제품 결과

READY call/put option surface와 source-backed underlying spot을 같은 시점 경계에서
결합하는 shadow-only option skew vertical을 추가했다.

```text
READY call surface + READY put surface
-> exact underlying/feed/expiration identity
-> Alpaca SIP raw receipt + canonical completed-minute OHLCV
-> matched-strike put IV - call IV
-> pre-registered absolute-delta bucket put IV - call IV
-> content-addressed private skew artifact
```

spot 가격을 CLI 숫자로 직접 입력할 수 없다. private runtime SQLite에 저장된 receipt와
연결된 canonical dataset event를 content hash, instrument, event/provider time과 raw
receipt lineage로 다시 검증한다. runtime canonical payload도 재파싱해 저장된 완료
분봉의 OHLCV와 정확히 일치해야 한다.

## 인과성·품질 경계

- 두 surface는 `READY`, 같은 underlying·feed·expiration이어야 한다.
- surface 파일명과 content hash가 정확히 일치해야 한다.
- runtime SQLite, 부모 디렉터리, report와 artifact는 owner-only private 경계를
  통과해야 한다.
- spot receipt는 bar 완료 뒤에 관측되고 두 surface의 earliest observation보다
  늦지 않아야 한다.
- latest completed bar는 같은 뉴욕 정규장에 속해야 한다.
- call/put surface 관측 skew는 최대 300초다.
- strike bucket은 moneyness `9000–9500`, `9500–10000`, `10000–10500`,
  `10500–11000` bps다.
- absolute-delta bucket은 `.10–.25`, `.25–.40`, `.40–.60`, `.60–.75`,
  `.75–.90`이다.
- strike skew는 matched strike별 `(put IV - call IV)`의 median이다.
- delta skew는 같은 absolute-delta bucket의 put median IV에서 call median IV를
  뺀 값이다.
- strike와 delta bucket이 각각 하나 이상 없으면 발행하지 않는다.

## 구현과 검증

- skew 구현: `dbd42383a301acb1cd5f28216137c52a17c49699`
- bounded SIP spot capture: `065daa19099aab5f91ebd21e410dcdc73a5daee6`
- provider HTTP blocker 보존: `5dccbb26226a53ea092245a27b6db5f3577153a0`

failing-first 테스트는 늦은 receipt, canonical payload와 다른 runtime close, bar 완료 전
receipt와 non-private runtime DB를 실제로 허용하던 경계를 각각 재현한 뒤 차단했다.

- skew focused: `25 passed`
- skew 포함 당시 전체 pytest: `3493 passed`
- spot capture와 관련 경계: `17 passed`
- HTTP blocker 수정 뒤 전체 SIP 관련 suite: `224 passed`
- Ruff: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`
- fixture skew artifact SHA-256:
  `576aa88e3d416c1c8c7f4b6d19c9f9b28d2e1bd355d9c2c122412eb7dbb5e7d0`
- fixture spot capture: 신규 receipt `31`, exact replay 신규 receipt `0`
- artifact, runtime SQLite와 report: mode `600`

## actual 정규장 admission 결과

exact runtime `5dccbb26226a53ea092245a27b6db5f3577153a0`에서
2026-07-23 12:36:23 EDT AAPL completed-minute SIP spot capture를 GET-only로
실행했다.

- result: `blocked_source`
- reason: `sip_access_forbidden`
- provider status: `403`
- stdout: `0` bytes
- report: mode `600`
- credential, response body, 계좌 식별자 출력: `0`
- broker, account, order mutation: `0`
- actual skew artifact: `0`

provider rejection은 generic invalid input으로 숨기지 않고 status code만 보존한 private
운영 report로 닫는다. source quality gate를 IEX로 바꾸거나 spot을 수동 입력하지
않았다.

## actual call/put 입력 surface

같은 exact runtime에서 AAPL 2026-07-24 만기 indicative call/put을 각각 bounded
GET으로 수집하고 option-contract master와 exact identity join했다.

- call/put contract master: 각각 `77`
- call/put chain snapshot: 각각 `77`
- call/put exact identity join: 각각 `77/77`
- call/put snapshot coverage: 각각 `10000 bps`
- call/put IV observations: 각각 `20`
- call/put Greeks observations: 각각 `20`
- call/put surface status: 각각 `READY`
- surface observation skew: `1.2354초`
- call artifact identity:
  `2b687f96a7b91e9b3764a220229fac05cf0a5322b1e0665c4a8664d289fa0762`
- call file SHA-256:
  `edeaaeec488910b349200d0e161a3972d12e55d82577ab9b77b903047fa2ed6f`
- put artifact identity:
  `3f6449fb74fc7d4768e946053906ebfac41c40490f121ef246e29b8038924c77`
- put file SHA-256:
  `ce898c9adc368241d82c370dba7e3f5095bf92187163f8d9f0dee867ef3ada38`

존재하지 않는 credential 경로로 exact replay했을 때 contract·chain run/receipt는 모두
`2→2`로 유지됐고 네 provider report는 `replayed: yes`, `network access: 0`이었다.
재생한 두 surface file SHA도 actual과 정확히 일치했다. report·artifact·log는 mode
`600`, broker·account·order mutation은 `0`이다.

두 READY surface는 skew의 300초 입력 gate를 통과했지만 SIP spot receipt가
`blocked_source`이므로 실제 skew artifact는 계속 `0`이다. surface 성공만으로 spot
gate를 우회하지 않는다.

## 자동 재검증

같은 exact runtime의 one-shot
`ai.trading-agent.m6-sip-spot-retry-20260724`를
2026-07-24 09:31:30 EDT에 등록했다.

- registered PID: `2518`
- launchd runs: `1`
- wrapper: mode `700`
- stdout/stderr와 atomic receipt: mode `600`
- operation: Alpaca market-data GET-only
- Paper·계좌·주문·allocation authority: 없음

이 예약은 entitlement가 확보됐다는 주장이나 actual skew 성공 증거가 아니다. 실행 뒤
receipt와 report를 다시 검증하고, SIP source가 실제 READY인 경우에만 call/put
surface와 spot을 동일 300초 경계에서 수집해 skew를 생성한다.
