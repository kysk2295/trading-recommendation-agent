# 적응형 전략 평가 체크포인트

## 문제

급등주 전략을 최소 60거래일 동안 그대로 유지한 뒤 한 번만 평가하면 시장 국면 변화에 비해 대응이 늦다. 반대로 며칠의 손익만 보고 파라미터를 계속 바꾸면 데이터 스누핑과 전략 회전이 커진다. 두 위험을 분리하기 위해 60일을 **수익 확정·위험 확대의 최종 증명 문턱**으로만 두고, 운용 중 판단은 더 짧은 사전 고정 롤링 창에서 수행한다.

## 구현 계약

- 입력은 일일 연구 record와 그 record가 SHA-256으로 고정한 `paper_metrics/paper_trades.csv`다.
- 서로 다른 전략 버전·평가기 버전·feed entitlement는 섞지 않는다.
- 동일 날짜 record가 갱신된 경우 가장 늦게 기록된 불변 record 하나만 사용한다.
- 각 record ID에 대응하는 세션 폴더가 정확히 하나가 아니면 실패한다.
- 시장 국면은 `research_regime_snapshot.json`이 정규장 개장 전에 관측되고 record checksum에 포함된 경우만 사용한다.
- 결과는 권고이며 전략 상태·주문 권한을 자동 변경하지 않는다.

## 사전 고정 판단

| 단계 | 최소 표본 | 판단 |
|---|---:|---|
| 수집 | 5 적격일 미만 | 모든 challenger를 독립 shadow로 계속 수집 |
| 조기중단 | 최근 5일·10거래 | 20bp PF<0.75, 평균<0, 거래일 bootstrap CI 상단<0이 모두 성립하면 중단 |
| 진단 | 최근 10일·15거래 | PF<1 또는 평균≤0이면 국면·체결·스캐너 분해 진단 |
| 비교 준비 | 최근 20일·30거래 | PF≥1.15, 평균>0, CI 하한≥0이면 동일 위험 champion 비교 후보 |
| 열화 중단 | 누적 20일 이상 | 최근 5일 조기중단 조건 재발 시 즉시 shadow 중단 권고 |
| 최종 검토 | 최근 60일·100거래 | PF≥1.15, 평균>0, CI 하한≥0과 시장 국면 coverage·다양성 확인 |

짧은 창의 열화가 긴 창의 양호한 aggregate에 가려지지 않도록 판단 우선순위는 `최근 5일 명확한 실패 → 최근 10일 약화 진단 → 60일 최종 검토 → 20일 비교 준비` 순서로 고정한다.

최근 60일의 장전 국면 라벨 coverage는 80% 이상, 서로 다른 라벨은 최소 2개여야 한다. 10거래 이상인 한 국면에서 PF<0.8 또는 평균≤0이면 aggregate가 양수여도 `regime_instability` blocker를 남긴다. 이 숫자는 현재 표본의 최고점을 보고 고른 값이 아니라 전진검증 전에 고정한 운영 안전 문턱이다.

## 산출물과 실행

```bash
./run_adaptive_strategy_evaluation.py outputs/live_sessions/<session>
```

- `adaptive_evaluation/adaptive_evaluation.json`
- `adaptive_evaluation/adaptive_evaluation_ko.md`
- 자동 watch 감사행 `post_session_adaptive_evaluation_cycles.csv`

장마감 흐름은 EOD 분봉 보존 → paper metrics → 일일 불변 원장 → 적응형 평가 순서다. 앞 단계가 실패하면 다음 단계를 실행하지 않는다.

## 검증

- 5일 명확한 손실의 조기중단
- 10일 약한 edge의 진단
- 20일 안정적 edge의 비교 준비
- 성숙 후보의 최근 5일 열화 중단
- 60일·180거래·2개 국면의 최종 검토
- 거래 CSV checksum 변조 거부
- 정규장 개장 뒤 생성된 국면 라벨 거부
- 실제 CLI JSON·한국어 카드 생성

이 체크포인트는 수익성 증거가 아니다. 실제 적격 정규장 표본은 아직 누적 초기이며, broker/shadow 동일 위험 비교와 Paper 주문 안전 게이트는 별도 검증이 필요하다.
