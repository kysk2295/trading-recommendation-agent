import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderMarkets: WorkspaceRenderer = (snapshot, drawer) =>
  renderWorkspace(workspaceById("markets"), snapshot, drawer);
