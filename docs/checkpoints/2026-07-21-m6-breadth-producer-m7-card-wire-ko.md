# M6 breadth producer + M7 KR card wire 체크포인트

## Milestone 6 — breadth/vol read-only producer

`trading_agent/market_context_breadth_producer.py`

- 입력: universe member별 `session_return_bps`, `relative_volume_bps`
- 출력: `MarketContextSnapshot` (producer `market-context-breadth-v1`)
- feature: advance/decline, advance_decline_ratio, median_abs_return_bps, up_volume_share 등
- regime: risk_on/off, high/low vol, trending, mean_reverting, unknown
- network·주문·allocation 권한 없음
- 동일 입력 → 동일 `context_id` / snapshot

검증: `tests/test_market_context_breadth_producer.py`

## Milestone 7 — intraday CLI 추천 카드 발행

`run_kr_theme_day_intraday.py`

- shadow signal이 있으면 `kr_theme_day_recommendation_card.ko.md` 를 mode 600으로 기록
- aggregate report는 종목 비식별 유지 (`005930` 없음)
- 카드는 종목·손절·목표·**KR shadow-only / 국내 주문 없음** 포함
- report에 `recommendation card: written|none`

검증: `tests/test_kr_theme_day_intraday_cli.py`

## 다음

- M7: 실제 열린 KRX 세션 open-smoke evidence (운영)
- M6: options chain 계약
- M5: social connector (자격 후)
