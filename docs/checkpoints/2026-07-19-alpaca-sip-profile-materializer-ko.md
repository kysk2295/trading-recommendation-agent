# Alpaca SIP profile 자동 materialization 체크포인트

## 완성 범위

- scanner policy preflight와 profile artifact binding을 분리했다.
- preflight는 exact desired set과 현재 완료 정규장 분을 provider 호출 없이 확정한다.
- desired instrument마다 별도 mode-700 historical cache를 소유한다.
- 기존 raw-first 20세션 수집과 canonical replay를 재사용한다.
- 현재 분의 content-addressed profile을 자동 생성해 runtime binding으로 전달한다.
- 수동 `--profile`과 자동 `--auto-profile-root`를 상호배타적으로 제공한다.

## 검증

- 2종목 최초 materialization: historical data GET 40건
- 동일 scope 즉시 재실행: historical data GET 0건
- 자동 CLI 1종목: historical GET 20건 + current GET 1건, M4.4 READY
- profile minute와 runtime completed minute 불일치: 기존 binding gate에서 차단
- account/order endpoint와 mutation: 0건
