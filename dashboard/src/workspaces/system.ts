import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderSystem: WorkspaceRenderer = (snapshot, drawer) =>
  renderWorkspace(workspaceById("system"), snapshot, drawer);
