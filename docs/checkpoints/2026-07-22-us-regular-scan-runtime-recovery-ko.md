# US 정규장 scan runtime 복구 체크포인트

기준일: 2026-07-22

## 실제 장애

- 사전등록된 ORB shadow trial은 정규장 시작에 성공했다.
- 첫 정규장 scan부터 연속 exit code `1`이 기록됐고 opportunity와 signal outbox는 생성되지 않았다.
- standalone scan executable의 `uv run --with` 선언에 startup import가 요구하는 DuckDB와 PyArrow가 없었다.
- launchd cwd에서는 project dependency가 상속되지 않아 `ModuleNotFoundError`가 발생했다.
- 실패한 audit 두 파일은 즉시 mode `0600`으로 좁혔다.

## 코드 수정

- `run_kis_paper_scan.py`를 PEP 723 `uv run --script` 계약으로 바꿨다.
- 추적된 project runtime dependency 제약을 script metadata에 명시했다.
- PEP 723 dependency metadata를 구조적으로 파싱하는 regression test를 추가했다.
- 기능 커밋: `61d62755aee1bb5f30c5f034e53957d7387d063e`
- metadata parser 보강: `b850449`
- 검사 marker 정리: `d2a9973`

## 당일 비중단 복구

- clean detached runtime: `/private/tmp/trading-agent-runtime-20260722-61d6275`
- launchd label: `ai.trading-agent.us-orb-20260722`
- recovery wrapper에는 experiment registration, Paper arm, broker order 또는 mutation 인자가 없다.
- anti-lookahead gate가 새 code SHA의 당일 trial 재등록을 정상 거부했으므로 우회하지 않았다.
- 사용자-facing read-only ORB scan만 재개했고 기존 trial 결과와 사후 혼합하지 않는다.
- 복구 후 연속 실제 정규장 scan 두 번이 exit `0`으로 끝났다.
- 각 cycle은 KIS ranking 6개와 NYSE halt source coverage를 모두 complete로 기록했다.
- current opportunity outbox가 mode `0600`으로 생성됐고 실제 Hermes WATCH가 Telegram ACK까지 도달했다.

## stale 전달 대사

- 빠르게 교체되는 과거 WATCH는 기존 freshness 정책이 `market_event_ineligible`로 terminal 억제한다.
- 이전 reconciliation은 이 의도적 억제를 외부 전송 실패와 함께 계산해 영구 `complete=false`가 됐다.
- reconciliation v2는 stale suppression만 별도 집계한다.
- `telegram_timeout` 등 실제 전달 실패는 계속 hard dead-letter이며 완료를 차단한다.
- 기능 커밋: `007f20f68121e72ebe162b86cd1b26d911c31a1a`
- clean projector runtime: `/private/tmp/trading-agent-projector-20260722-007f20f`
- 10:22 EDT 최신 projector 대사: expected `19`, acknowledged `10`, suppressed `9`, pending `0`, complete `true`
- 해당 시점 signal outbox는 없었으므로 새 ACK는 실제 opportunity WATCH 전달 증거다.
- report는 mode `0600`이며 platform message ID, chat ID와 credential을 포함하지 않는다.
- query-only replay 전후 production delivery DB 상태는 불변이었다.

## 검증

- launcher/scan 집중 회귀: `41 passed`
- delivery suppression/terminal 집중 회귀: `11 passed`
- 최종 전체 회귀: `3314 passed in 185.62s`
- 저장소 전체 Ruff: 통과
- 저장소 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- 변경 파일 compileall, diff check, no-excuse: 통과
- CLI `--help`: exit `0`
- 잘못된 session date: exit `2`
- 실제 production reconciliation replay: delivery DB mutation `0`

## 남은 당일 gate

- 실제 WATCH 전달은 복구됐지만 M1 전체 완료는 장후 terminal ACK까지 기다린다.
- 오늘 시작된 연구 trial은 startup collection incident를 포함하므로 성과 근거로 사용하지 않는다.
- 명시적 one-use Paper arm이 없으므로 실제 Paper POST는 계속 금지 상태다.
- live-money endpoint, KIS/LS mutation, 계좌·주문·포지션 변경은 사용하지 않았다.
