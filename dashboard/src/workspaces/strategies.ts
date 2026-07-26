import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderStrategies: WorkspaceRenderer = (snapshot, drawer) =>
  renderWorkspace(workspaceById("strategies"), snapshot, drawer);
