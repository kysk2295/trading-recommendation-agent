import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderPaper: WorkspaceRenderer = (snapshot, drawer) =>
  renderWorkspace(workspaceById("paper"), snapshot, drawer);
