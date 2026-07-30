# Paper smoke 진행 + 추천 카드 품질 체크포인트

## 범위

- 우선순위: Alpaca Paper 정규장 smoke 1순위, 추천 카드 품질 최우선.
- 실제 자금 거래 없음. Paper endpoint `https://paper-api.alpaca.markets` 고정.

## Paper smoke 결과 (2026-07-21 NYSE regular)

### 통과한 GET-only 단계

| 단계 | 결과 |
|---|---|
| bootstrap | 기존 계좌 결합 exact replay |
| lane bootstrap | already_registered |
| experiment ledger bootstrap | strategy version/authority/lifecycle 신규 4 (code-coupled) |
| preflight | 준비 예 · 미체결 0 · 포지션 0 |
| readiness | 시장 개장 예 · WSS 인증·구독·Pong · REST·원장 대사 통과 |
| recovery (최종) | open order 0 · position 0 · FILL 0 |
| preflight (최종) | flat 유지 |

산출 경로: `outputs/paper_execution/smoke/20260721T102245/`

### ORB watch · 추천 관측

- `outputs/live_sessions/20260721/` 에서 KIS ORB watch 실행.
- experiment-ledger 포함 watch는 **장전 trial 사전등록 창 이후**라 `register → blocked_source`로 전체 watch가 차단됨.
  - 원인: `register_orb_shadow_trial`은 `registered_at < regular_session open` 만 허용.
- experiment-ledger **없이** watch 실행 → 후보 10 · **조건부 추천 1건 (BEG / ORB)**.
- BEG timeline:
  - 10:24:49 ET setup 생성 (entry 38.0890 / stop 36.88 / 1R 39.2981 / 2R 40.5071)
  - 10:25:00 ET 같은 다음 완료 봉에서 active + **1R**
  - 10:27:00 ET **2R**
- Paper entry smoke:
  - stale/non-setup source → `InvalidCurrentOrbPaperEntrySourceError` 로 **mutation 전 차단** (의도된 fail-closed).
  - 추가 8회 scan loop에서도 `state=setup` 이면서 age ≤ 30s 인 후보 0건 → **POST/DELETE 0건 유지**.

### 해석

기능 smoke의 GET/WSS/원장 경로는 열린 정규장에서 검증됐다.
armed entry는 **exact current setup 30초 창**이 필요하며, 급등 종목이 생성 직후 1R/2R에 도달하면 창이 닫힌다. 값을 완화하거나 새 DB로 우회하지 않았다.

## 추천 카드 품질 개선

### 변경 파일

- `trading_agent/replay.py` — paper `recommendations_ko.md` · alert outbox 카드 강화
- `trading_agent/contract_outbox.py` — trade-signal 한국어 카드 강화 · 가격 자릿수 정리
- `trading_agent/kis_scan_report.py` — 스캔 요약에 카드/entry 해석 가이드 추가
- `tests/test_alert_outbox.py`, `tests/test_contract_outbox.py` — 기대 카드 갱신

### 카드에 추가된 핵심 필드

- 시장 `us_equities`, 에이전트 `day_trading`, 전략 lane, 추천/신호 ID
- 실행 가능성 · **현재 진입 가능: 아니오(조건부)** · **주문 권한: 없음**
- 주당 계획위험(R), 예상 보유(당일 time_exit), 같은 봉 손절 우선
- 빈 추천 시 “무엇을 의미하는지 / 무엇을 확인할지” 명시
- 신호 카드 가격 표시의 부동소수 잔여 자릿수 축소

기존 BEG 원장을 개선된 `write_report`로 재투영해 `recommendations_ko.md`를 갱신했다. (alert outbox immutable 카드 본문은 최초 queue 시점 내용 유지)

## 검증

- `uv run pytest tests/test_alert_outbox.py tests/test_contract_outbox.py tests/test_kis_watch.py` → **36 passed**
- Ruff / basedpyright on changed modules → **clean**
- 실제 Paper POST/DELETE: **0건**
- 최종 flat preflight: **통과**

## 다음 세션 액션

1. **NYSE open 전** experiment ledger ORB trial `register` (장중 등록 불가).
2. open 직후 watch + entry 연동을 **같은 운영 루프**로 두어 setup 30초 창을 놓치지 않기.
3. setup이 살아 있는 종목에서만 armed entry → protective OCO → flatten 완료.
4. 카드에 lifecycle/forward 표본 요약은 Reviewer 원장 query-only 결합 후 단계적으로 추가.
