import { renderResearchStrategiesWorkspace } from "./research_strategies_workspace";
import type { WorkspaceRenderer } from "./types";

export const renderResearch: WorkspaceRenderer = (snapshot, drawer, context) =>
  renderResearchStrategiesWorkspace("research", snapshot, drawer, context.receipts);
