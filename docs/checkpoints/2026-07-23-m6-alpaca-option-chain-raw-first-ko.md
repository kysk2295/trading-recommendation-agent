# M6 Alpaca 옵션체인 raw-first 체크포인트

## 제품 결과

M6 derivatives data의 첫 실제 vertical로 Alpaca option-chain snapshot을 bounded
GET-only로 수집한다.

```text
explicit underlying / expiration / call-or-put / feed
→ exact Alpaca option-chain GET
→ raw response receipt before parsing
→ strict contract snapshot projection
→ immutable terminal run
→ local derivatives capability + entitlement
```

기준 코드 SHA는
`388f099369402b04f38070e7dee140de21bc5bcd`다. endpoint는 공식 계약의
`GET https://data.alpaca.markets/v1beta1/options/snapshots/{underlying_symbol}`로
고정하고 redirect를 따르지 않는다. feed는 `indicative` 또는 `opra`를 명시해야 하며
다른 feed로 조용히 대체하지 않는다.

## 보존·실패 계약

- request는 underlying, exact expiration date, call/put, feed, page limit을
  content-addressed ID에 고정한다.
- response wire bytes는 최대 16 MiB이며 HTTP status와 content type을 함께 parser 전에
  append-only SQLite receipt로 확정한다.
- HTTP 오류도 raw receipt를 먼저 남긴 뒤 `http_status` terminal로 닫는다.
- transport, response structure, page limit, token cycle, duplicate contract는 서로 다른
  failure code로 보존한다.
- 성공 snapshot은 OCC symbol의 underlying, expiration, right를 요청과 다시 대사한다.
- latest quote/trade, implied volatility와 Greeks를 보존한다.
- exact terminal replay는 provider와 credential을 다시 열지 않는다.
- database와 보고서는 mode `600`, 최종 private directory는 mode `700`이다.
- capability projection은 `US_DERIVATIVES`, source
  `alpaca/options_indicative|options_opra`, `historical_research`와
  `shadow_forward`만 허용한다. real-time entitlement나 재배포 권한을 만들지 않는다.
- 이 경로는 Alpaca trading, account, position, order API를 import하거나 호출하지 않는다.

## TDD와 수동 QA

공개 CLI 부재, non-JSON HTTP failure raw-first 보존, capability CLI 부재를 각각 RED로
재현한 뒤 구현했다.

- option-chain 및 capability 관련 집중 회귀: `39 passed`
- 전체 pytest: `3436 passed in 207.18s`
- Ruff: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`
- changed-file no-excuse 규칙: pass
- 두 CLI `--help`: exit `0`
- 잘못된 symbol: exit `2`, database 미생성
- fixture 수집 / exact replay / capability projection: `0/0/0`
- fixture 원장 receipt/run: `1/1`
- database, registry, 두 보고서 mode: 모두 `600`

macOS `/tmp` symlink는 private-directory identity 경계에서 의도대로 거부됐다. 동일 QA는
실제 디렉터리인 `/private/tmp`에서 성공했으며 이 검사를 완화하지 않았다.

## 실제 정규장 검증 예약

exact SHA의 clean detached runtime을
`/private/tmp/trading-agent-m6-option-chain-388f099`에 고정했다.

- label: `ai.trading-agent.m6-option-chain-smoke-20260723`
- 실행시각: 2026-07-23 09:40 EDT / 22:40 KST
- request: AAPL, 2026-07-24 call, `indicative`, limit 1,000, 최대 2 pages
- collection ledger:
  `outputs/derivatives/m6_live/2026-07-23/option-chain.sqlite3`
- capability registry:
  `outputs/derivatives/m6_live/2026-07-23/capabilities.sqlite3`
- 등록 직후 상태: running, run count `1`, PID `21809`
- wrapper/payload mode: `700`; stdout/stderr mode: `600`

공용 at-most-once runner가 실행 시각 직전에 claim을 획득하고 payload 성공·실패 receipt를
쓴 뒤 자기 launchd label을 제거한다. payload도 New York 날짜와 09:30~16:00 정규장을
다시 확인한다.

이 예약은 아직 live 성공 증거가 아니다. 실제 실행 뒤 raw receipt, terminal,
capability report를 다시 검증해야 한다. 단일 AAPL·단일 만기·단일 right snapshot은
시장 전체 옵션 coverage, OPRA entitlement, OI, term structure, futures curve,
derivatives 전략 성과 또는 추천·주문 권한을 뜻하지 않는다.
