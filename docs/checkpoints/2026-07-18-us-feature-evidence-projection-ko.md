# US Feature Evidence Projection 체크포인트

- 날짜: 2026-07-18
- 마일스톤: M4.4 Evidence-Gated US Opportunity Projection
- provider/network 호출: 0건
- credential/account/order 접근: 0건

## 구현

- M4 경로는 `UsFeatureGateReady | UsFeatureGateBlocked` 타입으로 분리했다. blocked 결과에는 base Opportunity ID, 평가시각과 고정 차단 사유가 남고 Opportunity 객체가 없다.
- 기존 Opportunity의 모든 candidate symbol에 정확히 하나의 `UsFeatureEvidenceBinding`이 있어야 한다. 누락·초과·중복 symbol 또는 instrument binding은 통과하지 못한다.
- M4.1 status가 gap, stale 또는 insufficient history면 각각 `feature_gap`, `feature_stale`, `insufficient_history`로 차단한다. ready snapshot도 평가시각보다 미래이거나 2분 freshness window를 넘으면 차단한다.
- ready snapshot은 research identity, instrument, source range, indicator semantic version과 계산 결과 전체를 canonical hash에 결합한다. Opportunity에는 `research/intraday_feature` SHA-256 reference만 추가하고 indicator 값을 candidate feature로 복사하지 않는다.
- derived Opportunity ID는 base Opportunity 전체 canonical payload, gate 평가시각과 정렬된 evidence reference를 결합한다. 같은 base ID를 잘못 재사용해 candidate 내용이 바뀌어도 derived ID가 충돌하지 않는다.
- `project_evidence_gated_trade_signal_publications`는 ready 타입에서만 기존 publisher를 호출한다. 기존 day strategy threshold, entry·stop·target, scanner API와 publication API는 변경하지 않았다.
- gated signal은 계속 `conditional`이며 `quote_validation`은 없다. signal은 derived Opportunity를 reference하고 recommendation evidence를 유지한다.

## Fixture E2E

실제 M4.1 completed-bar kernel로 ready feature를 만든 뒤 M4.4 gate와 기존 signal publisher를 순서대로 실행했다. candidate payload는 그대로였고 feature reference 1개, derived Opportunity 1개와 conditional signal 1개가 생성됐다. 같은 표면에서 missing, gap과 stale 입력은 각각 `missing_evidence`, `feature_gap`, `feature_stale`로 종료됐다.

## 검증

- M4.4 focused: **8 passed**
- full repository: **2159 passed**
- Ruff: 통과
- basedpyright: 0 errors/warnings
- compileall: 통과
- no-excuse: 변경 production module 2개 위반 0건

## 다음 단계

실제 US read-only provider adapter를 M4.3 supervisor 계약에 연결하고 정규장 bounded smoke와 장기 restart·gap soak evidence를 누적한다. 그 전까지 fixture gated signal을 실시간 coverage, 체결 가능성 또는 수익성 증거로 사용하지 않는다.
