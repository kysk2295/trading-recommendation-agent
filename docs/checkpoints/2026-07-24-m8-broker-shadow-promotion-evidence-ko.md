# M8 Paper broker/shadow 승격 증거 체크포인트

## 제품 결과

US Day terminal이 broker/local reconciliation 성공을 실제로 관측하지 않은
broker/conservative-shadow equality로 복제하던 오류를 fail-closed로 수정했다.
shadow source가 API에 들어오지 않으면 terminal의
`broker_shadow_ledger_equal`은 항상 `false`다.

별도 query-only 진단 CLI는 exact daily research lineage와 Paper execution SQLite
snapshot을 결속해 broker 체결과 같은 recommendation ID의 conservative shadow
거래를 비교한다.

```text
verified forward research source
→ exact execution snapshot SHA before/after
→ complete Paper entry fill
→ protective OCO 또는 acknowledged EOD close fill
→ same recommendation/session/strategy shadow pair
→ collecting | broker_shadow_ready | broker_shadow_not_confirmed
→ content-addressed mode-600 artifact
```

## 판정 계약

- broker entry는 complete·execution-detail-complete fill만 허용한다.
- exit는 저장된 protective OCO leg 또는 acknowledged/recovered EOD
  `CLOSE_POSITION` broker order ID의 Account Activities FILL만 허용한다.
- recommendation, symbol, strategy version과 NYSE session date가 정확히 같아야 한다.
- broker와 shadow 양쪽 모두 왕복 40bp 비용을 적용한다.
- 최소 60 paired session·100 paired trade 전에는 항상 `collecting`이다.
- 성숙 뒤 양쪽 PF `1.15` 이상, 평균수익 양수, 고정 거래일 block-bootstrap
  95% CI 하한 `0` 이상일 때만 `broker_shadow_ready`다.
- unpaired broker intent가 하나라도 있으면 승격 준비 상태가 아니다.
- execution snapshot은 읽기 전후 SHA가 같아야 하며 중간 mutation은 차단한다.
- artifact model은 pairs에서 status, blocker와 양쪽 metrics를 다시 계산해
  self-consistent 위조를 거부한다.
- 자동 lifecycle, 주문권한과 allocation 변경은 모두 `false`다.

## TDD와 실제 CLI QA

- fail-first terminal 회귀: `broker_shadow_ledger_equal`이 `true`여서 assertion 실패
- fail-first evidence API, OCO pairing, EOD close pairing과 mature 통계
- focused: `9 passed`
- exact feature HEAD 전체 pytest: `3608 passed`
- Ruff 전체: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`
- no-excuse audit: `0`
- isolated CLI help/bad/happy: `0/1/0`
- missing execution ledger: `blocked`, artifact `0`
- actual 2026-07-23 source: `collecting`, paired session/trade `0/0`
- actual artifact ID:
  `81c1ebe9c5999ab72521dbc68f5486b75bd34768e276cd0cdf7046fc91fbbb57`
- execution snapshot SHA:
  `46aeaf227360f048cde5b0af7a3c6562b5114fedc6e5cc746fd39892703a0201`
- artifact/report mode: `600`
- exact replay: `created: false`
- external broker/account mutation: `0`

실제 2026-07-23 research source에는 strict 적격 세션이 없고 Paper intent도 0건이므로
빈 shadow source hash와 `collecting`은 정상적인 fail-closed 결과다. 성과, equality,
Paper champion 또는 승격 근거가 아니다.

## 커밋과 후속 예약

- false-positive 수정:
  `2fed32570123731f73d356325aa57244bb541b4e`
- query-only 증거 vertical:
  `2baa433d5bd795cc6f035052a114b900a9511b02`
- 두 커밋 모두 `origin/main`에 push됐다.
- frozen runtime:
  `/private/tmp/trading-agent-broker-shadow-2baa433`

기존 US forward, finalizer, cumulative research와 terminal audit을 변경하지 않고 그
뒤에 실행되는 at-most-once query-only 작업을 추가했다.

| Session | 실행 시각 (KST) | Label | 등록 PID |
|---|---:|---|---:|
| 2026-07-24 | 2026-07-25 07:15 | `ai.trading-agent.broker-shadow-evidence-20260724` | 60873 |
| 2026-07-27 | 2026-07-28 07:15 | `ai.trading-agent.broker-shadow-evidence-20260727` | 60879 |

payload/runner는 mode `700`, stdout/stderr는 mode `600`·0바이트이며 receipt와
claim은 아직 없다. 예약은 paired evidence 성공 주장이 아니다. daily research
record나 initialized execution ledger가 없으면 artifact 없이 blocked report와
nonzero receipt를 남긴다.

KR runner PID `94276`과 Hermes PID `31663`은 중단·변경·재시작하지 않았다.
