import {
  EMPTY_RECEIPT_ORIGINS,
  renderResearchStrategiesWorkspace,
} from "./research_strategies_workspace";
import type { WorkspaceRenderer } from "./types";

export const renderResearch: WorkspaceRenderer = (snapshot, drawer) =>
  renderResearchStrategiesWorkspace("research", snapshot, drawer, EMPTY_RECEIPT_ORIGINS);
