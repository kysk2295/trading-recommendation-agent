import { renderCommandCenter } from "./command_center";
import { renderDataSources } from "./data_sources";
import { renderDerivatives } from "./derivatives";
import { renderMarkets } from "./markets";
import { renderOverview } from "./overview";
import { renderPaper } from "./paper";
import { renderResearch } from "./research";
import { renderStrategies } from "./strategies";
import { renderSystem } from "./system";
import type { WorkspaceRenderer } from "./types";

export const WORKSPACE_RENDERERS = {
  command_center: renderCommandCenter,
  overview: renderOverview,
  markets: renderMarkets,
  data_sources: renderDataSources,
  research: renderResearch,
  strategies: renderStrategies,
  derivatives: renderDerivatives,
  paper: renderPaper,
  system: renderSystem,
} satisfies Readonly<Record<string, WorkspaceRenderer>>;
