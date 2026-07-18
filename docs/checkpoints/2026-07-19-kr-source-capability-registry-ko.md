# KR source capability registry 체크포인트

## 완성 범위

- 기존 KR theme SQLite 원장의 exact same-cycle terminal run 네 개를 read-only로 조회한다.
- DART, LS NWS, KIS KR ranking, local volume-surge를 고정 source identity와 entitlement에 결합한다.
- 성공·실패 health assessment를 append-only capability registry에 기록한다.
- 정상 zero-record poll은 실제 event를 만들지 않고 source heartbeat로만 freshness를 표현한다.
- local CLI는 complete `0`, incomplete `2`, validation block `1`로 종료하고 mode-600 집계 보고서를 쓴다.

## 인과성과 무결성

- exact source set, source-run ID, adapter version, collection cycle/date를 모두 재검증한다.
- heartbeat와 event 수신시각은 assessment 뒤이거나 data gate 평가시각 뒤일 수 없다.
- DART·KIS·local derived correction은 append-correction, LS news deletion은 append-tombstone 계약으로 분리한다.
- entitlement는 2026-07-15 계약 버전의 고정 발효일이며 source 관측 성공으로 새 권한을 만들지 않는다.
- registry는 mode 600, current-user regular file, no-symlink, update/delete 차단과 exact retry를 유지한다.

## 수동 QA

- `--help`: exit 0, provider arm 또는 주문 옵션 없음
- date 오입력: argparse exit 2
- complete fixture cycle: exit 0, source 4/4 resolved
- exact retry: capability 0, entitlement 0 추가
- registry와 report mode: 600

## 검증

- `pytest`: 2282 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, changed-file format, no-excuse rules: passed

## 안전 경계

- 기존 KR ledger mutation 0건
- provider·credential·network 접근 0건
- account/order endpoint와 broker mutation 0건
- complete health는 해당 bounded poll의 transport·projection 상태이며 데이터 내용의 포괄성, 전략 성과 또는 주문 권한을 뜻하지 않음

## 다음 단계

- Alpaca SIP runtime fleet audit의 종목별 minute-bar health projection
- canonical KR source event의 entity/claim/corroboration research read model
- provider별 deletion cursor와 retention 이행 상태
