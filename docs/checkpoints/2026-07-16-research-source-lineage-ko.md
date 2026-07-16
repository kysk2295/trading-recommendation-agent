# 연구 Source Lineage 체크포인트

날짜: `2026-07-16 KST`

## 완료 범위

- global experiment ledger schema v2에 append-only `ResearchSource` catalog와 기존 `HypothesisRegistration`을 감싸는 `ResearchHypothesisCard`를 추가했다.
- source는 종류, query·userinfo·fragment 없는 HTTPS 원문 URL, 공개일, 주장, 한계, 검색 시각을 보존한다. 비어 있는 주장·한계와 시간 역행은 등록 전에 거절한다.
- card는 기존 가설의 canonical payload와 source key, 경제적 메커니즘, 반증 기준을 함께 보존한다. source가 없거나 가설의 `source_registered_at` 뒤에 기록됐으면 transaction 전체를 거절한다.
- 기존 schema v1 ledger는 검증 후 하나의 SQLite transaction 안에서 v2 table·append-only trigger와 schema version만 추가한다. DDL 실패는 rollback하며 기존 hypothesis/version/trial/lifecycle 행을 UPDATE, DELETE 또는 재직렬화하지 않는다.
- `run_research_hypothesis_register.py`는 JSON manifest 하나를 local SQLite에 등록하고 redacted Korean report를 만든다. committed US swing 예시는 공개 문헌 근거 2개와 하나의 연구 가설 card만 등록한다.

## 안전 경계

- provider HTTP, WebSocket, 자격증명 loader, Alpaca Paper 계좌·주문·포지션, broker mutation import와 호출: 0건
- strategy version, trial, Reviewer event, lifecycle transition, champion·allocation 또는 Paper 권한 변경: 0건
- source card는 수익성·실시간 진입가·승격 근거가 아니라, 이후 별도 비교 실험이 검증할 수 있는 사전등록 연구 가설이다.
- SQLite DB, writer lock, CLI report는 owner-only mode `600`이며 Reader는 `mode=ro`와 `query_only`를 사용한다.

## 검증

- research source model focused suite: `5 passed, 19 deselected`
- experiment store research/migration focused suite: `3 passed, 30 deselected`
- model/store legacy regression: `57 passed`
- registration service·CLI suite: `6 passed`
- review 보강 model·store·registration·CLI suite: `72 passed`
- Ruff: `uv run ruff check .` 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- 수동 CLI QA: `--help` exit 0, 없는 manifest는 database를 만들지 않고 exit 1, committed manifest의 최초 등록과 exact replay, DB·lock·report mode `600`을 확인했다.

## 다음 단계

1. source-bound US swing 가설을 별도 설계로 preregistered strategy version·shadow trial·independent Reviewer evidence에 연결한다.
2. 동일 위험의 충분한 forward 표본, 비교 계약, Reviewer 근거가 생기기 전에는 lifecycle promotion·Paper 권한·위험 한도를 변경하지 않는다.
