# M6 actual multi-expiration option term structure 체크포인트

## 제품 경계

단일 만기 option surface를 여러 만기의 as-of IV/OI evidence로 결합하는
`run_alpaca_option_term_structure.py`를 추가했다.

```text
2~32 READY option surfaces
-> same underlying/feed + unique expiration/right
-> maximum 300-second observation skew
-> exact surface semantic ID + file SHA
-> OI as-of date/total + all-strike median IV
-> content-addressed mode-600 term structure
```

각 입력은 private query-only reader로 읽고
`option_surface_<semantic-id>.json` 이름과 parsed semantic ID를 다시 대사한다.
`DEGRADED`, renamed/non-content-addressed, 다른 underlying·feed, 중복 만기/right,
2개 미만 만기, OI·IV 결손, 미래 OI date와 configured skew 초과는 결과 발행 전에
차단한다. 결과와 report에는 개별 provider symbol, instrument ID나 contract 가격을
노출하지 않는다.

## TDD와 검증

- missing CLI E2E: `1 failed -> 2 passed`
- OI 관측일 누락과 renamed input: `2 failed -> 4 passed`
- aggregate report 결손: `1 failed -> 5 passed`
- option chain/contract/surface/term focused: `12 passed`
- 전체 pytest: `3483 passed`
- Ruff 전체: pass
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- Python no-excuse: 위반 `0`
- pure LOC: models `102`, builder `162`, CLI `88`, E2E `175`

CLI `--help`는 반복 `--surface`와 최대 skew를 노출했다. surface 한 개의 bad
input은 exit `2`, output `0`이었고 두 만기 happy/replay는 exit `0/0`,
artifact `yes/no`였다. artifact와 report는 mode `600`이다.

## exact-SHA 정규장 actual evidence

clean detached runtime
`/private/tmp/trading-agent-m6-term-15ee93c`의 exact commit
`15ee93c72aa25ede8bed2c7805cafe50cb12c942`에서 2026-07-23 11:18 EDT
정규장에 AAPL call 두 만기를 bounded GET-only로 수집했다.

| expiration | master/chain exact join | raw chain bytes | OI | IV/Greeks | minute/daily/previous bar |
|---|---:|---:|---:|---:|---:|
| 2026-07-24 | 77/77 | 42,666 | 68 | 18/18 | 75/75/71 |
| 2026-07-31 | 78/78 | 45,519 | 74 | 43/43 | 75/75/72 |

두 surface 모두 `READY`, coverage `10,000 bps`였다.

- 2026-07-24 contract raw SHA:
  `e2a1ea58c795d18c847978a0bbb7165714a6a29777fd7943a2fa081c665a7a40`
- 2026-07-24 chain raw SHA:
  `f076c3be3b24785f4fbf07a21644bdbd4c6785125a789bd2c43a7541b32987d3`
- 2026-07-31 contract raw SHA:
  `fd84d6b22981069a758972239b4f469800a6d2e8fbeface6ae4a9bcb8b0ec055`
- 2026-07-31 chain raw SHA:
  `efca06c741e329191930b35bfd858ee3d14024b6568079ad34850a8a5fbdda69`
- input surface file SHA:
  `99cbac542d6889d84b257eca7dfaf9f9911f74efe8f79b1f47898bd4bfc2ce2d`,
  `25741c234ac25f24ef93c4f12545d944569616ff4e35870f1b9c36975eb45cf2`
- surface observation skew: `3.487562` seconds
- term structure ID:
  `7fcc110fcb8134c5514d50730e9fc090f7e31c61c65f6fe38cf0c332b5c51138`
- term artifact SHA:
  `8924ca6d7fd8da8be9d2e2828fea15c19d8679e4666a4533521f0ec19953081a`
- 2026-07-24 OI / all-strike median IV: `147,531 / 0.4052`
- 2026-07-31 OI / all-strike median IV: `74,397 / 0.3996`

actual artifact는
`outputs/derivatives/m6_live/2026-07-23/term-15ee93c/`에 보존했다.
database, surface와 term artifact, report/log는 mode `600`이다.

## exact replay와 제한

동일 네 GET request를 존재하지 않는 credential 경로로 재실행한 결과 exit `0`,
network access `0`, stderr `0 bytes`였다.

- contract receipt/run: `2/2 -> 2/2`
- chain receipt/run: `2/2 -> 2/2`
- surface artifact: `2 -> 2`
- term artifact: `1 -> 1`
- broker, account, position, order mutation: `0`

이 결과는 AAPL call, 두 만기, indicative feed의 shadow-only data evidence다.
`all-strike median IV`는 ATM IV, delta bucket skew 또는 per-strike volatility
surface가 아니다. put parity, OPRA entitlement, 시장 전체 coverage, 전략 성과,
추천·champion·allocation 또는 주문 권한을 뜻하지 않는다. 다음 M6 경계는
같은 as-of의 call/put과 underlying spot을 결합해 사전등록 delta/strike bucket별
skew를 계산하는 것이다.
