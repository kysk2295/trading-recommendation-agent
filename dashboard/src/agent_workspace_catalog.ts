import type { AgentId, Interaction, InteractionMode } from "./schema";

export const agentIds = [
  "opportunity_manager",
  "day_trading",
  "swing_trading",
  "systematic_quant",
  "derivatives_research",
  "market_context",
] as const satisfies readonly AgentId[];

export const agentLabels: Readonly<Record<AgentId, readonly [string, string]>> = {
  opportunity_manager: ["기회 관리자", "OPPORTUNITY MANAGER"],
  day_trading: ["데이 트레이딩", "DAY TRADING"],
  swing_trading: ["스윙 트레이딩", "SWING TRADING"],
  systematic_quant: ["시스템 퀀트", "SYSTEMATIC QUANT"],
  derivatives_research: ["파생상품 연구", "DERIVATIVES RESEARCH"],
  market_context: ["시장 맥락", "MARKET CONTEXT"],
};

export const modes = [
  ["conversation", "대화"],
  ["research", "연구 작업"],
  ["analysis", "분석 작업"],
  ["hypothesis", "가설 등록"],
  ["experiment", "실험 실행"],
  ["allowed_code", "허용 코드 점검"],
] as const satisfies readonly (readonly [InteractionMode, string])[];

export function latestInteraction(
  current: Interaction | undefined,
  incoming: Interaction,
): Interaction {
  return current !== undefined && current.updated_at > incoming.updated_at ? current : incoming;
}
