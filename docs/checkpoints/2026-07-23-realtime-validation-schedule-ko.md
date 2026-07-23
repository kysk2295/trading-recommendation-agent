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

## 2026-07-24 연속 실행 확장

7월 23일 표본이 실패하더라도 다음 정규장 표본을 자동 확보하도록 7월 24일에도
US forward, 장중 preflight, read-only SIP smoke, 장마감 finalizer, strict causal
dataset job을 같은 fail-closed 경계로 예약했다. forward runtime은 retry 복구가
포함된 `d59d2534a2561472c894bfe2acb56bd051dfca90`에 고정되어 있다.

dataset 뒤의 수동 단절도 제거했다. 별도
`ai.trading-agent.intraday-research-20260724` one-shot은 dataset job이 terminal이
될 때까지 기다린 뒤 다음을 순서대로 실행한다.

1. dataset report가 `ready`이고 exact CSV/receipt가 각각 하나인지 확인한다.
2. `26b5e2538c354837c27d827a08f45ba5cdf2a45c` frozen runtime, mode-600 KIS
   historical-research entitlement, exact 세 queue card로 VWAP reclaim, HOD
   breakout, Gap-and-Go READY foundation과 v2 manifest를 결속한다.
3. 같은 CSV SHA, foundation SHA, queue snapshot을 사용해 bounded multi-strategy
   walk-forward와 query-only 독립 Reviewer를 실행한다.

dataset이 blocked이거나 artifact cardinality, runtime SHA, entitlement, queue,
foundation 중 하나라도 다르면 후속 loop는 실행하지 않는다. runner `zsh -n`,
`--dry-run` exit 0, unknown argument exit 2, mode 700을 확인했고 launchd의 run
count 1·running 상태와 stdout/stderr mode 600을 확인했다. 이 예약은 주문 권한,
lifecycle 자동 승격 또는 성과 증거가 아니다.
