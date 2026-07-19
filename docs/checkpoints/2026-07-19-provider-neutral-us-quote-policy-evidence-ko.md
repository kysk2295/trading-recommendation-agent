# Provider-neutral US quote policy evidence 체크포인트

## 완료 계약

- 공통 `UsQuotePolicyEvidence`는 provider 이름 대신 quote ID, exact source `EvidenceRef`, symbol, provider/receipt 시각, bid/ask·잔량·spread만 보존한다.
- evidence source 관측시각과 provider quote 시각은 exact match여야 하고, 가격·spread·identity shape가 유효하지 않으면 생성되지 않는다.
- 공통 terminal policy와 derived publication은 이 evidence만 소비한다.
- 기존 KIS schema v2 snapshot은 `quote/snapshot` reference를 가진 evidence로 투영되며 외부 schema, ID 공식, outbox 파일과 평가 순서는 바뀌지 않는다.
- 공통 artifact matcher가 base signal, evidence, assessment와 derived publication을 독립 재검증하고 KIS matcher는 그 위의 compatibility facade다.

## 검증

- provider-neutral + 기존 KIS actionability/outbox focused: **69 passed**
- full suite: **2487 passed**
- Ruff, changed-file format, basedpyright 0 errors/0 warnings, compileall, no-excuse rules 통과
- local library QA: KIS snapshot → `quote/snapshot` evidence → `validated_waiting` → `current_quote_validated` 확인
- provider·credential·network·account/order endpoint 호출과 mutation 0건

## 남은 경계

- 공통 evidence는 provider-specific completeness를 스스로 추측하지 않는다. adapter가 검증된 source record만 공급해야 한다.
- Alpaca SIP adapter는 dynamic microstructure bundle의 complete plan/epoch/instrument/symbol과 quote confirmation의 bid/ask venue를 먼저 검증한다.
- Alpaca source reference는 KIS `quote/snapshot`이 아닌 별도 namespace를 사용한다.
- append-only provider evidence/outbox와 fixture E2E는 다음 체크포인트에서 연결한다.
