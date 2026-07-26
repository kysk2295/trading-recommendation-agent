import { requiredElement } from "./dom";
import { resolveWorkspaceHash, WORKSPACES, type WorkspaceDefinition } from "./workspace_registry";

type RouteCallback = (workspace: WorkspaceDefinition) => void;

export function initializeWorkspaceTabs(onRoute: RouteCallback): () => void {
  const links = workspaceLinks();
  const activateHash = (): void => {
    const workspace = resolveWorkspaceHash(window.location.hash);
    if (window.location.hash !== workspace.hash) {
      window.history.replaceState(null, "", workspace.hash);
    }
    activateLinks(links, workspace);
    onRoute(workspace);
  };
  const handleKeydown = (event: KeyboardEvent, index: number): void => {
    const nextIndex = keyboardWorkspaceIndex(event.key, index, links.length);
    if (nextIndex === null) return;
    event.preventDefault();
    const next = links[nextIndex];
    if (next !== undefined) {
      next.focus();
      next.click();
    }
  };
  for (const [index, link] of links.entries()) {
    link.addEventListener("keydown", (event) => handleKeydown(event, index));
  }
  window.addEventListener("hashchange", activateHash);
  window.addEventListener("popstate", activateHash);
  activateHash();
  return activateHash;
}

export function keyboardWorkspaceIndex(
  key: string,
  current: number,
  length: number,
): number | null {
  switch (key) {
    case "Home":
      return 0;
    case "End":
      return length - 1;
    case "ArrowRight":
    case "ArrowDown":
      return (current + 1) % length;
    case "ArrowLeft":
    case "ArrowUp":
      return (current - 1 + length) % length;
    default:
      return null;
  }
}

function workspaceLinks(): readonly HTMLAnchorElement[] {
  return WORKSPACES.map((workspace) => {
    const selector = `[data-workspace-link="${workspace.id}"]`;
    const link = document.querySelector(selector);
    if (!(link instanceof HTMLAnchorElement)) {
      throw new WorkspaceNavigationError(`missing workspace link: ${workspace.id}`);
    }
    return link;
  });
}

function activateLinks(links: readonly HTMLAnchorElement[], workspace: WorkspaceDefinition): void {
  for (const link of links) {
    const selected = link.dataset["workspaceLink"] === workspace.id;
    link.tabIndex = selected ? 0 : -1;
    if (selected) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  }
  requiredElement("launcher-current", HTMLButtonElement).textContent = workspace.label;
}

class WorkspaceNavigationError extends Error {
  override readonly name = "WorkspaceNavigationError";
}
