import { requiredElement } from "./dom";

const tabIds = ["overview", "agents", "account", "evidence"] as const;
type TabId = (typeof tabIds)[number];

export function initializeWorkspaceTabs(): void {
  const tabs = tabIds.map((id) => requiredElement(`tab-${id}`, HTMLButtonElement));
  const activate = (id: TabId, updateHash: boolean): void => {
    for (const candidate of tabIds) {
      const selected = candidate === id;
      const tab = requiredElement(`tab-${candidate}`, HTMLButtonElement);
      const panel = requiredElement(`panel-${candidate}`, HTMLElement);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      panel.hidden = !selected;
      if (selected) {
        tab.scrollIntoView({ behavior: "auto", block: "nearest", inline: "center" });
      }
    }
    if (updateHash && window.location.hash !== `#${id}`) {
      window.history.replaceState(null, "", `#${id}`);
    }
  };

  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => activate(tabIds[index] ?? "agents", true));
    tab.addEventListener("keydown", (event) => {
      const nextIndex = keyboardIndex(event.key, index, tabs.length);
      if (nextIndex === null) {
        return;
      }
      event.preventDefault();
      const next = tabs[nextIndex];
      const nextId = tabIds[nextIndex];
      if (next !== undefined && nextId !== undefined) {
        next.focus();
        activate(nextId, true);
      }
    });
  }
  window.addEventListener("hashchange", () => activate(tabFromHash(), false));
  activate(tabFromHash(), false);
}

function tabFromHash(): TabId {
  const candidate = window.location.hash.slice(1);
  return isTabId(candidate) ? candidate : "agents";
}

function isTabId(value: string): value is TabId {
  return tabIds.some((candidate) => candidate === value);
}

function keyboardIndex(key: string, current: number, length: number): number | null {
  if (key === "Home") {
    return 0;
  }
  if (key === "End") {
    return length - 1;
  }
  if (key === "ArrowRight") {
    return (current + 1) % length;
  }
  if (key === "ArrowLeft") {
    return (current - 1 + length) % length;
  }
  return null;
}
