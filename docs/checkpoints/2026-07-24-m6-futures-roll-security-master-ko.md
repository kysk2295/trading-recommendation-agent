# M6 futures roll security master 체크포인트

## 제품 경계

검토된 private futures contract manifest를 provider-neutral immutable roll
security master로 materialize하는 local-only vertical을 추가했다.

```text
mode-600 contract manifest
-> stable instrument identity + provider alias
-> listing/observation/settlement/notice/last-trade 검증
-> 연속 active_from/roll_at window
-> as-of active contract 1개
-> content-addressed mode-600 master
```

입력은 동일 root, venue, currency, timezone, multiplier와 provider namespace를
공유하는 2~32개 계약만 허용한다. 계약 identity, alias와 expiration은 고유하고
last-trade 순서로 정렬돼야 한다. cash 계약은 first-notice가 없어야 하고 physical
계약은 roll 뒤, last-trade 이전 first-notice를 가져야 한다. source observation
시점에 아직 상장되지 않은 계약, gap·overlap, roll 전에 끝나는 instrument/alias
유효기간은 결과 발행 전에 차단한다.

CLI는 외부 network나 provider를 열지 않고 private manifest만 query-only로 읽는다.
as-of 시점에 정확히 한 active contract를 해석한 경우에만 master와 aggregate
report를 게시한다. exact replay는 같은 master 파일을 재사용한다.

## TDD와 검증

- missing CLI happy path: `1 failed -> 1 passed`
- alias/instrument active-window, pre-listing active, provider namespace:
  각 failing-first 회귀 뒤 통과
- continuous window, future knowledge, settlement, private input,
  pre-observation as-of와 bad CLI 포함 focused: `11 passed`
- security-master·option-contract 관련 회귀: `40 passed`
- 전체 pytest: `3507 passed`
- Ruff 전체: pass
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- Python no-excuse: 위반 `0`
- pure LOC: models `176`, operations `96`, CLI `68`, tests `111/196`

실제 CLI `--help`는 manifest, as-of와 output directory 세 필수 option을 노출했다.
committed public fixture는 exit `2`, output directory 생성 `0`으로 차단됐다.
private copy의 happy/replay는 exit `0/0`, artifact `yes/no`, 파일 수 `1`이었다.

- fixture artifact SHA-256:
  `80097faf0842616cc2cb702d5db41b0165735c96c7de11b607832a7c9d671831`
- replay artifact SHA-256:
  `80097faf0842616cc2cb702d5db41b0165735c96c7de11b607832a7c9d671831`
- artifact/report mode: `600/600`
- network access: `0`
- broker, account, position, order mutation: `0`

## 제한

이 체크포인트의 ES 두 계약은 parser, identity, roll-window와 replay 계약을 검증하는
repository fixture다. CME 웹 페이지를 실제 수집하거나 licensed settlement
calendar를 검증한 증거가 아니며, 실제 현재 계약 coverage, 거래 가능성, basis,
curve, roll yield, 전략 성과나 주문 권한을 뜻하지 않는다.

실제 CME·ICE 등 provider별 raw-first adapter, 수정·삭제 이력, licensed source
receipt와 정규장 actual master는 후속 운영 체크포인트다. Allocation Manager와
Paper 권한은 이 vertical에서 계속 닫혀 있다.
