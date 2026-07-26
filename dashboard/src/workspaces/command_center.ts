import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderCommandCenter: WorkspaceRenderer = (snapshot, drawer) =>
  renderWorkspace(workspaceById("command-center"), snapshot, drawer);
