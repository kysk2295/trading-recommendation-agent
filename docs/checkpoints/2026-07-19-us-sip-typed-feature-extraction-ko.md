# US SIP typed feature extraction 체크포인트

## 완성 범위

- READY intraday feature snapshot의 breakout과 RVOL threshold를 deterministic research claim으로 추출한다.
- snapshot의 `ResearchInputIdentity`를 exact canonical dataset replay에서 다시 계산한다.
- `alpaca/sip` minute-bar source, instrument entity, event count와 정규장 개장부터의 연속 시각, 마지막 완료 분봉을 재검증한다.
- 20개 완료 세션 volume-profile evidence와 indicator semantic version을 extraction output hash에 결합한다.
- 기존 fleet cycle의 opt-in 경로에서 owner별 research evidence read model을 별도 저장한다.

## 인과성과 격리

- event receipt와 normalization은 snapshot observation 뒤일 수 없다.
- blocked/gap/stale snapshot, 다른 dataset, 누락된 latest event와 잘못된 RVOL 기준은 fail-closed다.
- RVOL threshold bps는 claim key와 output hash에 포함되어 다른 기준의 실험이 섞이지 않는다.
- 여러 owner는 하나의 read model로 합치지 않는다. 종목별 evidence는 독립 artifact로 남는다.
- 단일 `alpaca/sip` source의 결과는 `unconfirmed`이며 추천·수익성·승격 근거가 아니다.

## 운영 경로

`run_us_runtime_fleet_cycle.py`에 다음 opt-in 옵션을 추가했다.

```bash
--research-artifact-root outputs/runtime/us-sip-fleet/research-evidence \
--minimum-rvol-bps 15000
```

READY fleet 결과가 생긴 같은 process에서 provider를 다시 호출하지 않고 snapshot binding과 canonical dataset을 대사한다. content-addressed artifact는 mode 600이며 raw receipt reference, 원문 payload와 credential을 포함하지 않는다.

## 검증

- focused extraction/fleet/read-model: 23 passed
- full repository: 2307 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, changed-file format, no-excuse rules: passed

## 수동 CLI QA

- `--help`: exit 0
- invalid `--minimum-rvol-bps 0`: exit 1, provider·secret·policy state 접근 전 blocked report
- fixture happy path: Alpaca data GET 1건, evidence artifact 1개, claim 2개, raw receipt reference 0건
- account/order endpoint, POST/DELETE와 broker mutation: 0건

## 다음 단계

- KR OpenDART 공시와 LS 뉴스의 raw-before-parse normalized typed extraction 계약
- provider correction/tombstone 뒤 stale extraction invalidation replay
- 다음 열린 NYSE 정규장의 bounded current-minute read-only GET smoke
