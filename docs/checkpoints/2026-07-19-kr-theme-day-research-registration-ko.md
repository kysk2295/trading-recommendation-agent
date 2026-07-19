# KR theme day 연구 사전등록 체크포인트

## 범위

`H-KR-THEME-LEADER-VWAP-001` 가설과 `kr_equities/day_trading/theme_leader_vwap_reclaim` lane을 기존 global multi-market experiment ledger에 사전등록한다. 전략 버전은 `kr-theme-leader-vwap-reclaim-v1-code-<code SHA-256 앞 16자>` 형식이며 manifest의 code version과 분리될 수 없다.

기존 `H-KR-THEME-MOMENTUM-001` Opportunity Manager 등록 계약과 replay는 그대로 보존한다. 등록 CLI는 이 두 hypothesis만 허용하고 결과 보고서에 실제 등록 lane과 `shadow` mode를 기록한다.

## 안전 경계

- 등록 행은 hypothesis와 strategy lineage만 append한다.
- day verifier는 exact strategy version, code version, lane, shadow mode와 등록 이후 투영시각을 모두 요구한다.
- 등록만으로 trial, fill, lifecycle, champion, 계좌 binding 또는 주문 권한을 만들지 않는다.
- committed example은 fixture replay용이다. 실제 forward trial은 clean checkpoint commit SHA로 만든 version을 먼저 등록해야 한다.
- provider, credential, KIS/LS/Alpaca, 계좌·잔고·포지션·주문 endpoint는 사용하지 않는다.

## 검증

- 관련 registration CLI 테스트: `7 passed`
- 전체 회귀: `2652 passed`
- Ruff, changed-file format: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, changed-file no-excuse, `git diff --check`: 통과
- actual CLI `--help`: exit `0`, Opportunity/day shadow 설명과 필수 인자 확인
- missing manifest: exit `1`, database 생성 `0`, private blocked report 확인
- day happy/replay: 첫 등록 hypothesis/version `1/1`, 재실행 `0/0`, exact lane과 mode `600` 확인
- 외부 network와 broker mutation: `0`

## 다음 단계

clean checkpoint SHA로 day strategy를 운영 원장에 사전등록한 뒤, current setup·signal을 exact strategy registration에 결합하는 append-only shadow trial과 보수적 fill 계약을 추가한다. trial은 no-entry baseline, 비용·체결가능성·drawdown·안정성·multiple-testing gate를 사전 고정하고 Reviewer 판단 전에는 승격하지 않는다.
