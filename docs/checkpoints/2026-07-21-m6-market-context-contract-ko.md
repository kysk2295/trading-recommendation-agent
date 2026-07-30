# Milestone 6: MarketContextSnapshot 계약 체크포인트

## 범위

설계 §8.2 `MarketContextSnapshot` 을 코드 계약으로 고정했다.
이 단계는 **시장 국면 연구 스냅샷**만 다루며 options/futures 수집·주문 권한은 없다.

## 구현

`trading_agent/market_context_models.py`

- `MarketRegimeLabel` — risk_on/off, high/low vol, trending, mean_reverting, illiquid, unknown
- `MarketContextSnapshot` — market_id, observed/valid, regime labels, breadth/vol features, macro refs, coverage, producer_version
- 권한 플래그: `order_authority` / `allocation_authority` / `lifecycle_authority` 모두 `False` 고정
- `UNKNOWN` 은 단독일 때만 허용 (다른 regime과 혼합 금지)
- `MarketContextBindingRule` — 전략이 context를 쓰려면 exact producer version + max age를 사전등록
- `context_is_usable` — as-of 신선도·version·unknown 정책 검사

## 검증

- `tests/test_market_context_models.py` 3 passed
- Ruff / basedpyright clean
- network 0 · broker mutation 0

## 다음 M6 조각

1. US breadth/realized-vol 등 read-only producer (canonical events 기반)
2. options chain · quote · OI 계약
3. futures roll security-master 확장
