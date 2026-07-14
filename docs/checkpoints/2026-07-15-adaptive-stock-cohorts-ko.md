# 적응형 종목 특성 cohort 체크포인트

## 목적

급등주는 같은 ticker가 장기간 반복되지 않으므로 티커별 60일 성과는 실용적이지 않다. 대신 매매 결정 당시 관찰 가능한 가격대, 장전 갭, 시점 누적 volume/ADV, 시점 거래대금으로 표본을 나눠 어떤 종류의 급등주에서 전략이 유지되거나 깨지는지 평가한다.

## 인과적 조인

1. 완료 거래의 recommendation ID로 추천 생성시각을 읽는다.
2. `candidate_input_snapshots`에서 종목과 추천 생성시각이 정확히 같은 단일 행을 찾고 거래소를 고정한다.
3. 그 시각 이하의 최신 checksum된 `market_risk_screen.csv` 행만 가격·등락률·volume/ADV·거래대금·spread 원천으로 사용한다.
4. `kis_opening_gap_snapshots.csv`도 ranking·quote 관찰시각이 모두 추천 생성시각 이하인 최신 성공 행만 사용한다.
5. 미래 행, 중복 candidate input, 원천 결손은 소급 보간하지 않고 `censored`다.

## 사전 고정 cohort

| 차원 | 구간 |
|---|---|
| 가격 | `<$5`, `$5~20`, `$20~50`, `$50+` |
| opening gap | `<4%`, `4~10%`, `10~20%`, `20%+` |
| 누적 volume/ADV | `<10%`, `10~25%`, `25~50%`, `50%+` |
| 시점 거래대금 | `<$1M`, `$1~5M`, `$5~20M`, `$20M+` |

구간은 현재 수익 결과를 보고 고른 최고점이 아니다. 최소 10거래인 cohort가 편도 20bp 후 PF<0.8 또는 평균≤0이면 aggregate가 좋아도 최종 검토를 차단한다. 핵심 특성과 gap coverage는 각각 80% 이상이어야 한다.

## 산출물

- 적응형 JSON의 `feature_coverage`, `gap_feature_coverage`, `cohorts`
- 한국어 카드의 종목 특성 cohort 표
- 거래별 조인 시각·원시값·버킷·검열 사유를 남기는 `trade_feature_assignments.csv`

이 결과는 Paper 전진검증 분해 도구이며 수익성 증거나 자동 승격 권한이 아니다.
