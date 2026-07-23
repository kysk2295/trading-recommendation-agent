# M8 다중 전략 source queue 체크포인트

작성 시각: 2026-07-23 15:14 KST

## 닫은 결손

source-backed v2 loop는 최대 세 전략을 받지만 committed source manifest와 실제 design-ready queue card는 VWAP 하나뿐이었다. clean actual dataset이 생겨도 HOD breakout과 Gap-and-Go를 같은 exact queue·v2 manifest로 실행할 수 없었다.

## 구현

- 기존 VWAP card의 두 공개 academic source와 명시 limitations를 그대로 재사용했다.
- `H-MOM-HOD-SOURCE-002`를 별도 immutable HOD breakout hypothesis/card로 추가했다.
- `H-MOM-GAP-SOURCE-002`를 별도 immutable Gap-and-Go hypothesis/card로 추가했다.
- 두 가설 모두 `intraday_momentum` single-lane이고 독립 falsification, mechanism, counterfactual을 가진다.
- 공개 연구는 current-market point-in-time net-cost 성과를 증명하지 않는다는 기존 limitation을 유지했다.
- 세 manifest를 같은 current-schema ledger에 등록하면 source row는 두 개만 존재하고 card는 세 개가 된다.
- 세 card 모두 strategy version과 historical trial이 없으므로 `strategy_design`으로만 routing된다.
- lifecycle, allocation, order authority는 계속 false다.

## 실제 local operating evidence

원본 checkout의 `outputs/experiment_control/experiment_ledger.sqlite3`는 현재 reader가 구 schema로 차단했다. 이 파일을 수정하거나 migration하지 않고 integration worktree의 별도 ignored ledger `outputs/experiment_control/source_intraday.sqlite3`를 사용했다.

- VWAP 등록: source 신규/재사용 `2/0`, card 신규/재사용 `1/0`
- HOD 등록: source 신규/재사용 `0/2`, card 신규/재사용 `1/0`
- Gap 등록: source 신규/재사용 `0/2`, card 신규/재사용 `1/0`
- queue item: 3
- strategy design: 3
- queue snapshot: `507e9bb45d1865978be73eff7c9efa03072b567e4b33d751dfec86e4ba45703b`
- VWAP card: `c2940bb80c97523d86489d95ca91d1b8c1e2a350bef02c7fc8ddbf1d1127714d`
- HOD card: `a269a4c4f9e1e05f5b68dc3f90fdc2c29adc6e276d0a157812f2b07ee2c6ddb8`
- Gap card: `ad82cbded2ae72c9919c00fd36f2b53868fae1246f7a5b6a7c65d3d8f280f097`
- ledger와 queue artifact mode: 600
- provider, credential, account, broker, order mutation: 0
- 관련 registration·queue·source-backed loop 24개 테스트, Ruff, basedpyright 0/0, no-excuse가 통과했다.

## 다음 gate

이 queue는 실제 연구 설계 입력을 준비했을 뿐이다. clean strict forward session과 검증된 historical entitlement 계약이 생긴 뒤 actual causal CSV, 전략별 READY foundation과 v2 manifest를 결속해야 한다. walk-forward와 독립 Reviewer 결과가 생기기 전에는 Paper champion이나 Allocation Manager 근거가 아니다.
