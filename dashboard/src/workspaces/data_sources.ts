import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderDataSources: WorkspaceRenderer = (snapshot, drawer) =>
  renderWorkspace(workspaceById("data-sources"), snapshot, drawer);
