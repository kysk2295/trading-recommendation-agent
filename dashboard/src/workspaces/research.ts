import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderResearch: WorkspaceRenderer = (snapshot, drawer) =>
  renderWorkspace(workspaceById("research"), snapshot, drawer);
