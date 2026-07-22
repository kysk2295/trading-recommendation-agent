# KR Day ACTIONABLE Hermes 전달 체크포인트

작성일: 2026-07-22 KST

## 판정

- KR same-cycle Opportunity의 WATCH event가 같은 Hermes delivery 원장에 먼저 존재할 때만 KR Day shadow
  entry 신호를 ACTIONABLE reply event로 append한다.
- 동일 신호 재실행은 shadow entry와 delivery event를 추가하지 않는다.
- 한국어 RecommendationCard는 현재 진입가, 손절, 목표, 무효화, 관측·유효시각과 KR shadow-only
  경계를 표시한다.
- 실제 열린 KRX session의 카드·Telegram acknowledgement는 아직 없다. 따라서 M1과 M3의 운영 완료
  증거가 아니며 Allocation gate도 열리지 않는다.

## 구현 경계

`run_kr_theme_day_intraday.py`는 별도 `--delivery-database`를 필수로 받는다. experiment, market receipt,
shadow entry와 delivery DB는 모두 달라야 하며 report/card가 DB 또는 sidecar를 덮을 수 없다.

shared Hermes projection kernel은 typed `TradeSignalEnvelope`를 직접 검증해 다음 계약으로 변환한다.

- kind: `actionable`
- market/agent: `kr_equities/day_trading`
- status: `current_quote_validated`
- occurred_at: 신호의 causal `observed_at`
- root: exact `<opportunity_id>:<symbol>` WATCH source

root WATCH가 같은 delivery store에 없으면 append는 conflict로 차단된다. event 시각은 downstream worker의
30초 current-market gate를 그대로 통과해야 하므로 지연된 과거 진입 알림은 발송되지 않는다.

KR Day session manifest는 schema v3에서 `delivery_store`를 immutable path로 고정한다. onboarding,
supervisor와 intraday child가 같은 경로를 사용하며 entry phase source attestation은 shadow entry뿐 아니라
대응 ACTIONABLE delivery identity도 포함한다. repository `outputs/`에는 이전 KR session manifest가 없어
운영 artifact migration은 발생하지 않았다.

## 수동 QA

실제 `run_kr_theme_day_intraday.py` subprocess surface에서 확인했다.

- `--help`: exit 0, `--delivery-database` 노출, order command surface 없음
- delivery DB와 entry DB alias: exit 1, shadow entry DB 생성 없음
- fixture happy path와 exact replay: 둘 다 exit 0, stdout/stderr 비어 있음
- delivery 원장: WATCH 1건 + ACTIONABLE 1건, ACTIONABLE root가 WATCH delivery ID와 일치

macOS `/var` symlink fixture는 private-file trust boundary에서 의도대로 차단됐다. canonical
`/private/var` 경로로 다시 실행해 위 happy path를 확인했다.

## 검증

- 실패 우선 CLI: `--delivery-database` 미지원으로 4 failed
- session manifest 실패 우선: v3 delivery path 미지원으로 20 failed
- KR session manifest/onboarding/supervisor/child 회귀: 28 passed
- KR/Hermes 인접 회귀: 64 passed
- verifier 회귀: 9 passed
- 전체 pytest: **3264 passed in 185.61s**
- 전체 Ruff: 통과
- 전체 basedpyright: **0 errors, 0 warnings, 0 notes**
- changed Python compileall: 통과
- no-excuse: 15 files, violation 없음

## 안전과 남은 운영 증거

이번 구현과 QA는 fixture와 임시 mode-600 SQLite만 사용했다. 실제 Telegram 전송, Alpaca Paper POST/DELETE,
KIS·LS account/order mutation은 0건이다. 위험한도와 Paper arm 계약도 변경하지 않았다.

2026-07-22 실제 KR source 수집은 KIS ranking, LS NWS와 local volume-surge까지 성공했지만 OpenDART 설정과
장전 composite/trial이 없어 four-source collection cycle을 확정하지 않았다. 다음 열린 KRX session에서
사전등록과 four-source cycle을 먼저 충족한 뒤 WATCH → ACTIONABLE/card → Telegram ACK → shadow exit/review를
실제 시각으로 완주해야 M1/M3 운영 증거가 된다.
