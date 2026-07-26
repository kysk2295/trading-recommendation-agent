import {
  EMPTY_RECEIPT_ORIGINS,
  renderResearchStrategiesWorkspace,
} from "./research_strategies_workspace";
import type { WorkspaceRenderer } from "./types";

export const renderStrategies: WorkspaceRenderer = (snapshot, drawer) =>
  renderResearchStrategiesWorkspace("strategies", snapshot, drawer, EMPTY_RECEIPT_ORIGINS);
