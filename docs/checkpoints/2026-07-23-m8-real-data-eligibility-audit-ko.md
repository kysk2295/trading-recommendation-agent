# M8 실제 historical data 적격성 감사

## 목적

source-backed intraday v2를 repository fixture가 아니라 현재 로컬에 보존된 실제
point-in-time 분봉으로 실행할 수 있는지 모든 원장을 읽기 전용으로 확인했다. 원본
session DB, canonical dataset과 실행 프로세스는 변경하지 않았다.

## KIS forward session

기존 `load_replay_source`의 동일한 인과성·품질 게이트로 4개 session을 재검증했다.

| Session | 결과 | 주요 차단 근거 |
|---|---|---|
| 2026-07-15 | blocked | ranking/watch 실패, 미복구 retry, post-session metrics 결손 |
| 2026-07-16 | blocked | coverage/candidate cycle 불일치, watch 실패, post-session metrics 결손 |
| 2026-07-21 | blocked | watch 실패, 미복구 retry, post-session metrics 결손 |
| 2026-07-22 | blocked | coverage·candidate cycle 불일치, watch 실패와 미복구 retry |

2026-07-22 원장에는 1거래일, 32종목, 10,639개 분봉과 3,206개 point-in-time
candidate input snapshot이 있다. 하지만 품질 실패를 무시하고 일부 행만 골라 쓰면
성공한 cycle만 사후 선택하게 되므로 historical trial로 승격하지 않았다.

## Alpaca SIP historical profile

AAPL 전용 canonical store에는 20개 정규장 dataset, 각 390개씩 총 7,800개의 검증된
SIP minute bar가 있다. canonical dataset replay는 성공했다. 다만 이 자료는 다음
intraday challenger 입력을 제공하지 않는다.

- 급등주 point-in-time candidate universe와 최초 관측시각
- 당시 `prior_close`와 trailing ADV identity
- 당시 quote 기반 spread
- 다종목 ranking과 selection coverage

따라서 AAPL 단일종목 OHLCV를 VWAP/HOD/Gap-and-Go 급등주 성과로 표현하지 않았다.
spread나 candidate context를 synthetic 값으로 채우지도 않았다.

## 결론

- 실제 historical trial 신규: `0`
- 성과·승격·champion claim: `0`
- provider/account/order mutation: `0`
- 현재 실행 가능한 source-backed 예제: repository 1-session fixture, Reviewer `hold`

다음 실제 데이터 milestone은 품질 게이트를 통과한 KIS/Alpaca 결합 session을 그대로
누적하는 것이다. 최소 20 clean session과 30개 이상 completed trade 전에는 현재
Reviewer의 promotion threshold에도 도달하지 않는다. 수집 실패를 0수익으로 바꾸거나
현재 AAPL profile을 전체 급등주 universe로 대체하지 않는다.
