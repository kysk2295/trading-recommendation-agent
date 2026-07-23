# M6 actual option-chain bar 복구 체크포인트

## 실제 실패

2026-07-23 09:40 EDT 예약 option-chain smoke는 HTTP `200`,
`application/json`, raw `41,421` bytes와 77개 snapshot을 먼저 보존했지만 terminal은
`response_structure`로 닫혔다. raw receipt를 query-only로 분석한 결과 provider가
snapshot에 다음 세 필드를 추가로 반환했다.

- `minuteBar`: 72개
- `dailyBar`: 72개
- `prevDailyBar`: 63개

기존 strict `ProviderOptionSnapshot(extra="forbid")`에는 latest quote/trade,
implied volatility와 Greeks만 선언돼 있어 세 bar 모두 `extra_forbidden`이었다.
세 필드만 제거하면 77개 snapshot 전부가 AAPL, 2026-07-24, call scope로 검증됐다.
따라서 quote/trade/Greeks나 OCC scope가 아니라 actual bar shape가 원인임을 toggle로
확정했다.

## TDD 복구

commit `7f5043cb5870871e07f2ebf451b94a9d591eca2c`는 option bar의 timestamp,
OHLC, volume, trade count와 VWAP을 typed model로 검증한다. timestamp는 timezone-aware,
가격·수량은 nonnegative여야 하며 high/low가 open/close를 포함해야 한다. 세 bar는
provider page에서 canonical `OptionContractSnapshot`까지 보존되고 unknown field를
허용하는 일반 fallback은 추가하지 않았다.

actual-shaped regression은 수정 전 성공 terminal 기대에서 실패했고, 수정 뒤
snapshot과 세 bar를 정확히 round-trip했다.

## exact-SHA 정규장 운영 증거

clean detached runtime
`/private/tmp/trading-agent-m6-option-bars-7f5043c`에서 2026-07-23
10:57 EDT 정규장에 AAPL, expiration 2026-07-24, call, `indicative`, limit 1,000,
최대 2 pages를 GET-only로 실행했다.

- collection exit: `0`
- raw receipt: HTTP `200`, `application/json`, `42,869` bytes
- raw payload SHA-256:
  `77e03a32603a524bc878cde278123700ce2a505eecba374a4bb00e9865b8f823`
- option snapshot: `77`
- minute/daily/previous-daily bar: `75/75/71`
- quote/trade/IV/Greeks: `77/75/20/20`
- run payload SHA-256:
  `f47bf6c5b753d4b54bd17354326bda7ff617d25cd53d0e0391328ffd1107062b`
- database SHA-256:
  `50a41bf9af65e972622c15f70e6b10f2297388ea2b23452a6ba3fcc1abb01445`
- database와 report/log mode: `600`

actual AAPL contract master 77개와 query-only surface join도 exit `0`이었다.

- result: `READY`
- exact identity join: `77/77`
- snapshot coverage: `10,000 bps`
- master OI observation: `68`
- surface artifact SHA-256:
  `1cc69d67a2bbe30f7e46cdff991b0d81238124877d98f6c67480f19cf7cef275`
- artifact mode: `600`
- network operation during surface join: `0`

동일 collection request를 존재하지 않는 credential 경로로 다시 실행했을 때 exit `0`,
`replayed=yes`, network access `0`, receipt/run `1/1 → 1/1`이었다. actual source와
결과는
`outputs/derivatives/m6_live/2026-07-23/recovery-7f5043c/`에 보존했다.

## 검증과 경계

- failing-first regression: `1 failed → 1 passed`
- option-chain/capability/surface focused: `8 passed`
- 전체 pytest: `3478 passed`
- Ruff 전체: pass
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- provider operation: bounded market-data GET-only
- broker, account, position, order mutation: `0`

이 결과는 단일 AAPL·단일 만기·call의 indicative shadow data evidence다. 시장 전체
옵션 coverage, OPRA entitlement, 여러 만기의 IV skew·term structure, derivatives
전략 성과, 추천 또는 주문 권한이 아니다. 다음 M6 제품 경계는 여러 만기·right의
READY surface를 고정해 동일 underlying as-of IV/OI term structure를 계산하는 것이다.
