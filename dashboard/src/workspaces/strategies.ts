import { renderResearchStrategiesWorkspace } from "./research_strategies_workspace";
import type { WorkspaceRenderer } from "./types";

export const renderStrategies: WorkspaceRenderer = (snapshot, drawer, context) =>
  renderResearchStrategiesWorkspace("strategies", snapshot, drawer, context.receipts);
