# Multi-market shadow trial v5 체크포인트

## 원장 계약

global experiment ledger schema v5는 `multi_market_trials`와 `multi_market_trial_events`를 추가한다. trial은 exact multi-market strategy version, experiment scope와 `StrategyLaneRef`를 부모로 요구하며 v1에서는 single-lane `shadow_forward`만 허용한다. event는 `started` sequence 1과 하나의 terminal sequence 2까지만 previous-key chain으로 append할 수 있다.

v1~v4 migration은 기존 payload와 key를 재작성하지 않는다. 두 table, 두 index와 네 UPDATE/DELETE 차단 trigger를 한 transaction에서 추가하며 current-version schema object가 하나라도 빠지거나 더해지면 Reader와 Writer가 fail-closed한다.

## KR day 연결

- exact lane: `kr_equities/day_trading/theme_leader_vwap_reclaim`
- strategy: 등록된 code-coupled shadow version만 허용
- timing: 다음 평일 KST 09:00 전에 register, 09:00 이후 start
- entitlement: `KIS_read_only_domestic_quotes`
- fixed budget: no-entry baseline, entry ask+20bp, missing evidence 0, 20 forward sessions, 30 completed signals, fillability/drawdown/stability/multiple-testing review
- generic writer로 만든 budget·session·parent 변형 trial은 전용 start API에서 차단

## 검증

- focused trial/schema/bootstrap/CLI: `99 passed`
- 전체 회귀: `2669 passed`
- actual CLI `--help`: exit `0`, local-only shadow register/start 확인
- missing lineage register: exit `1`, experiment DB 생성 `0`, private blocked report 확인
- register/replay: trial 신규/재사용 `1/0` 뒤 `0/1`
- start/replay: event 신규/재사용 `1/0` 뒤 `0/1`
- schema `5`, database/report mode `600`, external mutation `0`
- Ruff, changed-file format, basedpyright, compileall, no-excuse: 최종 gate에서 통과

## 안전 경계

이 체크포인트는 local experiment lineage만 append한다. provider, credential, KIS/LS/Alpaca network, 국내 계좌·잔고·포지션·주문을 호출하지 않는다. shadow fill, terminal event, lifecycle, champion과 주문 권한은 아직 닫혀 있다.

v1 timing은 평일과 KST 09:00만 검증한다. KRX 휴장일·임시 세션을 판정하는 authoritative calendar gate는 아직 연결하지 않았으므로 실제 forward register/start 전 운영 gate에서 별도로 fail-closed해야 한다.

## 다음 단계

current KR TradeSignal을 exact started trial에 결합하고, 현재 ask에 20bp adverse slippage를 더한 보수적 entry, stop-first same-bar ambiguity와 EOD time exit를 append-only shadow fill artifact로 기록한다. 그 artifact가 완전한 날만 terminal event와 독립 Reviewer 입력으로 사용한다.
