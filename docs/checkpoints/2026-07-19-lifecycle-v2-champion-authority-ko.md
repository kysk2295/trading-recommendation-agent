# Lifecycle v2 champion authority 체크포인트

## 목적

shadow-only agent를 Paper Champion으로 부르지 않고, 실행 가능한 Alpaca Paper agent와 신호·shadow 전용 agent의 champion 경로를 분리한다.

## 상태 경로

- Shadow: `EXPERIMENTAL_SHADOW → CHALLENGER → SHADOW_CHAMPION`
- Paper: `EXPERIMENTAL_SHADOW → EXPERIMENTAL_PAPER → CHALLENGER → PAPER_CHAMPION`
- 두 champion은 같은 성숙도 rank이며 `SUSPENDED`로만 내려갈 수 있다.
- `SUSPENDED` 복구는 이전에 도달한 rank를 넘지 못하는 기존 규칙을 유지한다.

## 권한 검증

- 신규 champion event는 exact `StrategyAuthorityBinding` key를 `evidence_keys`에 포함해야 한다.
- `AgentOperatingMode.SHADOW`는 `SHADOW_CHAMPION`만, `ALPACA_PAPER`는 `PAPER_CHAMPION`만 허용한다.
- Paper Champion은 lifecycle history에 `EXPERIMENTAL_PAPER`가 있어야 한다. Shadow Champion history에 Paper phase가 있으면 차단한다.
- binding이 없거나 event보다 늦게 bound됐거나 mode/key/path가 다르면 Writer와 Reader 모두 fail-closed한다.
- schema v3 이전의 binding 없는 Paper Champion history는 읽기 호환성을 유지한다. 새 `SHADOW_CHAMPION`은 legacy가 없으므로 binding 없는 행을 변조로 거부한다.

## 운영 연결

- intraday bootstrap은 네 current strategy version 각각에 US day + Alpaca Paper authority를 version과 lifecycle 사이 같은 transaction으로 append한다.
- 기존 v2 version을 보강할 때 `bound_at`은 과거 version 시각이 아니라 현재 보강 요청시각이다. 부분 authority batch는 차단한다.
- swing shadow trial은 US swing + shadow authority를 등록하고 replay·start·finalize마다 exact binding을 다시 확인한다.
- bootstrap report는 hypothesis, version, authority, lifecycle 신규/재사용 수를 각각 출력한다.

## 열지 않은 것

- Lifecycle Controller v1의 자동 promotion은 그대로 차단한다.
- champion 조건을 충족했다는 forward evidence, 동일 위험 비교, DSR/PBO, parameter plateau 또는 SIP 검토는 생성하지 않았다.
- champion 상태 자체가 주문권한·위험한도·allocation을 변경하지 않는다.
- Portfolio Manager는 최소 두 executable Paper champion 전까지 구현하지 않는다.
- provider, credential, network, 계좌·주문 endpoint와 broker mutation은 0건이다.

## 검증

- lifecycle/model/store/controller/bootstrap/swing focused: `126 passed`
- 전체 회귀: `2614 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall: 통과
- 신규 lifecycle authority policy no-excuse: 위반 0건
- bootstrap CLI: help 0, missing source 1, happy 0; 신규 version/authority/lifecycle `4/4/4`
- public Writer/Reader driver: `shadow_created=1`, projected `shadow_champion`, cross-Paper blocked `1`

## 다음 단계

상태 타입과 권한 경계는 열렸지만 자동 승격은 아직 아니다. 다음 구현은 equal-risk terminal trial, broker/shadow 일치, DSR/PBO, parameter plateau와 SIP 검증을 별도 evidence contract로 만들고 독립 Reviewer가 모두 확인한 경우에만 promotion 후보를 내는 것이다.
