# M5 SEC document claim + M7 KR recommendation card 체크포인트

## Milestone 5 — filing document claim

`trading_agent/us_sec_filing_document_research_extraction.py`

- 성공한 `SecFilingDocumentTarget` + `Run` + `RawResponse`만 허용
- source: `sec/edgar_documents`
- claim_key: `us.symbol.attention.{symbol}` (submission/news와 교차 가능)
- body NLP·sentiment 없음 (`no_body_nlp`, `document_receipt`)
- 호출자가 symbol binding을 명시 (ticker 추정 없음)
- submission metadata claim과 결합 시 read model `corroborated`

검증: `tests/test_us_sec_filing_document_research_extraction.py` 통과

## Milestone 7 — KR theme day recommendation card

`trading_agent/kr_theme_day_recommendation_card.py`

- `theme_leader_vwap_reclaim` lane 전용 한국어 카드
- **국내 주문·Paper 경로 없음** 명시
- 주문 권한 없음 / shadow-only

`trading_agent/contract_outbox.py` 공용 signal card:

- 시장 라벨을 `us_equities` / `kr_equities` 에 따라 분기
- KR은 shadow·국내 주문 부재 문구 사용 (미국 하드코딩 제거)

검증: `tests/test_kr_theme_day_recommendation_card.py`, `tests/test_contract_outbox.py` 통과

## 권한·네트워크

- provider GET 0
- 국내/미국 주문 mutation 0

## 다음

1. M5: social official connector (자격 확보 후) 또는 news-catalyst day service 운영 정리 commit
2. M7: KR open-session production evidence / open-smoke
3. M6: market context snapshot 계약
4. M1/M4: 시장 세션 조건 시 운영 smoke
