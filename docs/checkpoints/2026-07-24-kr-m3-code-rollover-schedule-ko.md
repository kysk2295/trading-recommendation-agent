# KR M3 Code-version Rollover와 세션 예약 체크포인트

- 작성일: 2026-07-24 KST
- 구현 SHA: `caf5901e6d78dd422babf8aed46b2ef8b39e7440`
- 상태: rollover·replay·push·세션 예약 완료, actual 실행 대기

## 닫은 결손

기존 KR research registration은 hypothesis와 strategy version을 한 manifest에서 동시에 등록했다. 같은 exact hypothesis 아래 새 runtime code version을 추가하면 hypothesis의 과거 `ledger_recorded_at`을 재사용하거나 새 시각으로 immutable conflict를 만드는 선택지만 있었다. 이 상태에서는 최신 orchestration fix를 old strategy identity로 실행하거나 오늘 세션을 소급 등록할 위험이 있었다.

`run_kr_theme_research_rollover.py`는 다음을 모두 먼저 검증한다.

- 등록된 Opportunity/day base manifest와 exact hypothesis/version
- base Opportunity policy와 base runtime code identity
- 새 code version의 40자 clean Git SHA
- aware recorded-at과 기존 partial rollover 부재

그 뒤 Opportunity/day 새 version 두 건을 한 global experiment Writer transaction으로 append한다. 새 policy와 두 registration 전체를 담은 bundle은 mode-600 immutable file로 먼저 확정한다. exact replay는 최초 recorded-at과 artifact를 재사용하고 version을 늘리지 않는다.

## Actual rollover

- runtime SHA: `caf5901e6d78dd422babf8aed46b2ef8b39e7440`
- Opportunity version: `kr-theme-keyword-projection-v1-code-bd575d4e7a2f0bb8`
- day version: `kr-theme-leader-vwap-reclaim-v1-code-bd575d4e7a2f0bb8`
- first/replay exit: `0/0`
- first/replay version append: `2/0`
- policy SHA-256: `e980e0fb42caa04f91f1b4efe9f3bff0a64e02cf39e08a92f5f0df2bb966e263`
- bundle SHA-256: `e3b2742bfcde919b75e728b7807f63158e9313cc05dbca2bd6ef3cfb2a89c2e2`
- policy/bundle/report mode: `600`

## 2026-07-24 actual 세션 예약

- label: `ai.trading-agent.kr-m3-20260724`
- frozen runtime: `/private/tmp/trading-agent-kr-m3-20260724-caf5901`
- PID at registration: `94276`
- state/runs: `running/1`
- wrapper/claim/stdout/stderr mode: `700/700/600/600`

예약 chain은 별도 integration output과 delivery store만 사용한다.

1. 08:55 KST official KIS current calendar GET
2. exact composite와 current-code day trial 사전등록
3. 09:00 KST exact STARTED
4. 09:05 KST OpenDART, LS NWS, KIS ranking, local volume surge strict same-cycle
5. unique Opportunity이 있을 때만 onboarding과 KIS GET-only shadow tick
6. 15:32 KST terminal, delivery, independent Reviewer와 lifecycle

현재 mode-600 KIS/LS 설정은 있으나 OpenDART 설정은 없다. 실행 시에도 없으면 four-source cycle은 fail-closed하고 trial은 `CENSORED` data-quality evidence로만 닫는다. 이는 clean session이나 전략 성과가 아니다. 기존 2026-07-23 KR finalizer와 Hermes process는 변경·중단·재시작하지 않았다.

## 검증

- rollover와 기존 registration focused: `11 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- CLI `--help`, bad SHA, happy, exact replay: `0/1/0/0`
- wrapper `zsh -n`: 통과
- frozen runtime exact SHA/clean: 통과
- account/order/Paper mutation: `0`
