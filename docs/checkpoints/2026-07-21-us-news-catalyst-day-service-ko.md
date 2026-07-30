# Milestone 5: US News-Catalyst Day Service 체크포인트

## 범위

M5 news-catalyst day session scheduler 위에 **host-level day service** 를 둔다.

```text
launchd/cron interval
  → run_us_news_catalyst_day_service.py tick
    → (pre-open) day session init
    → day session tick (register→…→review domain-first)
```

주문·lifecycle 승격·실계좌 권한 없음. secret-free launch agent만 생성한다.

## 구성

- `trading_agent/us_news_catalyst_day_service.py` — single-writer lease + init/tick 위임
- `trading_agent/us_news_catalyst_day_service_config.py` — private config + launch agent verify
- `run_us_news_catalyst_day_service.py` — provision / tick CLI

## 안전

- session root nonblocking file lease
- open 이후 manifest 없으면 bootstrap 차단 (`bootstrap_window_missed`)
- launch agent에 환경변수·KeepAlive·secret 없음
- config/plist mode 600

## 검증

- `tests/test_us_news_catalyst_day_service.py` 포함 day service/session 관련 회귀 통과
- provider 강제 오픈 없음

## 운영 메모

실 launchd 등록은 사용자가 host에서 `provision` 산출 plist를 검토한 뒤 수행한다. 이 저장소는 자동으로 system launch agent를 설치하지 않는다.
