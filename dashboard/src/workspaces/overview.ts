import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderOverview: WorkspaceRenderer = (snapshot, drawer) =>
  renderWorkspace(workspaceById("overview"), snapshot, drawer);
