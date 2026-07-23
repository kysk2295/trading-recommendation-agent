# 2026-07-23 실시간 검증 예약 체크포인트

## 예약 결과

clean commit `0c7dc575301862d3cf0d98c6d9c16c69111783fb`의 detached runtime을 사용해
현재 US forward watch 뒤에 필요한 실제 장중·장마감 검증을 one-shot launchd job으로
연결했다. 기존 KR finalizer, Hermes delivery service와 US forward watch는 변경하거나
재시작하지 않았다.

| launchd label | 실행 조건 | 검증 |
|---|---|---|
| `ai.trading-agent.us-forward-20260723` | 장전부터 정규장 종료까지 | KIS ranking, candidate/watch, EOD, metrics, daily research, adaptive evaluation |
| `ai.trading-agent.us-day-preflight-20260723` | 09:30~15:30 EDT, watch DB 변경 시 | current completed bar와 setup, Alpaca Paper GET/WSS readiness, broker/shadow reconciliation |
| `ai.trading-agent.alpaca-sip-smoke-20260723` | 09:35 EDT | AAPL SIP trade stream 최대 3 frame read-only smoke |
| `ai.trading-agent.us-day-finalizer-20260723` | watch 종료 뒤, 최대 16:15 EDT | flat broker 상태, reconciliation, real scheduled-session terminal |
| `ai.trading-agent.intraday-dataset-20260723` | watch 종료 뒤, 최대 16:30 EDT | strict quality gate를 통한 causal research CSV materialization |

KR의 당일 data-quality censored trial은 기존
`ai.trading-agent.kr-m3-finalize-20260723`가 15:32 KST에 terminal, 독립 Reviewer와
lifecycle evidence로 닫는다.

## 권한 경계

- SIP smoke는 `--arm-read-only`만 사용하고 계좌·주문 endpoint를 호출하지 않는다.
- US Day observer와 finalizer는 Paper account/order state를 GET/WSS로 대사하지만
  `PaperMutationArm`을 소비하지 않고 POST/DELETE를 수행하지 않는다.
- signed Hermes arm 설정과 confirmed one-time request가 없으므로 실제 Paper mutation
  job은 예약하지 않았다. scheduler가 arm을 생성하거나 확인을 대행하지 않는다.
- KR four-source 다음 세션은 OpenDART private 설정이 없어서 complete source cycle을
  만들 수 없다. 품질 gate를 완화하거나 source 실패를 성공으로 바꾸지 않는다.

## 배치 전 검증

- 네 신규 wrapper `zsh -n`: 모두 exit `0`
- 네 신규 wrapper `--dry-run`: 모두 exit `0`
- 네 신규 wrapper unknown argument: 모두 exit `2`
- wrapper mode: 모두 `700`
- frozen runtime의 preflight, SIP smoke, dataset CLI help: 모두 exit `0`
- 신규 launchd job: 모두 `running`, run count `1`, terminal exit 미발생
- 기존 KR finalizer, Hermes delivery service, US forward watch: 기존 PID 유지

실제 장중 결과는 각 private mode-600 stdout/stderr/event artifact와 terminal/dataset
artifact로 판정한다. 예약 상태 자체는 readiness, clean session, Paper 실행 또는
성과 증거가 아니다.
