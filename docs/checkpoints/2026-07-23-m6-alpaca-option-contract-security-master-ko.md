# M6 Alpaca 옵션 계약 security master 체크포인트

## 제품 결과

M6 derivatives research가 snapshot의 계약 identity를 추정하지 않도록 Alpaca Paper의
옵션 계약 master를 bounded GET-only로 수집하는 실제 vertical을 추가했다.

```text
exact underlying / expiration / call-or-put
→ GET /v2/options/contracts
→ raw response receipt before parsing
→ strict provider contract validation
→ canonical US_DERIVATIVES security master
→ immutable terminal and network-free replay
```

최초 구현 SHA는
`98d0a663b1d9b557f0a9283ee38bd5c3759525bd`, 실제 provider 응답에서 확인한
`multiplier` 계약을 반영한 운영 SHA는
`a6029ef2e4d42ccf2c77bb5d1297880320e0ad8f`다. endpoint는 Alpaca 공식
[option contracts API](https://docs.alpaca.markets/us/v1.1/reference/get-options-contracts)의
Paper origin `GET https://paper-api.alpaca.markets/v2/options/contracts`로 고정한다.

## 보존·검증 계약

- request는 collection ID, underlying, exact expiration, call/put, 최대 10,000개 page
  size와 최대 8 pages를 content-addressed request ID에 고정한다.
- response bytes는 최대 16 MiB이며 HTTP status와 content type을 parser 전에
  append-only SQLite receipt로 확정한다.
- HTTP status, transport, response structure, page limit, token cycle, duplicate contract,
  empty result는 서로 다른 immutable failure terminal로 남는다.
- provider contract UUID와 exact underlying asset UUID를 각각 canonical instrument와
  underlying instrument identity로 사용한다.
- OCC symbol의 root, expiration, right, strike를 provider metadata와 다시 대사한다.
- status, tradable, exercise style, strike, size, multiplier, OI와 OI date, close와 close
  date를 typed boundary에서 검증한다. size와 multiplier가 다르면 fail-closed다.
- canonical 계약은 `US_DERIVATIVES`, `OPTION`, `US_OPTIONS`, `USD`,
  `America/New_York` identity와 provider alias를 보존한다.
- exact terminal replay는 credential을 읽거나 network를 다시 열지 않는다.
- database와 보고서는 mode `600`, private 부모 디렉터리는 mode `700`이다.
- trading, account, position, order endpoint와 mutation은 이 경로에 없다.

## 실제 GET에서 발견한 결함과 TDD 수정

SHA `98d0a663...`의 첫 actual GET은 HTTP `200`, JSON 38,072 bytes와 77개 계약을 raw
receipt로 보존했지만 `response_structure` terminal로 닫혔다. 실제 계약은
`size="100"`과 공식 `multiplier="100"`을 함께 반환했고 strict provider model이
후자를 unknown field로 거부했다.

실제 payload에서 multiplier를 제거하면 77개 계약이 검증되고 다시 넣으면 같은
`extra_forbidden`이 재현되는 toggle로 원인을 확정했다. 회귀 fixture에 documented
multiplier를 먼저 추가해 CLI exit `2` RED를 확인한 뒤 다음 최소 변경을 적용했다.

- positive multiplier를 provider boundary의 필수 필드로 추가
- size와 multiplier의 exact equality 검증
- canonical multiplier를 provider multiplier에서 투영

수정 뒤 같은 회귀는 GREEN이며 관련 테스트와 정적 검사는 모두 통과했다.

- option-contract 관련 테스트: `3 passed`
- 전체 pytest: `3439 passed in 205.84s`
- Ruff: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`
- changed-file no-excuse: pass

## exact-SHA actual 운영 증거

SHA `a6029ef2...`를 archive한 clean runtime에서 2026-07-23 AAPL,
expiration `2026-07-24`, call, limit 10,000, 최대 2 pages를 실행했다.

- live CLI: exit `0`
- raw receipt: HTTP `200`, `application/json`, 38,072 bytes, 1 page
- raw payload SHA-256:
  `e2a1ea58c795d18c847978a0bbb7165714a6a29777fd7943a2fa081c665a7a40`
- canonical contracts: 77, unique instrument IDs: 77
- OI observed: 68 contracts
- OI as-of date: `2026-07-21`
- observed multiplier set: `{100}`
- terminal: `success`, failure code `none`
- database SHA-256:
  `2365e385e0aa61092348cb2f33b06480fb62aa0050f11658a47953228a050cb0`
- database와 live/replay 보고서 mode: 모두 `600`
- 동일 request를 존재하지 않는 credential 경로로 재실행: exit `0`,
  `replayed: yes`, `network access: 0`
- broker, account, position, order mutation: `0`

운영 증거는
`outputs/derivatives/m6_contract_live/2026-07-23/a6029ef2/`에 보존했다.

## 경계와 다음 단계

이 결과는 단일 AAPL·단일 만기·call 계약 master와 OI 관측의 실제 성공 증거다. 시장
전체 옵션 coverage, OPRA entitlement, 실시간 option-chain snapshot 성공,
IV surface·skew·term structure, futures basis·curve·roll, derivatives 전략 성과,
추천·Paper 주문 권한을 뜻하지 않는다. 별도로 예약된 정규장 option-chain smoke의
terminal을 검증한 뒤 contract master와 snapshot을 동일 identity로 결합해야 한다.
