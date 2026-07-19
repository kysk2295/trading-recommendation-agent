# Alpaca SIP quote actionability durable store 체크포인트

## 완료 계약

- 하나의 frozen artifact envelope가 base conditional publication, full dynamic microstructure bundle, provider-neutral policy evidence, terminal assessment와 optional derived publication을 함께 보존한다.
- artifact ID는 base signal + scan cycle의 assessment ID와 같고 전체 envelope는 저장 전 같은 bundle로 deterministic 재평가된다.
- canonical sorted JSON bytes와 SHA-256을 mode-600 current-owner single-hard-link SQLite에 append한다.
- store는 exact table/trigger object set, schema version, row metadata, payload hash, canonical bytes와 nested Pydantic/dataclass 계약을 read마다 다시 검증한다.
- `BEGIN IMMEDIATE` single writer와 no-update/no-delete trigger를 사용한다.
- exact replay는 no-op이고 같은 base+scan identity의 다른 bundle/status/payload는 conflict다.
- forged assessment는 DB 파일 생성 전에 차단된다.

## 검증

- focused adapter + store: **12 passed**
- dynamic SIP + KIS actionability/outbox related: **90 passed**
- full suite: **2499 passed**
- Ruff, changed-file format, basedpyright 0 errors/0 warnings, compileall, no-excuse rules 통과
- fault injection: SQL update, mode 0644, hard link, missing append-only trigger 모두 fail-closed
- manual library restart QA: first append 1, exact replay append 0, record 1, plan/epoch match, `validated_waiting`, `current_quote_validated`, mode 0600 확인
- provider·credential·network·account/order endpoint 호출과 mutation 0건

## 남은 경계

- 이 store는 self-contained actionability evidence publication이며 기존 KIS JSONL이나 Paper execution ledger를 수정하지 않는다.
- runtime owner가 현재 conditional signal과 exact completed bundle을 선택해 이 API를 호출하는 orchestration은 아직 연결하지 않았다.
- 외부 Telegram delivery/ack와 사용자-facing always-on signal feed는 후속 milestone이다.
- durable `current_quote_validated` publication도 주문 intent가 아니며 explicit Paper arm/account/risk/session gate를 우회하지 않는다.
