# Strategy authority binding 체크포인트

## 목적

`challenger`가 자기 권한과 무관하게 `paper_champion` 또는 향후 `shadow_champion`을 선택하지 못하도록, lifecycle 전이보다 먼저 전략 버전의 연구 lane과 최대 운영모드를 immutable authority로 고정한다.

## 구현

- `StrategyAuthorityBinding`은 `strategy_version`, 정확한 `StrategyLaneRef`, `AgentOperatingMode`, 승인된 legacy 실행 lane과 `bound_at`을 결합한다.
- Alpaca Paper 운영모드는 미국 day/swing agent에만 허용한다. market-context와 다른 시장·agent는 Paper authority를 만들 수 없다.
- global experiment ledger schema v3는 `strategy_authority_bindings`를 추가한다. 전략 버전마다 한 행만 허용하고 UPDATE·DELETE를 trigger로 차단한다.
- Writer는 부모 전략의 `strategy_id`, legacy lane과 기록시각을 검증한 뒤 같은 transaction에서 append한다. exact replay는 행을 늘리지 않고, 같은 버전의 mode 변경은 immutable conflict다.
- Reader는 content key, 정규화 열, canonical payload와 부모 전략을 모두 다시 검증한다.
- v1과 v2 Writer migration은 기존 hypothesis, source, version, trial과 lifecycle payload를 재작성하지 않고 v3 객체만 원자적으로 추가한다.

## 경계

- KR `StrategyLaneRef`에는 승인된 legacy execution binding이 없으므로 구형 `LaneId`를 억지로 붙이지 않고 차단한다. KR lifecycle은 multi-market experiment scope/version v2 이후 연결한다.
- 이 체크포인트는 lifecycle enum·전이표, champion, allocation, risk limit 또는 주문 권한을 변경하지 않는다.
- provider, credential, network, 계좌·주문 endpoint와 broker mutation은 0건이다.
- 기존 `experiment_ledger_store.py`의 대형 모듈, `object` annotation, broad rollback catch는 no-excuse 기존 기술부채다. 새 모델·key·schema에는 no-excuse 위반이 없다.

## 검증

- authority model·SQLite focused: `50 passed`
- 전체 회귀: `2603 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall: 통과
- 새 model/key/schema no-excuse: 위반 0건
- 수동 public Writer/Reader driver: `created=1 replay_created=0 bindings=1 schema=3`

## 다음 단계

authority binding을 lifecycle chain 검증에 연결하고, shadow mode는 `SHADOW_CHAMPION`만, Alpaca Paper mode는 `PAPER_CHAMPION`만 허용하는 상호배타 전이를 추가한다. 자동 승격 정책은 여전히 열지 않는다.
