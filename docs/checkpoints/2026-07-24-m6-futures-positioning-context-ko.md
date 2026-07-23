# M6 Futures Positioning Context 체크포인트

작성일: 2026-07-24 KST

## 판정

Milestone 6의 실제 CFTC TFF weekly market-level context를 reviewed futures roll
master의 exact active contract window에 결합하는 query-only vertical을 완료했다.
구현 기준 SHA는 `0da133c93efb5c338e22374590487ddc7de9828a`다.

이 결과는 CFTC source가 관측 가능했던 시점에 어떤 futures contract가 active였는지를
재생하는 shadow context다. 추천, 전략 성과, Paper 주문 또는 allocation 권한이 아니다.

## fail-closed 계약

세 input은 모두 현재 사용자 소유 mode-600 regular file이어야 한다.

- CFTC와 roll master는 canonical bytes, semantic ID, content-addressed filename과 file
  SHA-256을 다시 대사한다.
- binding은 CFTC contract market code, root symbol과 venue를 명시적으로 고정한다.
  market 이름이나 provider symbol 텍스트로 관계를 추론하지 않는다.
- `as_of`는 binding의 관측 이후이면서 effective interval 안이어야 한다.
- CFTC receipt observation과 futures master source observation은 미래일 수 없다.
- CFTC latest report date는 as-of UTC date보다 미래일 수 없고 명시된 최대 age를
  넘을 수 없다.
- 기존 roll resolver가 `active_from <= as_of < roll_at`인 계약을 정확히 하나
  선택해야 한다. roll 경계와 같은 시각에는 다음 계약을 선택한다.

출력은 세 input의 exact file SHA와 CFTC context ID, futures master ID, active
instrument/alias/window, 다섯 positioning category를 content-addressed mode-600
artifact에 보존한다. 별도 aggregate report에는 instrument ID, provider symbol,
position 수치와 로컬 경로를 쓰지 않는다.

## 실제·fixture 결합 증거

`2026-07-24T18:00:00Z` as-of로 actual CFTC artifact와 reviewed fixture roll
master를 결합했다.

- CFTC context ID:
  `2204fd0de65cb76f138c5f6384db40c64b9ae20b7d516f5b2a8691fc88a20346`
- CFTC artifact SHA-256:
  `318619090a28443c33f49771b33c2fab2bdec9ec440eb75dd1b293eb2d19d10d`
- CFTC observed at: `2026-07-23T17:44:10.540899Z`
- latest/previous report date: `2026-07-14` / `2026-07-07`
- futures master ID:
  `b1f6a0e3f40871f918ce5b9cf86e6f3384a7dc8cb89a09ad792b7d94b93f744d`
- futures master file SHA-256:
  `80097faf0842616cc2cb702d5db41b0165735c96c7de11b607832a7c9d671831`
- binding file SHA-256:
  `cf7d491c5071a864bd51a2d260502b5373a7c1ac42eac9446c0264688b7a1705`
- selected active instrument/alias: `cme:es-202609` / `ESU6`
- active window:
  `2026-06-01T12:00:00-05:00 <= as_of < 2026-09-10T16:00:00-05:00`
- output context ID:
  `6feb7cdb19de41f04c180afa4be0c2b6aff68c30bfaaf416473d9f961964510b`
- output artifact SHA-256:
  `b0f972561296764a9707ef3c45aad3d962e161ebd83ca4b6d01da93b5d2c45b5`
- category count: `5`
- 최초/replay artifact created: `yes` / `no`
- input/output/report mode: 모두 `600`
- join network, credential, broker, account, order mutation: `0`

actual인 것은 CFTC 공개 source artifact다. futures master는 현재 licensed CME/ICE
source coverage가 아니라 기존 계약을 검토하기 위한 fixture manifest다. 따라서 위
active contract 선택을 현재 production roll recommendation 또는 실제 시장 coverage로
해석하지 않는다.

## 수동 QA와 검증

- CLI `--help`: exit `0`
- root mismatch private binding: exit `2`, output directory 생성 `0`, 오류에 input
  value/path 노출 `0`
- fixture happy/replay: exit `0/0`, artifact created `yes/no`, file SHA 동일
- actual CFTC + fixture master happy/replay: exit `0/0`, artifact created `yes/no`
- focused join/CLI/CFTC/futures regression: `32 passed`
- 전체 pytest: `3540 passed in 216.60s`
- 전체 Ruff: 통과
- 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- compileall, diff check, changed-file format: 통과
- production pure LOC: models `148`, operations `191`, CLI `93`
- no-excuse production grep: 위반 `0`

## 다음 증거

다음 M6 dependency는 licensed current CME/ICE roll source의 raw-first adapter와
다중 root coverage다. 이후에야 settlement, curve, basis와 positioning history를
결합하는 derivatives research agent를 평가할 수 있다. 이 단계와 독립적으로 M8의
actual forward session, causal CSV, READY foundation, source-backed walk-forward와
Reviewer 증거를 계속 수집한다.
