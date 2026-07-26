import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderDerivatives: WorkspaceRenderer = (snapshot, drawer) =>
  renderWorkspace(workspaceById("derivatives"), snapshot, drawer);
