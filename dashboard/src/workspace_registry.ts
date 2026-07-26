export const WORKSPACES = [
  {
    id: "command-center",
    key: "command_center",
    hash: "#command-center",
    label: "Command Center",
    kicker: "ACT · COMMAND CENTER",
    description: "대화, 지시형 도구 작업, 자율 연구 증거를 분리해 확인합니다.",
  },
  {
    id: "overview",
    key: "overview",
    hash: "#overview",
    label: "Overview",
    kicker: "ORIENT · OVERVIEW",
    description: "시장, 연구, Paper, 시스템 상태를 중복 없이 요약합니다.",
  },
  {
    id: "markets",
    key: "markets",
    hash: "#markets",
    label: "Markets",
    kicker: "OBSERVE · MARKETS",
    description: "완료된 바와 라이선스가 확인된 시장 문맥만 표시합니다.",
  },
  {
    id: "data-sources",
    key: "data_sources",
    hash: "#data-sources",
    label: "Data Sources",
    kicker: "VERIFY INPUTS · DATA SOURCES",
    description: "공급자별 권한, 신선도, 커버리지와 receipt를 검증합니다.",
  },
  {
    id: "research",
    key: "research",
    hash: "#research",
    label: "Research",
    kicker: "EVALUATE · RESEARCH",
    description: "연구 자료, 가설, causal dataset과 증거 공백을 평가합니다.",
  },
  {
    id: "strategies",
    key: "strategies",
    hash: "#strategies",
    label: "Strategies",
    kicker: "GOVERN · STRATEGIES",
    description: "전략 lane, trial, Reviewer와 lifecycle 결정을 추적합니다.",
  },
  {
    id: "derivatives",
    key: "derivatives",
    hash: "#derivatives",
    label: "Derivatives",
    kicker: "INSPECT · DERIVATIVES",
    description: "권위 있는 옵션·선물 연구 문맥의 가용성을 확인합니다.",
  },
  {
    id: "paper",
    key: "paper",
    hash: "#paper",
    label: "Paper",
    kicker: "RECONCILE · PAPER",
    description: "확정된 Paper 원장과 주문 lifecycle receipt만 조정합니다.",
  },
  {
    id: "system",
    key: "system",
    hash: "#system",
    label: "System",
    kicker: "OPERATE · SYSTEM",
    description: "런타임, stage, 배포와 relay의 typed receipt를 운영합니다.",
  },
] as const;

export type WorkspaceDefinition = (typeof WORKSPACES)[number];
export type WorkspaceId = WorkspaceDefinition["id"];
export type WorkspaceKey = WorkspaceDefinition["key"];

export const DEFAULT_WORKSPACE: WorkspaceDefinition = WORKSPACES[0];

export function resolveWorkspaceHash(hash: string): WorkspaceDefinition {
  return WORKSPACES.find((workspace) => workspace.hash === hash) ?? DEFAULT_WORKSPACE;
}

export function workspaceById(id: WorkspaceId): WorkspaceDefinition {
  return WORKSPACES.find((workspace) => workspace.id === id) ?? DEFAULT_WORKSPACE;
}
