# KR same-cycle Opportunity 운영 연결 체크포인트

## 범위

`run_kr_same_cycle_opportunity.py`는 기존 `DART → LS NWS → KIS ranking → volume_surge → final cycle` 수집기와 등록된 KR theme Opportunity projector를 one-shot 운영 경로로 연결한다. 새 분류 모델이나 주문 경로를 만들지 않고, 같은 append-only KR 원장과 기존 `kr_equities/opportunity_manager/theme_momentum` outbox 계약을 재사용한다.

## 운영 계약

- provider를 열기 전에 policy의 code-coupled strategy version이 global experiment ledger의 exact shadow Opportunity lane에 등록됐는지 확인한다.
- production에서 collection date는 실행 시점 KST 날짜와 같아야 한다. historical fixture는 명시적 `--fixture-root`에서만 허용한다.
- 네 source terminal run의 ID·adapter version·collection date와 final cycle의 시작·완료·coverage를 다시 계산해 exact equality를 요구한다.
- 첫 production projection은 final cycle 완료 이후이며 policy age 이내여야 한다. 설정 가능한 상한은 300초다.
- policy는 runtime code version, producer strategy version, immutable keyword rules, Opportunity validity와 maximum cycle age를 한 객체로 고정한다.
- cycle ID digest 아래 mode-700 디렉터리에 canonical `policy.json`, `keyword-rules.json`, `projection-run.json`을 mode 600·single-link로 보존한다. 세 파일은 별도 private staging에서 fsync한 뒤 directory rename으로 한 번에 publish하며 중간 write 실패는 partial final bundle을 노출하지 않는다.
- existing bundle replay는 현재 시각으로 분류를 다시 만들지 않는다. 기존 projection 시각과 exact rules를 재사용해 classification/outbox crash만 복구한다.
- valid cycle에서 positive theme가 없으면 실패로 위조하지 않고 `no_opportunity`로 종료한다.

CLI에는 account, broker, order, arm 또는 임의 endpoint 옵션이 없다. KIS/OpenDART/LS는 기존 read-only adapter만 사용하고 국내 계좌·주문 mutation은 열지 않는다.

## Fixture E2E

committed same-cycle fixture와 synthetic policy를 실제 CLI로 두 번 실행했다.

- 첫 실행: source 4개 신규 terminal, keyword classification 3건, theme Opportunity 1건
- exact replay: source 4개 재사용, 신규 classification 0건, 신규 Opportunity 0건
- 최종 outbox: 1행
- immutable run bundle: 1개
- outbox, operator report, projection manifest mode: 모두 600
- report: `ready`, Opportunity 1건, order authority false, account/order mutation 0

미등록 policy와 다른 KST 날짜의 non-fixture 요청은 collector 또는 KR source database 생성 전에 exit 1로 차단한다. 인자 누락은 실제 CLI에서 exit 2, `--help`는 exit 0이다.

## 검증

- new focused: `8 passed`
- related source-cycle/projection regression: `93 passed`
- full pytest: `2767 passed`
- Ruff check 전체: 통과
- basedpyright 전체: errors 0, warnings 0
- changed Python format check: 통과
- compileall, JSON parse, `git diff --check`: 통과
- no-excuse production grep: `Any`, `object`, `cast`, `type: ignore`, `noqa` 0건

저장소 전체 `ruff format --check .`는 이번 변경과 무관한 기존 169개 파일을 미포맷으로 보고한다. 해당 파일은 사용자 작업을 보존하기 위해 수정하지 않았다.

## 남은 운영 검증

1. 열린 KRX 세션에서 새 cycle ID로 bounded KIS/OpenDART/LS production read-only 전체 실행
2. 실제 검토된 production theme rules와 clean checkpoint code version 사전등록
3. 장중 생성 Opportunity을 기존 KR day shadow session에 안전하게 동적 onboarding하는 계약
4. LS 기사 본문 `t3102`, 추가 수급·VI evidence와 별도 분류 품질/Human audit

fixture 결과는 실시간 coverage, 분류 정확도, 추천 성과 또는 수익성 증거가 아니다.
