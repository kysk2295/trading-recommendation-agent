# KR Projection Output Guard 체크포인트

## 완료 범위

- `run_kr_theme_projection.py`는 `KrThemeStore`와 동일하게 resolve한 `--database`와 SQLite writer lock, journal, shared-memory, WAL sidecar를 projection output 아래의 `opportunities.v1.jsonl`, `kr_theme_projection_summary_ko.md`와 비교한다.
- SQLite ledger 또는 sidecar가 두 산출물 중 하나와 경로 또는 같은 device/inode(hard link)로 겹치면 `KrThemeStore`를 열거나 classification을 append하기 전에 안전한 CLI 오류로 종료한다. 대상 산출물이 symlink인 경우도 닫힌다.
- KR projection JSONL outbox와 한국어 요약은 owner-only mode `600`으로 기록한다. 새 JSONL은 generic append 전에 `0600`으로 생성하고, corrupt outbox 실패, zero-projection replay, 일반 replay를 포함해 기존 outbox/report 권한도 `600`으로 복구한다. 새 zero-projection output에는 빈 JSONL을 만들지 않는다.
- 공용 `contract_outbox.py`는 변경하지 않았다. 이 경계는 KR projection CLI에만 적용된다.

## 검증

- focused projection/outbox 회귀: `uv run pytest -q tests/test_kr_theme_projection_cli.py tests/test_contract_outbox.py` -> `47 passed`
- 전체 자동 게이트: `uv run pytest -q` -> `1597 passed`
- 정적 분석: `uv run ruff check .` -> `All checks passed!`
- 타입 검사: `uv run basedpyright` -> `0 errors, 0 warnings, 0 notes`
- diff hygiene: `git diff --check` -> 성공
- 수동 CLI QA: `./run_kr_theme_projection.py --help`가 run manifest, database, output directory, help만 표시하고 exit 0으로 성공했다.
- 수동 CLI QA: 존재하지 않는 manifest는 exit 2로 종료했고, 새 임시 경로에 database나 output 파일을 만들지 않았다.
- 수동 CLI QA: committed synthetic ingest 뒤 첫 projection은 classification 1건, 신규 1건, theme Opportunity 1건, 신규 1건으로 성공했다.
- 수동 CLI QA: 동일 manifest/database/output 재실행은 classification과 Opportunity 모두 신규 0건으로 끝났고 JSONL은 1행을 유지했다.
- 수동 CLI QA: 첫 실행과 replay 뒤 `opportunities.v1.jsonl`, `kr_theme_projection_summary_ko.md` 모두 mode `600`이었다.
- 독립 코드 리뷰: 새 JSONL이 append 전에 private mode로 준비되는지, output artifact symlink와 hard-link alias가 모두 ledger open 전 차단되는지 회귀 테스트로 고정했다.
- 후속 독립 코드 리뷰: database symlink가 실제 sidecar 경로를 바꾸는 경우와 zero-projection replay의 기존 outbox 권한을 보강했다. 네 resolved sidecar와 두 artifact의 hard-link alias는 모두 `KrThemeStore` open 전 차단한다.
- 최종 리뷰의 P2 보강: zero-projection의 새 output은 outbox를 만들지 않고, 기존 outbox만 owner-only mode로 복구하는 두 경로를 함께 회귀 검증했다.

## 이번 단계에서 하지 않은 일

- KR provider, LS/KIS account endpoint, 국내 계좌·주문: 0건
- LLM, TradeSignal, broker, Alpaca 호출 또는 Paper mutation: 0건
- 외부 메시지·Telegram 전송: 0건

이 단계는 local-only KR keyword theme Opportunity projection의 파일 경계 강화다. 실시간 수급/뉴스 수집, 추천 전달, 가격·VI risk gate, shadow fill, 성과 평가, 국내 주문 권한은 추가하거나 열지 않는다.

## 다음 단계

OpenDART 설정 파일이 owner-only 상태로 준비되고 현재 KST·정상 endpoint 조건이 동시에 맞을 때만 bounded production same-cycle source collection을 read-only로 수행한다. immutable source coverage가 완성된 뒤에만 별도 manifest로 production KR keyword projection을 실행한다. 어느 단계도 TradeSignal이나 국내 주문을 열지 않는다.
